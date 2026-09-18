"""Unit tests for Bitget UTA Contract Specifications, Precision, and Quantization Rules."""

from decimal import Decimal
from unittest.mock import patch

import pytest

from quantdesk.venues.bitget_uta.contract_specs import (
    BitgetContractSpec,
    BitgetContractSpecsRegistry,
    fetch_bitget_funding_rate,
)


def test_btc_contract_spec_parameters():
    """Validates BTCUSDT official Bitget specifications."""
    spec = BitgetContractSpecsRegistry.get("BTCUSDT")
    assert spec.symbol == "BTCUSDT"
    assert spec.base_coin == "BTC"
    assert spec.quote_coin == "USDT"
    assert spec.margin_coin == "USDT"
    assert spec.volume_place == 4
    assert spec.price_place == 1
    assert spec.size_multiplier == Decimal("0.0001")
    assert spec.min_trade_num == Decimal("0.0001")
    assert spec.price_tick == Decimal("0.1")
    assert spec.min_trade_usdt == Decimal("5.0")
    assert spec.maker_fee_rate == Decimal("0.0002")
    assert spec.taker_fee_rate == Decimal("0.0006")


def test_eth_contract_spec_parameters():
    """Validates ETHUSDT official Bitget specifications."""
    spec = BitgetContractSpecsRegistry.get("ETHUSDT")
    assert spec.symbol == "ETHUSDT"
    assert spec.base_coin == "ETH"
    assert spec.quote_coin == "USDT"
    assert spec.margin_coin == "USDT"
    assert spec.volume_place == 2
    assert spec.price_place == 2
    assert spec.size_multiplier == Decimal("0.01")
    assert spec.min_trade_num == Decimal("0.01")
    assert spec.price_tick == Decimal("0.01")
    assert spec.min_trade_usdt == Decimal("5.0")
    assert spec.maker_fee_rate == Decimal("0.0002")
    assert spec.taker_fee_rate == Decimal("0.0006")


def test_btc_quantize_qty():
    """Tests BTC quantity quantization strictly to 4 decimals with ROUND_DOWN."""
    spec = BitgetContractSpecsRegistry.get("BTCUSDT")
    # Exact step
    assert spec.quantize_qty(Decimal("0.1960")) == Decimal("0.1960")
    # Extra decimal places truncated down to avoid margin overshoot
    assert spec.quantize_qty(Decimal("0.196085")) == Decimal("0.1960")
    assert spec.quantize_qty(Decimal("0.196011")) == Decimal("0.1960")
    assert spec.quantize_qty(Decimal("0.196099")) == Decimal("0.1960")
    # Sub-minimum trade size clamped to min_trade_num
    assert spec.quantize_qty(Decimal("0.00001")) == Decimal("0.0001")


def test_eth_quantize_qty():
    """Tests ETH quantity quantization strictly to 2 decimals with ROUND_DOWN."""
    spec = BitgetContractSpecsRegistry.get("ETHUSDT")
    # Exact step
    assert spec.quantize_qty(Decimal("6.12")) == Decimal("6.12")
    # Extra decimals truncated down
    assert spec.quantize_qty(Decimal("6.1299")) == Decimal("6.12")
    assert spec.quantize_qty(Decimal("6.1201")) == Decimal("6.12")
    # Sub-minimum trade size clamped to min_trade_num
    assert spec.quantize_qty(Decimal("0.001")) == Decimal("0.01")


def test_quantize_price():
    """Tests price tick quantization for BTC and ETH."""
    btc_spec = BitgetContractSpecsRegistry.get("BTCUSDT")
    eth_spec = BitgetContractSpecsRegistry.get("ETHUSDT")

    # BTC price tick is 0.1 USDT
    assert btc_spec.quantize_price(Decimal("76543.21")) == Decimal("76543.2")
    assert btc_spec.quantize_price(Decimal("76543.26")) == Decimal("76543.3")

    # ETH price tick is 0.01 USDT
    assert eth_spec.quantize_price(Decimal("2450.123")) == Decimal("2450.12")
    assert eth_spec.quantize_price(Decimal("2450.127")) == Decimal("2450.13")


def test_compute_qty_from_notional():
    """Tests contract quantity calculation from target USDT notional."""
    btc_spec = BitgetContractSpecsRegistry.get("BTCUSDT")
    eth_spec = BitgetContractSpecsRegistry.get("ETHUSDT")

    # 15,000 USDT notional at 76,500 USDT price
    btc_qty = btc_spec.compute_qty_from_notional(15000, 76500)
    assert btc_qty == Decimal("0.1960")
    assert btc_qty * Decimal("76500") <= Decimal("15000")  # Safe margin invariant

    # 15,000 USDT notional at 2,450 USDT price
    eth_qty = eth_spec.compute_qty_from_notional(15000, 2450)
    assert eth_qty == Decimal("6.12")
    assert eth_qty * Decimal("2450") <= Decimal("15000")


def test_compute_qty_from_risk():
    """Tests contract quantity calculation from risk budget and stop-loss distance."""
    btc_spec = BitgetContractSpecsRegistry.get("BTCUSDT")
    # 50 USDT risk budget with 500 USDT stop distance -> 0.1000 BTC
    qty = btc_spec.compute_qty_from_risk(
        risk_budget_usdt=50,
        entry_price=76000,
        stop_price=75500,
    )
    assert qty == Decimal("0.1000")


def test_order_validation():
    """Tests order validation against Bitget minimum trade size and notional limits."""
    btc_spec = BitgetContractSpecsRegistry.get("BTCUSDT")

    # Valid order: 0.1 BTC at 70,000 USDT (7,000 USDT notional)
    valid, msg = btc_spec.validate_order(Decimal("0.1000"), Decimal("70000"))
    assert valid is True
    assert msg == "VALID"

    # Reject below min_trade_num
    valid, msg = btc_spec.validate_order(Decimal("0.00005"), Decimal("70000"))
    assert valid is False
    assert "minimum trade" in msg

    # Reject below min_trade_usdt (5 USDT)
    valid, msg = btc_spec.validate_order(Decimal("0.0001"), Decimal("40000"))
    # 0.0001 * 40,000 = 4.0 USDT < 5.0 USDT
    assert valid is False
    assert "minimum trade notional" in msg


def test_registry_fallback():
    """Tests registry handling of unknown symbols."""
    spec = BitgetContractSpecsRegistry.get("SOLUSDT")
    assert spec.symbol == "SOLUSDT"
    assert spec.base_coin == "SOL"
    assert spec.quote_coin == "USDT"
    assert spec.volume_place == 2
    assert spec.min_trade_num == Decimal("0.01")


def test_fetch_funding_rate_fallback():
    """Tests fetch_bitget_funding_rate graceful fallback on network failure."""
    with patch("urllib.request.urlopen", side_effect=Exception("Network error")):
        fr = fetch_bitget_funding_rate("BTCUSDT")
        assert fr is None  # Fails closed on network error


def test_dynamic_capital_and_leverage_config():
    """Tests dynamic capital configuration, per-leg margin calculation, and sizing."""
    from quantdesk.strategies.live_runner import LiveStrategyRunner

    runner = LiveStrategyRunner(symbols=["BTCUSDT", "ETHUSDT"])

    # 1. Default configuration: 10,000 USDT capital, 3.0x leverage
    cfg = runner.get_capital_config()
    assert cfg["capital_usdt"] == "10000.00"
    assert cfg["leverage"] == "3.0"
    assert cfg["margin_per_leg"] == "5000.00"
    assert cfg["notional_per_leg"] == "15000.00"

    # 2. Update to 1,000 USDT capital with 3.0x leverage
    res = runner.update_capital_config(capital_usdt=1000, leverage=3.0)
    assert res["capital_usdt"] == "1000.00"
    assert res["leverage"] == "3.0"
    assert res["margin_per_leg"] == "500.00"
    assert res["notional_per_leg"] == "1500.00"
    assert runner.unified_engines["BTCUSDT"].target_notional == Decimal("1500.00")
    assert runner.unified_engines["ETHUSDT"].target_notional == Decimal("1500.00")

    # 3. Verify quantized units at 1,500 USDT notional
    btc_spec = BitgetContractSpecsRegistry.get("BTCUSDT")
    eth_spec = BitgetContractSpecsRegistry.get("ETHUSDT")
    btc_units = btc_spec.compute_qty_from_notional(1500, 77000)
    eth_units = eth_spec.compute_qty_from_notional(1500, 2460)
    # 1500 / 77000 = 0.01948... -> 0.0194 BTC (volumePlace=4)
    assert btc_units == Decimal("0.0194")
    # 1500 / 2460 = 0.6097... -> 0.60 ETH (volumePlace=2)
    assert eth_units == Decimal("0.60")

    # 4. Update to 5,000 USDT capital with 5.0x leverage
    res = runner.update_capital_config(capital_usdt=5000, leverage=5.0)
    assert res["capital_usdt"] == "5000.00"
    assert res["leverage"] == "5.0"
    assert res["margin_per_leg"] == "2500.00"
    assert res["notional_per_leg"] == "12500.00"
    assert runner.unified_engines["BTCUSDT"].target_notional == Decimal("12500.00")

