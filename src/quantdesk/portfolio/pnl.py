from decimal import Decimal, localcontext

from quantdesk.portfolio.arithmetic import ACCOUNTING_CONTEXT, money


def funding_cash_change(quantity: Decimal, settlement_mark: Decimal, rate: Decimal) -> Decimal:
    """Synthetic linear funding: a positive rate charges longs and credits shorts."""
    money(quantity)
    money(rate)
    if money(settlement_mark) <= 0:
        raise ValueError("positive settlement mark required")
    with localcontext(ACCOUNTING_CONTEXT):
        return -quantity * settlement_mark * rate
