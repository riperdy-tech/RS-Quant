from decimal import Decimal


def calculate_funding_payment(
    position_qty: Decimal, mark_price: Decimal, funding_rate: Decimal
) -> Decimal:
    """Positive funding rate means longs pay shorts.

    If position_qty is positive (long) and funding_rate is positive,
    payment is negative (deducted). Returns amount to add to account balance.
    """
    return -(position_qty * mark_price * funding_rate)
