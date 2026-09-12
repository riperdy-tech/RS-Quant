from __future__ import annotations

from dataclasses import dataclass
from decimal import MAX_EMAX, MIN_EMIN, Decimal, Inexact, localcontext
from fractions import Fraction
from hashlib import sha256

from quantdesk.core.events import InstrumentSpecUpdated, canonical_bytes


def exact(value: object) -> Decimal:
    if not isinstance(value, (str, Decimal, int)) or isinstance(value, bool):
        raise TypeError("financial values require Decimal, integer, or decimal string")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("financial value must be finite")
    return result


def aligned(value: object, step: Decimal) -> int:
    ratio = Fraction(exact(value)) / Fraction(step)
    if ratio.denominator != 1:
        raise ValueError("value is not aligned to instrument step")
    return ratio.numerator


def exact_product(*values: Decimal) -> Decimal:
    """Coefficient digit counts bound every intermediate product's exact precision."""
    with localcontext() as ctx:
        ctx.prec = max(50, sum(len(value.as_tuple().digits) for value in values))
        ctx.Emax, ctx.Emin = MAX_EMAX, MIN_EMIN
        ctx.traps[Inexact] = True
        result = Decimal(1)
        for value in values:
            result *= value
        return result


@dataclass(frozen=True, slots=True)
class InstrumentSpec(InstrumentSpecUpdated):
    """An immutable metadata observation, identified by its canonical content."""

    @classmethod
    def create(
        cls,
        *,
        instrument_id: str,
        tick_size: Decimal,
        quantity_step: Decimal,
        contract_multiplier: Decimal = Decimal(1),
        valid_from_ns: int = 0,
        known_from_ns: int = 0,
        min_quantity: Decimal | None = None,
        max_quantity: Decimal = Decimal("1000000"),
        min_notional: Decimal = Decimal("0.00000001"),
        trading_status: str = "TRADING",
        min_leverage: Decimal = Decimal(1),
        max_leverage: Decimal = Decimal(1),
        supported_margin_modes: tuple[str, ...] = ("isolated",),
        supported_position_modes: tuple[str, ...] = ("one_way",),
        funding_schedule: tuple[str, ...] = (),
    ) -> InstrumentSpec:
        parts = instrument_id.split(":")
        if len(parts) != 6 or not all(parts) or min(valid_from_ns, known_from_ns) < 0:
            raise ValueError("instrument identity or revision time invalid")
        spec = cls(
            instrument_id,
            valid_from_ns,
            known_from_ns,
            "",
            exact(tick_size),
            exact(quantity_step),
            exact(contract_multiplier),
            parts[2],
            parts[3],
            parts[4],
            exact(min_quantity if min_quantity is not None else quantity_step),
            exact(max_quantity),
            exact(min_notional),
            trading_status,
            exact(min_leverage),
            exact(max_leverage),
            supported_margin_modes,
            supported_position_modes,
            funding_schedule,
            (),
        )
        from dataclasses import replace

        return replace(spec, revision_hash=sha256(canonical_bytes(spec)).hexdigest())

    def price_to_ticks(self, price: object) -> int:
        ticks = aligned(price, self.tick_size)
        if ticks <= 0:
            raise ValueError("price must be positive")
        return ticks

    def quantity_to_lots(self, quantity: object) -> int:
        lots = aligned(quantity, self.quantity_step)
        if lots < 0:
            raise ValueError("quantity must be nonnegative")
        return lots

    def base_quantity(self, signed_lots: int) -> Decimal:
        if type(signed_lots) is not int:
            raise TypeError("lots must be integer")
        return exact_product(Decimal(signed_lots), self.quantity_step, self.contract_multiplier)

    def price(self, ticks: int) -> Decimal:
        if type(ticks) is not int or ticks <= 0:
            raise ValueError("positive integer ticks required")
        return exact_product(Decimal(ticks), self.tick_size)


class InstrumentRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, InstrumentSpec] = {}

    def add(self, spec: InstrumentSpec) -> None:
        from dataclasses import replace

        expected = sha256(canonical_bytes(replace(spec, revision_hash=""))).hexdigest()
        if expected != spec.revision_hash:
            raise ValueError("instrument revision hash mismatch")
        self._specs[spec.revision_hash] = spec

    def at(self, instrument_id: str, event_ns: int, known_ns: int) -> InstrumentSpec:
        candidates = [
            s
            for s in self._specs.values()
            if s.instrument_id == instrument_id
            and s.valid_from_ns <= event_ns
            and s.known_from_ns <= known_ns
        ]
        if not candidates:
            raise LookupError("no instrument specification available at requested time")
        return max(candidates, key=lambda s: (s.valid_from_ns, s.known_from_ns, s.revision_hash))
