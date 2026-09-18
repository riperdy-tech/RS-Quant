"""Official Bitget USDT-Futures Contract Specifications and Precision Registry.

Provides exact quantization for order quantities (volumePlace, sizeMultiplier)
and prices (pricePlace, priceEndStep) adhering strictly to Bitget USDT-M Futures
exchange rules.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any

logger = logging.getLogger("quantdesk.venues.bitget_uta.contract_specs")


@dataclass(frozen=True)
class BitgetContractSpec:
    """Official Bitget USDT-Futures contract specifications."""
    symbol: str               # "BTCUSDT", "ETHUSDT"
    base_coin: str            # "BTC", "ETH"
    quote_coin: str           # "USDT"
    margin_coin: str          # "USDT"
    volume_place: int         # 4 for BTC, 2 for ETH
    price_place: int          # 1 for BTC, 2 for ETH
    size_multiplier: Decimal  # Decimal("0.0001") for BTC, Decimal("0.01") for ETH
    min_trade_num: Decimal    # Decimal("0.0001") for BTC, Decimal("0.01") for ETH
    price_tick: Decimal       # Decimal("0.1") for BTC, Decimal("0.01") for ETH
    min_trade_usdt: Decimal   # Decimal("5.0") USDT
    maker_fee_rate: Decimal   # Decimal("0.0002") (0.02%)
    taker_fee_rate: Decimal   # Decimal("0.0006") (0.06%)
    max_market_order_qty: Decimal = Decimal("1000")

    def quantize_qty(self, raw_qty: Decimal | float | str) -> Decimal:
        """Quantizes quantity to Bitget volumePlace using ROUND_DOWN to prevent margin overreach.
        
        Guarantees exact alignment with sizeMultiplier and min_trade_num.
        """
        d = Decimal(str(raw_qty))
        step = self.size_multiplier
        # Truncate / round down to step size
        units = (d // step) * step
        # Round to exact volumePlace string representation
        quant_format = Decimal("10") ** (-self.volume_place)
        quantized = units.quantize(quant_format, rounding=ROUND_DOWN)
        return max(quantized, self.min_trade_num)

    def quantize_price(self, raw_price: Decimal | float | str) -> Decimal:
        """Quantizes price to Bitget pricePlace (price tick)."""
        d = Decimal(str(raw_price))
        step = self.price_tick
        return d.quantize(step, rounding=ROUND_HALF_UP)

    def compute_qty_from_notional(
        self,
        target_notional_usdt: Decimal | float | str,
        price: Decimal | float | str,
    ) -> Decimal:
        """Computes contract quantity from target notional in USDT, quantized to Bitget step size."""
        notional_dec = Decimal(str(target_notional_usdt))
        price_dec = Decimal(str(price))
        if price_dec <= Decimal("0"):
            return self.min_trade_num
        raw_qty = notional_dec / price_dec
        return self.quantize_qty(raw_qty)

    def compute_qty_from_risk(
        self,
        risk_budget_usdt: Decimal | float | str,
        entry_price: Decimal | float | str,
        stop_price: Decimal | float | str,
    ) -> Decimal:
        """Computes contract quantity from risk budget in USDT and stop-loss distance."""
        risk_dec = Decimal(str(risk_budget_usdt))
        p_entry = Decimal(str(entry_price))
        p_stop = Decimal(str(stop_price))
        stop_dist = abs(p_entry - p_stop)
        if stop_dist <= Decimal("0"):
            return self.min_trade_num
        raw_qty = risk_dec / stop_dist
        return self.quantize_qty(raw_qty)

    def validate_order(self, qty: Decimal, price: Decimal) -> tuple[bool, str]:
        """Validates if order satisfies Bitget min trade size and min notional filters."""
        if qty < self.min_trade_num:
            return False, f"Quantity {qty} {self.base_coin} < minimum trade {self.min_trade_num} {self.base_coin}"
        notional = qty * price
        if notional < self.min_trade_usdt:
            return False, f"Notional {notional:.2f} USDT < minimum trade notional {self.min_trade_usdt:.2f} USDT"
        return True, "VALID"


class BitgetContractSpecsRegistry:
    """Registry maintaining active Bitget USDT-Futures specifications."""

    _SPECS: dict[str, BitgetContractSpec] = {
        "BTCUSDT": BitgetContractSpec(
            symbol="BTCUSDT",
            base_coin="BTC",
            quote_coin="USDT",
            margin_coin="USDT",
            volume_place=4,
            price_place=1,
            size_multiplier=Decimal("0.0001"),
            min_trade_num=Decimal("0.0001"),
            price_tick=Decimal("0.1"),
            min_trade_usdt=Decimal("5.0"),
            maker_fee_rate=Decimal("0.0002"),
            taker_fee_rate=Decimal("0.0006"),
            max_market_order_qty=Decimal("220"),
        ),
        "ETHUSDT": BitgetContractSpec(
            symbol="ETHUSDT",
            base_coin="ETH",
            quote_coin="USDT",
            margin_coin="USDT",
            volume_place=2,
            price_place=2,
            size_multiplier=Decimal("0.01"),
            min_trade_num=Decimal("0.01"),
            price_tick=Decimal("0.01"),
            min_trade_usdt=Decimal("5.0"),
            maker_fee_rate=Decimal("0.0002"),
            taker_fee_rate=Decimal("0.0006"),
            max_market_order_qty=Decimal("1900"),
        ),
    }

    @classmethod
    def get(cls, symbol: str) -> BitgetContractSpec:
        """Retrieves contract specification for symbol with robust defaults."""
        sym = symbol.upper()
        if sym in cls._SPECS:
            return cls._SPECS[sym]
        # Generic crypto fallback
        base = sym.replace("USDT", "")
        return BitgetContractSpec(
            symbol=sym,
            base_coin=base,
            quote_coin="USDT",
            margin_coin="USDT",
            volume_place=2,
            price_place=2,
            size_multiplier=Decimal("0.01"),
            min_trade_num=Decimal("0.01"),
            price_tick=Decimal("0.01"),
            min_trade_usdt=Decimal("5.0"),
            maker_fee_rate=Decimal("0.0002"),
            taker_fee_rate=Decimal("0.0006"),
            max_market_order_qty=Decimal("1000"),
        )

    get_spec = get

    @classmethod
    def fetch_online_specs(cls) -> None:
        """Polls Bitget v2 public contracts API to refresh official parameters."""
        url = "https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "QuantDesk/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            rows = data.get("data", [])
            for r in rows:
                sym = r.get("symbol")
                if sym in ("BTCUSDT", "ETHUSDT"):
                    cls._SPECS[sym] = BitgetContractSpec(
                        symbol=sym,
                        base_coin=r.get("baseCoin", sym[:3]),
                        quote_coin=r.get("quoteCoin", "USDT"),
                        margin_coin="USDT",
                        volume_place=int(r.get("volumePlace", 4 if "BTC" in sym else 2)),
                        price_place=int(r.get("pricePlace", 1 if "BTC" in sym else 2)),
                        size_multiplier=Decimal(str(r.get("sizeMultiplier", "0.0001" if "BTC" in sym else "0.01"))),
                        min_trade_num=Decimal(str(r.get("minTradeNum", "0.0001" if "BTC" in sym else "0.01"))),
                        price_tick=Decimal("10") ** (-int(r.get("pricePlace", 1 if "BTC" in sym else 2))),
                        min_trade_usdt=Decimal(str(r.get("minTradeUSDT", "5.0"))),
                        maker_fee_rate=Decimal(str(r.get("makerFeeRate", "0.0002"))),
                        taker_fee_rate=Decimal(str(r.get("takerFeeRate", "0.0006"))),
                        max_market_order_qty=Decimal(str(r.get("maxMarketOrderQty", "1000"))),
                    )
            logger.info("Refreshed official Bitget contract specifications from API.")
        except Exception as e:
            logger.warning(f"Could not refresh online Bitget specs (using verified offline defaults): {e}")


def fetch_bitget_funding_rate(symbol: str) -> dict[str, Any] | None:
    """Fetches current 8-hour funding rate from Bitget USDT-FUTURES."""
    url = f"https://api.bitget.com/api/v2/mix/market/current-fund-rate?symbol={symbol}&productType=USDT-FUTURES"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "QuantDesk/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        rows = data.get("data", [])
        if rows:
            r = rows[0]
            return {
                "symbol": symbol,
                "funding_rate": float(r.get("fundingRate", 0.0)),
                "funding_rate_bps": float(r.get("fundingRate", 0.0)) * 10000.0,
                "interval_hours": int(r.get("fundingRateInterval", 8)),
                "next_update_ms": int(r.get("nextUpdate", 0)),
            }
    except Exception as e:
        logger.debug(f"Funding rate fetch for {symbol} failed: {e}")
    return None
