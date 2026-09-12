from dataclasses import dataclass
from typing import Protocol


class SequenceValidator(Protocol):
    @property
    def name(self) -> str: ...

    def validate(
        self, previous: str | None, current: str | None, prior: str | None, snapshot: bool
    ) -> str | None: ...


@dataclass(frozen=True, slots=True)
class NumericSequence:
    name: str
    contiguous: bool = False

    def validate(
        self, previous: str | None, current: str | None, prior: str | None, snapshot: bool
    ) -> str | None:
        try:
            if current is None or int(current) < 0:
                return "MISSING_SEQUENCE"
            if previous is not None:
                if int(current) <= int(previous):
                    return "OUT_OF_ORDER_SEQUENCE"
                if not snapshot and self.contiguous and int(current) != int(previous) + 1:
                    return "SEQUENCE_GAP"
                if not snapshot and prior is not None and prior != previous:
                    return "PRIOR_SEQUENCE_MISMATCH"
        except ValueError:
            return "MALFORMED_SEQUENCE"
        return None


@dataclass(frozen=True, slots=True)
class SnapshotSequence:
    name: str = "recorded_snapshot"

    def validate(
        self, previous: str | None, current: str | None, prior: str | None, snapshot: bool
    ) -> str | None:
        return None if snapshot else "SNAPSHOT_FEED_REJECTS_DELTA"


CONTRACTS: dict[str, SequenceValidator] = {
    "contiguous_fixture": NumericSequence("contiguous_fixture", True),
    "recorded_monotonic": NumericSequence("recorded_monotonic"),
    "recorded_snapshot": SnapshotSequence(),
}


def sequence_contract(name: str) -> SequenceValidator:
    if name not in CONTRACTS:
        raise ValueError("unknown sequence contract; an explicit adapter is required")
    return CONTRACTS[name]
