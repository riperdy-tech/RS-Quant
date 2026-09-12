from dataclasses import dataclass
from decimal import Decimal, localcontext

from quantdesk.portfolio.arithmetic import ACCOUNTING_CONTEXT, ZERO, money
from quantdesk.venues.instruments import InstrumentSpec


@dataclass(frozen=True, slots=True)
class Position:
    instrument_id: str
    signed_lots: int = 0
    base_quantity: Decimal = ZERO
    average_entry: Decimal | None = None
    spec_revision: str = ""


@dataclass(frozen=True, slots=True)
class PositionLeg:
    kind: str
    signed_lots: int
    price: Decimal
    realized_gross: Decimal


def apply_fill(
    prior: Position, signed_lots: int, price: Decimal, spec: InstrumentSpec
) -> tuple[Position, Decimal, tuple[PositionLeg, ...]]:
    """Externally observed reversals are decomposed into a close and a new open."""
    if type(signed_lots) is not int or signed_lots == 0 or money(price) <= 0:
        raise ValueError("fill requires nonzero integer lots and a positive price")
    delta = spec.base_quantity(signed_lots)
    quantity = spec.base_quantity(prior.signed_lots + signed_lots)
    with localcontext(ACCOUNTING_CONTEXT):
        legs: tuple[PositionLeg, ...]
        realized = ZERO
        average = prior.average_entry
        if prior.signed_lots == 0:
            average = price
            legs = (PositionLeg("OPEN", signed_lots, price, ZERO),)
        elif (prior.signed_lots > 0) == (signed_lots > 0):
            assert average is not None
            average = (abs(prior.base_quantity) * average + abs(delta) * price) / abs(quantity)
            legs = (PositionLeg("OPEN", signed_lots, price, ZERO),)
        else:
            assert average is not None
            closed_lots = min(abs(prior.signed_lots), abs(signed_lots))
            sign = 1 if prior.signed_lots > 0 else -1
            realized = spec.base_quantity(closed_lots) * (price - average) * sign
            legs = (PositionLeg("CLOSE", -sign * closed_lots, price, realized),)
            if quantity == 0:
                average = None
            elif (quantity > 0) != (prior.base_quantity > 0):
                average = price
                legs += (PositionLeg("OPEN", prior.signed_lots + signed_lots, price, ZERO),)
    return (
        Position(
            spec.instrument_id,
            prior.signed_lots + signed_lots,
            quantity,
            average,
            spec.revision_hash,
        ),
        realized,
        legs,
    )
