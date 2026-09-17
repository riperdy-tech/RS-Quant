from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class FeeProfile:
    maker_rate: Decimal = Decimal("0.0002")  # 2 bps
    taker_rate: Decimal = Decimal("0.00055") # 5.5 bps

def calculate_fee(notional: Decimal, is_maker: bool, profile: FeeProfile) -> Decimal:
    rate = profile.maker_rate if is_maker else profile.taker_rate
    return notional * rate
