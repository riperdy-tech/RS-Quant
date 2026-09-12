"""Native amounts are preserved exactly; derived divisions use precision 50."""

from decimal import MAX_EMAX, MIN_EMIN, ROUND_HALF_EVEN, Context, Decimal, localcontext

ACCOUNTING_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)
ZERO = Decimal(0)


def money(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError("financial values must be Decimal")
    if not value.is_finite():
        raise ValueError("financial values must be finite")
    return value


def exact_sum(*values: Decimal) -> Decimal:
    if not values:
        return ZERO
    for value in values:
        money(value)
    least = min(int(value.as_tuple().exponent) for value in values)
    greatest = max(value.adjusted() for value in values)
    with localcontext(ACCOUNTING_CONTEXT) as ctx:
        ctx.prec = max(50, greatest - least + len(str(len(values))) + 2)
        ctx.Emax, ctx.Emin = MAX_EMAX, MIN_EMIN
        return sum(values, ZERO)


def display_amount(value: Decimal, unit: Decimal = Decimal("0.01")) -> str:
    money(value)
    if money(unit) <= 0 or unit.as_tuple().digits != (1,):
        raise ValueError("display unit must be a positive power of ten")
    with localcontext(ACCOUNTING_CONTEXT) as ctx:
        ctx.prec = max(50, value.adjusted() - int(unit.as_tuple().exponent) + 2)
        return format(value.quantize(unit), "f")
