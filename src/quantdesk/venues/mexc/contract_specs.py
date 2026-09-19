"""Official MEXC USDT-Futures Contract Specifications and Precision Registry.

Provides exact quantization for order quantities (contractSize, volUnit, minVol)
and prices (priceUnit) adhering strictly to MEXC Contract V1 exchange rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
import json
import logging
from typing import Any
import urllib.request

logger = logging.getLogger("quantdesk.venues.mexc.contract_specs")


def to_mexc_symbol(sym: str) -> str:
    """Normalizes symbol to MEXC format (e.g. BTCUSDT -> BTC_USDT)."""
    clean = sym.strip().upper()
    if "_" in clean:
        return clean
    if clean.endswith("USDT"):
        return f"{clean[:-4]}_USDT"
    return clean


def from_mexc_symbol(sym: str) -> str:
    """Normalizes MEXC symbol back to standard internal format (e.g. BTC_USDT -> BTCUSDT)."""
    return sym.replace("_", "").upper().strip()


@dataclass(frozen=True)
class MEXCContractSpec:
    """Official MEXC USDT-Futures contract specifications."""
    symbol: str               # "BTC_USDT", "ETH_USDT"
    base_coin: str            # "BTC", "ETH"
    quote_coin: str           # "USDT"
    margin_coin: str          # "USDT"
    contract_size: Decimal    # Decimal("0.0001") for BTC, Decimal("0.01") for ETH
    min_vol: int              # 1 contract
    max_vol: int              # 400,000 for BTC, 70,000 for ETH
    price_tick: Decimal       # Decimal("0.1") for BTC, Decimal("0.01") for ETH
    maker_fee_rate: Decimal   # Decimal("0.0000") (0.00% maker fee on MEXC)
    taker_fee_rate: Decimal   # Decimal("0.0004") (0.04% taker fee)

    def quantize_qty(self, raw_qty: Decimal | float | str) -> Decimal:
        """Quantizes raw units to integer contract lots using ROUND_DOWN."""
        d = Decimal(str(raw_qty))
        # Number of contracts = raw_qty / contract_size
        contracts = int(d // self.contract_size)
        contracts = max(self.min_vol, min(contracts, self.max_vol))
        return Decimal(contracts) * self.contract_size

    def quantize_contracts(self, raw_qty: Decimal | float | str) -> int:
        """Returns the number of integer contracts (vol parameter for MEXC submit)."""
        d = Decimal(str(raw_qty))
        contracts = int(d // self.contract_size)
        return max(self.min_vol, min(contracts, self.max_vol))

    def quantize_price(self, raw_price: Decimal | float | str) -> Decimal:
        """Quantizes price to MEXC priceUnit tick size."""
        d = Decimal(str(raw_price))
        step = self.price_tick
        return d.quantize(step, rounding=ROUND_HALF_UP)

    def validate_order(self, qty_units: Decimal, price: Decimal) -> tuple[bool, str]:
        """Validates contract quantity and price bounds."""
        contracts = int(qty_units // self.contract_size)
        if contracts < self.min_vol:
            return False, f"Order quantity {contracts} contracts below MEXC minVol {self.min_vol}"
        if contracts > self.max_vol:
            return False, f"Order quantity {contracts} contracts exceeds MEXC maxVol {self.max_vol}"
        if price <= Decimal("0"):
            return False, "Price must be positive"
        return True, ""


class MEXCContractSpecsRegistry:
    """Registry maintaining verified contract specifications for MEXC Futures."""

    _specs: dict[str, MEXCContractSpec] = {
        "BTC_USDT": MEXCContractSpec(
            symbol="BTC_USDT",
            base_coin="BTC",
            quote_coin="USDT",
            margin_coin="USDT",
            contract_size=Decimal("0.0001"),
            min_vol=1,
            max_vol=400000,
            price_tick=Decimal("0.1"),
            maker_fee_rate=Decimal("0.0000"),
            taker_fee_rate=Decimal("0.0004"),
        ),
        "ETH_USDT": MEXCContractSpec(
            symbol="ETH_USDT",
            base_coin="ETH",
            quote_coin="USDT",
            margin_coin="USDT",
            contract_size=Decimal("0.01"),
            min_vol=1,
            max_vol=70000,
            price_tick=Decimal("0.01"),
            maker_fee_rate=Decimal("0.0000"),
            taker_fee_rate=Decimal("0.0001"),
        ),
    }

    @classmethod
    def get(cls, symbol: str) -> MEXCContractSpec:
        mexc_sym = to_mexc_symbol(symbol)
        spec = cls._specs.get(mexc_sym)
        if not spec:
            raise KeyError(f"Unsupported MEXC contract symbol: {symbol}")
        return spec

    @classmethod
    def fetch_online_specs(cls) -> bool:
        """Refreshes contract specifications live from MEXC API."""
        url = "https://contract.mexc.com/api/v1/contract/detail"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "QuantDesk/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            items = raw.get("data", [])
            for item in items:
                sym = item.get("symbol")
                if sym in cls._specs:
                    cls._specs[sym] = MEXCContractSpec(
                        symbol=sym,
                        base_coin=sym.split("_")[0],
                        quote_coin="USDT",
                        margin_coin="USDT",
                        contract_size=Decimal(str(item.get("contractSize", "0.0001"))),
                        min_vol=int(item.get("minVol", 1)),
                        max_vol=int(item.get("maxVol", 100000)),
                        price_tick=Decimal(str(item.get("priceUnit", "0.1"))),
                        maker_fee_rate=Decimal(str(item.get("makerFeeRate", "0.0"))),
                        taker_fee_rate=Decimal(str(item.get("takerFeeRate", "0.0002"))),
                    )
            logger.info("Refreshed official MEXC contract specifications from API.")
            return True
        except Exception as e:
            logger.warning(f"Could not refresh MEXC specs from API (using verified defaults): {e}")
            return False
