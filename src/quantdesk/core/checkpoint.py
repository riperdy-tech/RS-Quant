"""Immutable complete causal state and explicit, hash-verified JSON checkpoints.

Component sections hold versioned canonical bytes owned by their real domain
reducers. The engine never pickles or introspects mutable component objects.
Physical raw locators remain checkpoint evidence, outside causal state hashes.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, fields, replace
from hashlib import sha256
from typing import Any, ClassVar, cast

from quantdesk.core.clock import ScheduledTimer
from quantdesk.core.events import canonical_bytes
from quantdesk.persistence.manifests import DurableWatermark

EMPTY_HASH = sha256(b"").hexdigest()
type Section = tuple[tuple[str, bytes], ...]


@dataclass(frozen=True, slots=True)
class HashScopes:
    deterministic_state_hash: str
    economic_state_hash: str
    operational_hash: str
    audit_hash: str


@dataclass(frozen=True, slots=True)
class RNGStream:
    """Version-1 SHA-256 counter stream; each subsystem owns a distinct name.

    Rejection sampling gives an unbiased integer range without global RNG state.
    This is for reproducible simulation, not generation of credentials.
    """

    name: str
    seed: str
    counter: int = 0

    def __post_init__(self) -> None:
        if not self.name or not self.seed or type(self.counter) is not int or self.counter < 0:
            raise ValueError("invalid RNG stream")

    def draw(self, upper: int) -> tuple[int, RNGStream]:
        if type(upper) is not int or not 1 <= upper <= 2**256:
            raise ValueError("RNG bound must be in 1..2**256")
        counter = self.counter
        limit = 2**256 - (2**256 % upper)
        while True:
            digest = sha256(canonical_bytes(("rng-v1", self.name, self.seed, counter))).digest()
            counter += 1
            number = int.from_bytes(digest, "big")
            if number < limit:
                return number % upper, replace(self, counter=counter)


@dataclass(frozen=True, slots=True)
class SourceCursor:
    channel: str
    connection_epoch: str
    source_message_id: str | None
    source_sequence: str | None
    input_ordinal: int


@dataclass(frozen=True, slots=True)
class EngineState:
    logical_run_id: str
    account_scope: tuple[str | None, str, str | None] | None = None
    engine_seq: int = 0
    state_version: int = 0
    input_ordinal: int = 0
    last_input_id: str | None = None
    available_ns: int = 0
    next_scheduled_ordinal: int = 0
    timers: tuple[ScheduledTimer, ...] = ()
    source_cursors: tuple[SourceCursor, ...] = ()
    rng_streams: tuple[RNGStream, ...] = ()
    ownership_epoch: int = 0
    risk_epoch: int = 0
    config_hash: str = "unconfigured"
    model_hashes: tuple[tuple[str, str], ...] = ()
    books: Section = ()
    features: Section = ()
    strategies: Section = ()
    oms: Section = ()
    ledger: Section = ()
    dedup_keys: Section = ()
    reservations: Section = ()
    risk_latches: Section = ()
    protection: Section = ()
    instrument_specs: Section = ()
    clock_anchors: Section = ()
    simulator: Section = ()
    audit_hash: str = EMPTY_HASH
    operational_hash: str = EMPTY_HASH
    derived_history_hash: str = EMPTY_HASH

    _COMPONENTS: ClassVar[tuple[str, ...]] = (
        "books",
        "features",
        "strategies",
        "oms",
        "ledger",
        "dedup_keys",
        "reservations",
        "risk_latches",
        "protection",
        "instrument_specs",
        "clock_anchors",
        "simulator",
    )

    def __post_init__(self) -> None:
        if not self.logical_run_id or not self.config_hash:
            raise ValueError("state run/config identity is required")
        if self.account_scope is not None and (
            type(self.account_scope) is not tuple
            or len(self.account_scope) != 3
            or any(value is not None and not isinstance(value, str) for value in self.account_scope)
            or not self.account_scope[1]
        ):
            raise ValueError("account scope must be immutable")
        if any(
            type(row) is not tuple
            or len(row) != 2
            or any(not isinstance(value, str) or not value for value in row)
            for row in self.model_hashes
        ):
            raise ValueError("model hashes must be immutable pairs")
        for name in (
            "engine_seq",
            "state_version",
            "input_ordinal",
            "available_ns",
            "next_scheduled_ordinal",
            "ownership_epoch",
            "risk_epoch",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"invalid state {name}")
        for name in self._COMPONENTS:
            section = getattr(self, name)
            if (
                type(section) is not tuple
                or any(
                    type(row) is not tuple
                    or len(row) != 2
                    or not isinstance(row[0], str)
                    or not row[0]
                    or not isinstance(row[1], bytes)
                    for row in section
                )
                or section != tuple(sorted(section))
                or len(dict(section)) != len(section)
            ):
                raise ValueError(f"state section {name} must contain sorted immutable unique bytes")
        for name, rows, keys in (
            ("RNG", self.rng_streams, tuple(r.name for r in self.rng_streams)),
            ("model", self.model_hashes, tuple(r[0] for r in self.model_hashes)),
            (
                "cursor",
                self.source_cursors,
                tuple((r.channel, r.connection_epoch) for r in self.source_cursors),
            ),
        ):
            if (
                type(rows) is not tuple
                or keys != tuple(sorted(keys))
                or len(set(keys)) != len(keys)
            ):
                raise ValueError(f"{name} state must be sorted and unique")
        if type(self.timers) is not tuple or self.timers != tuple(sorted(self.timers)):
            raise ValueError("timers must be an immutable sorted queue")
        if len({timer.timer_id for timer in self.timers}) != len(self.timers):
            raise ValueError("duplicate timer identity")

    @classmethod
    def initial(cls, logical_run_id: str) -> EngineState:
        return cls(logical_run_id)

    @classmethod
    def component_names(cls) -> tuple[str, ...]:
        return cls._COMPONENTS

    def get(self, section: str, key: str, default: bytes = b"null") -> bytes:
        if section not in self._COMPONENTS:
            raise ValueError("unknown causal state section")
        return dict(cast(Section, getattr(self, section))).get(key, default)

    def put(self, section: str, key: str, value: bytes) -> EngineState:
        if section not in self._COMPONENTS:
            raise ValueError("unknown causal state section")
        content = dict(cast(Section, getattr(self, section)))
        content[key] = value
        # Dynamic field selection is confined to the validated section allowlist.
        changes: dict[str, Any] = {section: tuple(sorted(content.items()))}
        return replace(self, **changes)

    def next_random(self, name: str, upper: int) -> tuple[int, EngineState]:
        streams = {row.name: row for row in self.rng_streams}
        if name not in streams:
            raise ValueError("RNG stream must be explicitly seeded")
        number, streams[name] = streams[name].draw(upper)
        return number, replace(self, rng_streams=tuple(streams[key] for key in sorted(streams)))

    def hashes(self) -> HashScopes:
        causal = {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if field.name not in {"audit_hash", "operational_hash", "derived_history_hash"}
        }
        economic = {name: getattr(self, name) for name in ("ledger", "dedup_keys", "reservations")}
        return HashScopes(
            sha256(canonical_bytes(causal)).hexdigest(),
            sha256(canonical_bytes(economic)).hexdigest(),
            self.operational_hash,
            self.audit_hash,
        )

    def to_bytes(self) -> bytes:
        return canonical_bytes(self)

    @classmethod
    def from_bytes(cls, data: bytes) -> EngineState:
        value = json.loads(data)
        if set(value) != {field.name for field in fields(cls)}:
            raise ValueError("incompatible causal state schema")
        for name in (
            "engine_seq",
            "state_version",
            "input_ordinal",
            "available_ns",
            "next_scheduled_ordinal",
            "ownership_epoch",
            "risk_epoch",
        ):
            value[name] = int(value[name])
        for name in cls._COMPONENTS:
            value[name] = tuple(
                (key, base64.b64decode(blob["$bytes"], validate=True)) for key, blob in value[name]
            )
        value["account_scope"] = (
            tuple(value["account_scope"]) if value["account_scope"] is not None else None
        )
        value["model_hashes"] = tuple(tuple(row) for row in value["model_hashes"])
        value["rng_streams"] = tuple(
            RNGStream(row["name"], row["seed"], int(row["counter"])) for row in value["rng_streams"]
        )
        value["source_cursors"] = tuple(
            SourceCursor(
                row["channel"],
                row["connection_epoch"],
                row["source_message_id"],
                row["source_sequence"],
                int(row["input_ordinal"]),
            )
            for row in value["source_cursors"]
        )
        value["timers"] = tuple(
            ScheduledTimer(
                int(row["due_ns"]),
                int(row["scheduled_ordinal"]),
                row["timer_id"],
                row["scheduled_by_event_id"],
                row["instrument_id"],
                row["correlation_id"],
            )
            for row in value["timers"]
        )
        return cls(**value)


@dataclass(frozen=True, slots=True)
class Checkpoint:
    state: EngineState
    code_hash: str
    schema_hash: str
    raw_watermark: DurableWatermark
    snapshot_hash: str
    format_version: int = 1

    @property
    def engine_seq(self) -> int:
        return self.state.engine_seq

    def _content(self) -> dict[str, object]:
        return {
            "state": self.state,
            "code_hash": self.code_hash,
            "schema_hash": self.schema_hash,
            "raw_watermark": self.raw_watermark,
            "format_version": self.format_version,
        }

    @classmethod
    def create(
        cls, state: EngineState, code_hash: str, schema_hash: str, raw_watermark: DurableWatermark
    ) -> Checkpoint:
        checkpoint = cls(state, code_hash, schema_hash, raw_watermark, "")
        return replace(
            checkpoint, snapshot_hash=sha256(canonical_bytes(checkpoint._content())).hexdigest()
        )

    def verify(self, code_hash: str, schema_hash: str) -> None:
        if (
            self.format_version != 1
            or not code_hash
            or not schema_hash
            or self.code_hash != code_hash
            or self.schema_hash != schema_hash
        ):
            raise ValueError("checkpoint code/schema compatibility mismatch")
        if sha256(canonical_bytes(self._content())).hexdigest() != self.snapshot_hash:
            raise ValueError("checkpoint snapshot hash mismatch")

    def to_bytes(self) -> bytes:
        return canonical_bytes({**self._content(), "snapshot_hash": self.snapshot_hash})

    @classmethod
    def from_bytes(cls, data: bytes) -> Checkpoint:
        value = json.loads(data)
        if set(value) != {field.name for field in fields(cls)}:
            raise ValueError("incompatible checkpoint schema")
        result = cls(
            EngineState.from_bytes(canonical_bytes(value["state"])),
            value["code_hash"],
            value["schema_hash"],
            DurableWatermark.from_bytes(canonical_bytes(value["raw_watermark"])),
            value["snapshot_hash"],
            int(value["format_version"]),
        )
        result.verify(result.code_hash, result.schema_hash)
        return result
