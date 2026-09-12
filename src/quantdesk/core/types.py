from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

type CanonicalScalar = bool | int | float | str | Decimal | None
type PayloadField = tuple[str, CanonicalScalar]


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class EventPayload:
    """Immutable extensible payload used until an owning subsystem adds a richer schema."""

    fields: tuple[PayloadField, ...] = ()
