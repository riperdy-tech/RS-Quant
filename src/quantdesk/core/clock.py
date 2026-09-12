from __future__ import annotations

import base64
import heapq
import json
import time
from dataclasses import dataclass, fields
from enum import IntEnum
from typing import Protocol

from quantdesk.core.events import ClockAdjusted, IncomingEvent, canonical_bytes


class Clock(Protocol):
    def now_ns(self) -> int: ...


class SystemClock:
    def now_ns(self) -> int:
        return time.time_ns()


class VirtualClock:
    def __init__(self, initial_ns: int = 0) -> None:
        if initial_ns < 0:
            raise ValueError("initial time must be non-negative")
        self._now_ns = initial_ns

    def now_ns(self) -> int:
        return self._now_ns

    def advance(self, delta_ns: int) -> None:
        if delta_ns < 0:
            raise ValueError("virtual clock cannot move backward")
        self._now_ns += delta_ns

    def advance_to(self, target_ns: int) -> None:
        if target_ns < self._now_ns:
            raise ValueError("virtual clock cannot move backward")
        self._now_ns = target_ns


@dataclass(frozen=True, slots=True)
class TimerRequest:
    delay_ns: int

    def __post_init__(self) -> None:
        if type(self.delay_ns) is not int or self.delay_ns < 0:
            raise ValueError("timer delay must be a nonnegative integer")


@dataclass(frozen=True, slots=True, order=True)
class ScheduledTimer:
    due_ns: int
    scheduled_ordinal: int
    timer_id: str
    scheduled_by_event_id: str
    instrument_id: str | None
    correlation_id: str


class SourceRank(IntEnum):
    """Reconstructed ties only. Actual receipts must retain their ingress order."""

    MARKET_ACCOUNT = 10
    MARKET_EXECUTION = 20
    ORDER_ARRIVAL = 30
    CANCEL_ARRIVAL = 40
    STRATEGY_TIMER = 50


def decode_incoming(data: object) -> IncomingEvent:
    if not isinstance(data, dict):
        raise ValueError("invalid incoming event snapshot")
    values = dict(data)
    for field in fields(IncomingEvent):
        if (field.name.endswith("_ns") or field.name == "schema_version") and values.get(
            field.name
        ) is not None:
            values[field.name] = int(values[field.name])
    values["payload"] = base64.b64decode(values["payload"]["$bytes"], validate=True)
    return IncomingEvent(**values)


class DeterministicScheduler:
    """A serial boundary scheduler; future entries are never given to a reducer.

    Key: availability, declared source rank, source ordinal, scheduled ordinal.
    An entry scheduled while handling a cause cannot precede that cause, even
    when its rank is earlier. The current dispatch watermark is serialized too.
    """

    def __init__(self) -> None:
        self._queue: list[tuple[int, int, int, int, IncomingEvent]] = []
        self._next_ordinal = 0
        self._last_available_ns = 0

    @staticmethod
    def rank_table() -> tuple[tuple[str, int], ...]:
        return tuple((rank.name, int(rank)) for rank in SourceRank)

    def schedule(
        self,
        event: IncomingEvent,
        rank: SourceRank,
        source_ordinal: int,
        *,
        caused_at_ns: int | None = None,
    ) -> None:
        if type(event) is not IncomingEvent:
            raise TypeError("scheduler requires an unsequenced IncomingEvent")
        if source_ordinal < 0 or event.available_ns < self._last_available_ns:
            raise ValueError("scheduler availability/ordinal regressed")
        if caused_at_ns is not None and event.available_ns < caused_at_ns:
            raise ValueError("scheduled event cannot precede its cause")
        heapq.heappush(
            self._queue,
            (
                event.available_ns,
                int(SourceRank(rank)),
                source_ordinal,
                self._next_ordinal,
                event,
            ),
        )
        self._next_ordinal += 1

    def pop(self) -> IncomingEvent:
        _, _, _, _, event = heapq.heappop(self._queue)
        self._last_available_ns = event.available_ns
        return event

    def __len__(self) -> int:
        return len(self._queue)

    def to_bytes(self) -> bytes:
        return canonical_bytes(
            {
                "version": 1,
                "rank_table": self.rank_table(),
                "queue": sorted(self._queue),
                "next_ordinal": self._next_ordinal,
                "last_available_ns": self._last_available_ns,
            }
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> DeterministicScheduler:
        value = json.loads(data)
        if value["version"] != 1 or value["rank_table"] != [list(r) for r in cls.rank_table()]:
            raise ValueError("incompatible scheduler schema/rank table")
        result = cls()
        result._next_ordinal = int(value["next_ordinal"])
        result._last_available_ns = int(value["last_available_ns"])
        for available, rank, source, ordinal, encoded in value["queue"]:
            event = decode_incoming(encoded)
            key = (int(available), int(SourceRank(int(rank))), int(source), int(ordinal))
            if (
                event.available_ns != key[0]
                or key[0] < result._last_available_ns
                or not 0 <= key[3] < result._next_ordinal
                or key[2] < 0
            ):
                raise ValueError("invalid scheduler snapshot")
            heapq.heappush(result._queue, (*key, event))
        if len({row[3] for row in result._queue}) != len(result._queue):
            raise ValueError("duplicate scheduled ordinal")
        return result


class AvailabilityClock:
    """Ingress wall/monotonic anchor with explicit observations, never core I/O.

    The caller journals every adjustment before handing its next input to the
    engine. Reboots require a new anchor; absolute monotonic values from distinct
    boot epochs are never compared. Wall-clock regression cannot lower availability.
    """

    def __init__(self, wall_ns: int, monotonic_ns: int, boot_epoch: str, available_ns: int):
        if min(wall_ns, monotonic_ns, available_ns) < 0 or not boot_epoch:
            raise ValueError("invalid clock anchor")
        self._wall_ns = wall_ns
        self._monotonic_ns = monotonic_ns
        self._boot_epoch = boot_epoch
        self._anchor_available_ns = available_ns
        self._last_monotonic_ns = monotonic_ns
        self._available_ns = available_ns
        self._adjustments: tuple[ClockAdjusted, ...] = ()

    @classmethod
    def anchor(
        cls, *, wall_ns: int, monotonic_ns: int, boot_epoch: str, persisted_available_ns: int = 0
    ) -> AvailabilityClock:
        result = cls(wall_ns, monotonic_ns, boot_epoch, max(wall_ns, persisted_available_ns))
        result._adjustments = (
            ClockAdjusted(
                persisted_available_ns,
                result.now_ns(),
                f"ANCHOR:{boot_epoch}:{wall_ns}:{monotonic_ns}",
            ),
        )
        return result

    def now_ns(self) -> int:
        return self._available_ns

    @property
    def adjustments(self) -> tuple[ClockAdjusted, ...]:
        return self._adjustments

    def observe(self, *, wall_ns: int, monotonic_ns: int, boot_epoch: str) -> int:
        if boot_epoch != self._boot_epoch:
            raise ValueError("new boot epoch requires an explicit anchor")
        if monotonic_ns < self._last_monotonic_ns or wall_ns < 0:
            raise ValueError("clock observation regressed")
        self._last_monotonic_ns = monotonic_ns
        self._available_ns = self._anchor_available_ns + monotonic_ns - self._monotonic_ns
        return self._available_ns

    def reanchor(self, *, wall_ns: int, monotonic_ns: int, boot_epoch: str) -> None:
        prior = self.now_ns()
        replacement = self.anchor(
            wall_ns=wall_ns,
            monotonic_ns=monotonic_ns,
            boot_epoch=boot_epoch,
            persisted_available_ns=prior,
        )
        adjustments = (*self._adjustments, *replacement.adjustments)
        self.__dict__.update(replacement.__dict__)
        self._adjustments = adjustments

    def to_bytes(self) -> bytes:
        return canonical_bytes({"version": 1, **self.__dict__})

    @classmethod
    def from_bytes(cls, data: bytes) -> AvailabilityClock:
        value = json.loads(data)
        if value.pop("version") != 1:
            raise ValueError("incompatible clock schema")
        result = cls(
            int(value["_wall_ns"]),
            int(value["_monotonic_ns"]),
            value["_boot_epoch"],
            int(value["_anchor_available_ns"]),
        )
        result._last_monotonic_ns = int(value["_last_monotonic_ns"])
        result._available_ns = int(value["_available_ns"])
        result._adjustments = tuple(
            ClockAdjusted(
                int(row["prior_available_ns"]), int(row["next_available_ns"]), row["reason"]
            )
            for row in value["_adjustments"]
        )
        if result._available_ns != (
            result._anchor_available_ns + result._last_monotonic_ns - result._monotonic_ns
        ):
            raise ValueError("invalid clock snapshot")
        return result
