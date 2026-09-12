from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from quantdesk.core.events import IncomingEvent
from quantdesk.core.types import BookLevel
from quantdesk.data.orderbook.sequence import SequenceValidator, sequence_contract
from quantdesk.data.orderbook.validator import levels, validate_book


@dataclass(frozen=True, slots=True)
class BookView:
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    bid_levels: int
    ask_levels: int
    connection_epoch: str
    validity_epoch: int
    available_ns: int
    source_sequence: str | None
    raw_ref: str | None


@dataclass(frozen=True, slots=True)
class BookUpdate:
    state: str
    reason: str
    view: BookView | None
    duplicate: bool = False
    cancel_entries: bool = False
    rebuild_required: bool = False


class BookBuilder:
    def __init__(
        self,
        contract: str | SequenceValidator,
        *,
        depth: int = 20,
        max_levels: int = 20000,
        max_age_ns: int = 500_000_000,
    ) -> None:
        if min(depth, max_levels, max_age_ns) < 1:
            raise ValueError("book bounds must be positive")
        self.validator = sequence_contract(contract) if isinstance(contract, str) else contract
        self.depth, self.max_levels, self.max_age_ns = depth, max_levels, max_age_ns
        self.state = "EMPTY"
        self.view: BookView | None = None
        self._bids: dict[int, int] = {}
        self._asks: dict[int, int] = {}
        self._epoch: str | None = None
        self._scope: tuple[str | None, str] | None = None
        self._sequence: str | None = None
        self._fingerprint: str | None = None
        self._available = 0
        self._validity_epoch = 0

    def invalidate(self, reason: str, *, stale: bool = False) -> BookUpdate:
        self.state = "STALE" if stale else "INVALID"
        self.view = None
        return BookUpdate(self.state, reason, None, cancel_entries=True, rebuild_required=True)

    def begin_sync(self, connection_epoch: str) -> BookUpdate:
        if not connection_epoch:
            raise ValueError("connection epoch required")
        if self._epoch != connection_epoch:
            self._sequence = None
        self._epoch = connection_epoch
        self._fingerprint = None
        self._bids, self._asks = {}, {}
        self._validity_epoch += 1
        self.state, self.view = "SYNCING", None
        return BookUpdate(self.state, "AWAITING_SNAPSHOT", None, rebuild_required=True)

    def check_freshness(self, available_ns: int) -> BookUpdate:
        if self.view is not None and available_ns - self._available > self.max_age_ns:
            return self.invalidate("STALE_FEED", stale=True)
        return BookUpdate(self.state, "", self.view)

    def apply(self, event: IncomingEvent) -> BookUpdate:
        scope = (event.instrument_id, event.source_channel)
        if self._scope is None:
            self._scope = scope
        elif scope != self._scope:
            return self.invalidate("STREAM_SCOPE_MISMATCH")
        if self._epoch is None:
            self.begin_sync(event.connection_epoch)
        if event.connection_epoch != self._epoch:
            return BookUpdate(self.state, "INCOMPATIBLE_EPOCH", self.view)
        if event.event_type in {"DataGap", "ConnectionChanged"}:
            return self.invalidate("FEED_DISCONTINUITY")
        if event.event_type not in {"BookSnapshot", "BookDelta"}:
            return self.invalidate("UNKNOWN_BOOK_MESSAGE")
        if self.state in {"INVALID", "STALE"}:
            return BookUpdate(self.state, "REBUILD_REQUIRED", None, rebuild_required=True)
        try:
            if event.available_ns < self._available:
                raise ValueError("AVAILABILITY_REGRESSION")
            payload = json.loads(event.payload)
            snapshot = event.event_type == "BookSnapshot"
            if not snapshot and self.state != "VALID":
                raise ValueError("SNAPSHOT_REQUIRED")
            sequence = payload["native_sequence"]
            if sequence is not None and not isinstance(sequence, str):
                raise ValueError("INVALID_NATIVE_SEQUENCE")
            fingerprint = sha256(event.payload).hexdigest()
            if fingerprint == self._fingerprint and sequence == self._sequence:
                return BookUpdate(self.state, "DUPLICATE", self.view, duplicate=True)
            reason = self.validator.validate(
                self._sequence, sequence, payload.get("prior_sequence"), snapshot
            )
            if reason:
                raise ValueError(reason)
            if payload.get("checksum") is not None:
                raise ValueError("CHECKSUM_ADAPTER_REQUIRED")
            bids = levels(payload["bids" if snapshot else "bid_updates"])
            asks = levels(payload["asks" if snapshot else "ask_updates"])
            next_bids, next_asks = ({}, {}) if snapshot else (self._bids.copy(), self._asks.copy())
            for updates, side in ((bids, next_bids), (asks, next_asks)):
                for level in updates:
                    if level.size_lots:
                        side[level.price_ticks] = level.size_lots
                    else:
                        side.pop(level.price_ticks, None)
            validate_book(next_bids, next_asks, self.max_levels)
        except (ValueError, KeyError, TypeError) as exc:
            return self.invalidate(str(exc))
        self._bids, self._asks = next_bids, next_asks
        self._sequence, self._fingerprint = sequence, fingerprint
        self._available = event.available_ns
        self.state = "VALID"
        self.view = BookView(
            tuple(
                BookLevel(p, next_bids[p]) for p in sorted(next_bids, reverse=True)[: self.depth]
            ),
            tuple(BookLevel(p, next_asks[p]) for p in sorted(next_asks)[: self.depth]),
            len(next_bids),
            len(next_asks),
            event.connection_epoch,
            self._validity_epoch,
            event.available_ns,
            sequence,
            event.raw_ref,
        )
        return BookUpdate(self.state, "", self.view)
