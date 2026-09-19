"""Unit tests for MEXC Venue Integration: Auth, Contract Specs, REST Client, and Live Feed."""

from decimal import Decimal
import json
from unittest.mock import MagicMock, patch
import pytest

from quantdesk.venues.mexc.auth import (
    MEXCCredentials,
    generate_mexc_signature,
    signed_headers,
    ws_login_message,
)
from quantdesk.venues.mexc.contract_specs import (
    MEXCContractSpec,
    MEXCContractSpecsRegistry,
    from_mexc_symbol,
    to_mexc_symbol,
)
from quantdesk.venues.mexc.rest import MEXCRestClient
from quantdesk.venues.mexc.live_feed import MEXCLiveFeedService


# ==========================================
# 1. Auth & Signature Tests
# ==========================================

def test_mexc_credentials_validation():
    """Validates credential initialization rules."""
    creds = MEXCCredentials(api_key="mx0vgKey", secret_key="mx0vgSecret")
    assert creds.api_key == "mx0vgKey"
    assert creds.secret_key == "mx0vgSecret"

    with pytest.raises(ValueError, match="Complete MEXC credentials required"):
        MEXCCredentials(api_key="", secret_key="secret")

    with pytest.raises(ValueError, match="newline characters prohibited"):
        MEXCCredentials(api_key="key\n", secret_key="secret")


def test_mexc_signature_generation():
    """Tests deterministic HMAC-SHA256 signature against known inputs."""
    api_key = "test_key"
    secret = "test_secret"
    ts = 1726700000000
    param_str = "symbol=BTC_USDT"

    sig = generate_mexc_signature(secret, api_key, ts, param_str)
    assert isinstance(sig, str)
    assert len(sig) == 64  # SHA256 hex string is 64 characters

    # Re-running with same inputs produces identical signature
    sig2 = generate_mexc_signature(secret, api_key, ts, param_str)
    assert sig == sig2

    # Different timestamp or params produces different signature
    sig3 = generate_mexc_signature(secret, api_key, ts + 1, param_str)
    assert sig != sig3


def test_signed_headers_params_and_body():
    """Tests header generation for GET params and POST JSON bodies."""
    creds = MEXCCredentials(api_key="mexc_api_key", secret_key="mexc_secret")
    ts = 1726700000000

    # Test query params
    headers_get = signed_headers(creds, ts, params={"symbol": "BTC_USDT", "page_num": 1})
    assert headers_get["ApiKey"] == "mexc_api_key"
    assert headers_get["Request-Time"] == str(ts)
    assert "Signature" in headers_get
    assert headers_get["Content-Type"] == "application/json"

    # Test POST body dict
    body = {"symbol": "BTC_USDT", "price": "60000.0", "vol": 10, "type": 2}
    headers_post = signed_headers(creds, ts, body=body)
    assert headers_post["ApiKey"] == "mexc_api_key"
    assert headers_post["Request-Time"] == str(ts)
    assert len(headers_post["Signature"]) == 64

    # Invalid timestamp
    with pytest.raises(ValueError, match="positive integer"):
        signed_headers(creds, 0)


def test_ws_login_message():
    """Tests WebSocket authentication frame construction."""
    creds = MEXCCredentials(api_key="mexc_key", secret_key="mexc_secret")
    ts = 1726700000000
    msg = ws_login_message(creds, ts)
    assert msg["method"] == "login"
    assert msg["param"]["apiKey"] == "mexc_key"
    assert msg["param"]["reqTime"] == str(ts)
    assert len(msg["param"]["signature"]) == 64


# ==========================================
# 2. Contract Specs & Normalization Tests
# ==========================================

def test_symbol_normalization():
    """Tests symbol conversions between MEXC format and internal format."""
    assert to_mexc_symbol("BTCUSDT") == "BTC_USDT"
    assert to_mexc_symbol("ETHUSDT") == "ETH_USDT"
    assert to_mexc_symbol("BTC_USDT") == "BTC_USDT"

    assert from_mexc_symbol("BTC_USDT") == "BTCUSDT"
    assert from_mexc_symbol("ETH_USDT") == "ETHUSDT"


def test_mexc_contract_specs_btc_eth():
    """Validates BTC and ETH contract specs for MEXC USDT Futures."""
    btc_spec = MEXCContractSpecsRegistry.get("BTC_USDT")
    assert btc_spec.symbol == "BTC_USDT"
    assert btc_spec.contract_size == Decimal("0.0001")
    assert btc_spec.price_tick == Decimal("0.1")
    assert btc_spec.maker_fee_rate == Decimal("0.0000")  # 0.00% Maker Fee
    assert btc_spec.min_vol == 1

    eth_spec = MEXCContractSpecsRegistry.get("ETHUSDT")
    assert eth_spec.symbol == "ETH_USDT"
    assert eth_spec.contract_size == Decimal("0.01")
    assert eth_spec.price_tick == Decimal("0.01")
    assert eth_spec.maker_fee_rate == Decimal("0.0000")  # 0.00% Maker Fee
    assert eth_spec.min_vol == 1


def test_mexc_quantize_qty_and_contracts():
    """Tests quantization to integer contract lots."""
    btc_spec = MEXCContractSpecsRegistry.get("BTC_USDT")
    # 0.12345 BTC -> 1234 contracts * 0.0001 = 0.1234 BTC
    assert btc_spec.quantize_qty(Decimal("0.12345")) == Decimal("0.1234")
    assert btc_spec.quantize_contracts(Decimal("0.12345")) == 1234

    # Sub-minimum trade size clamped to 1 contract (0.0001 BTC)
    assert btc_spec.quantize_qty(Decimal("0.00001")) == Decimal("0.0001")
    assert btc_spec.quantize_contracts(Decimal("0.00001")) == 1

    eth_spec = MEXCContractSpecsRegistry.get("ETH_USDT")
    # 3.456 ETH -> 345 contracts * 0.01 = 3.45 ETH
    assert eth_spec.quantize_qty(Decimal("3.456")) == Decimal("3.45")
    assert eth_spec.quantize_contracts(Decimal("3.456")) == 345


def test_mexc_quantize_price():
    """Tests price quantization for BTC (0.1) and ETH (0.01)."""
    btc_spec = MEXCContractSpecsRegistry.get("BTC_USDT")
    assert btc_spec.quantize_price(Decimal("63450.123")) == Decimal("63450.1")
    assert btc_spec.quantize_price(Decimal("63450.16")) == Decimal("63450.2")

    eth_spec = MEXCContractSpecsRegistry.get("ETH_USDT")
    assert eth_spec.quantize_price(Decimal("2650.456")) == Decimal("2650.46")


def test_mexc_order_validation():
    """Tests validation of order quantities and prices."""
    btc_spec = MEXCContractSpecsRegistry.get("BTC_USDT")
    valid, msg = btc_spec.validate_order(Decimal("0.01"), Decimal("60000"))
    assert valid is True
    assert msg == ""

    # Negative price
    valid, msg = btc_spec.validate_order(Decimal("0.01"), Decimal("-100"))
    assert valid is False
    assert "positive" in msg


# ==========================================
# 3. REST Client Tests
# ==========================================

def test_mexc_rest_client_test_connection():
    """Tests test_connection parsing assets from MEXC response."""
    creds = MEXCCredentials(api_key="k", secret_key="s")
    client = MEXCRestClient(credentials=creds)

    mock_resp = {
        "success": True,
        "code": 0,
        "data": [
            {"currency": "USDT", "equity": 10000.50, "availableBalance": 9500.25},
            {"currency": "BTC", "equity": 0.0, "availableBalance": 0.0},
        ],
    }

    with patch.object(client, "_request", return_value=mock_resp):
        res = client.test_connection()
        assert res["success"] is True
        assert res["total_equity"] == "10000.50"
        assert res["available_usdt"] == "9500.25"
        assert res["assets_count"] == 2


def test_mexc_rest_client_submit_post_only_order():
    """Tests that submit_post_only_order constructs exact Post-Only Maker payload."""
    creds = MEXCCredentials(api_key="k", secret_key="s")
    client = MEXCRestClient(credentials=creds)

    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = {"success": True, "code": 0, "data": {"orderId": "12345"}}
        res = client.submit_post_only_order(
            symbol="BTCUSDT",
            side=1,  # Open Long
            price=Decimal("62000.5"),
            vol_contracts=20,
            client_order_id="qd_order_001",
            leverage=3,
        )
        assert res["success"] is True
        mock_req.assert_called_once_with(
            "POST",
            "/api/v1/private/order/submit",
            body={
                "symbol": "BTC_USDT",
                "price": "62000.5",
                "vol": 20,
                "side": 1,
                "type": 2,      # MUST BE 2: Post-Only Maker Only
                "openType": 1,  # MUST BE 1: Isolated Margin
                "leverage": 3,
                "externalOid": "qd_order_001",
            },
        )


def test_mexc_rest_client_cancel_order():
    """Tests cancel_order body parameters."""
    creds = MEXCCredentials(api_key="k", secret_key="s")
    client = MEXCRestClient(credentials=creds)

    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = {"success": True, "code": 0}
        client.cancel_order(order_id="ord_999", symbol="BTCUSDT")
        mock_req.assert_called_once_with(
            "POST",
            "/api/v1/private/order/cancel",
            body={"orderId": "ord_999", "symbol": "BTC_USDT"},
        )


# ==========================================
# 4. Live Feed Message Handler Tests
# ==========================================

def test_mexc_live_feed_depth_and_trades():
    """Tests WebSocket message parsing for push.depth and push.deal."""
    service = MEXCLiveFeedService(symbols=("BTCUSDT", "ETHUSDT"))

    # Test push.depth
    depth_msg = {
        "channel": "push.depth",
        "symbol": "BTC_USDT",
        "ts": 1726700000100,
        "data": {
            "bids": [[64000.0, 150, 1], [63999.0, 200, 2]],
            "asks": [[64001.0, 100, 1], [64002.0, 300, 3]],
        },
    }

    service._handle_message(depth_msg)
    book = service.order_books["BTCUSDT"]
    assert len(book["bids"]) == 2
    assert book["bids"][0] == ["64000.0", "150"]
    assert len(book["asks"]) == 2
    assert book["asks"][0] == ["64001.0", "100"]

    # Test push.deal
    deal_msg = {
        "channel": "push.deal",
        "symbol": "BTC_USDT",
        "data": [
            {"p": 64000.5, "v": 10, "T": 1, "t": 1726700000200},  # BUY
            {"p": 64000.0, "v": 5, "T": 2, "t": 1726700000250},   # SELL
        ],
    }

    service._handle_message(deal_msg)
    trades = list(service.recent_trades["BTCUSDT"])
    assert len(trades) == 2
    assert trades[0]["side"] == "SELL"  # prepended (most recent first)
    assert trades[1]["side"] == "BUY"
