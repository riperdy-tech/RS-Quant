"""REST Client for MEXC Contract V1 (Futures) API."""

from __future__ import annotations

from decimal import Decimal
import logging
import time
from typing import Any
import httpx

from quantdesk.venues.mexc.auth import MEXCCredentials, signed_headers
from quantdesk.venues.mexc.contract_specs import to_mexc_symbol

logger = logging.getLogger("quantdesk.venues.mexc.rest")

BASE_URL = "https://contract.mexc.com"


class MEXCRestClient:
    """Synchronous & async REST client for MEXC USDT-M perpetual futures."""

    def __init__(
        self,
        credentials: MEXCCredentials | None = None,
        base_url: str = BASE_URL,
        timeout: float = 8.0,
    ) -> None:
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        is_private: bool = True,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {"Content-Type": "application/json"}

        if is_private:
            if not self.credentials:
                raise ValueError("Credentials required for private MEXC endpoints")
            ts = int(time.time() * 1000)
            headers.update(signed_headers(self.credentials, ts, params=params, body=body))

        with httpx.Client(timeout=self.timeout) as client:
            if method.upper() == "GET":
                resp = client.get(url, params=params, headers=headers)
            elif method.upper() == "POST":
                resp = client.post(url, json=body, headers=headers)
            else:
                resp = client.request(method, url, params=params, json=body, headers=headers)

            resp.raise_for_status()
            data = resp.json()

        # MEXC Contract API uses success=True or code=0 / code=200
        if data.get("success") is False or (data.get("code") not in (0, 200, "0", "200", None)):
            msg = data.get("message") or data.get("msg") or "MEXC API error"
            raise RuntimeError(f"MEXC API call {path} failed: {msg} (code={data.get('code')})")

        return data

    def test_connection(self) -> dict[str, Any]:
        """Preflight verification of MEXC credentials and account accessibility."""
        data = self._request("GET", "/api/v1/private/account/assets")
        assets = data.get("data", [])
        total_equity = Decimal("0.00")
        available_usdt = Decimal("0.00")
        for a in assets:
            if a.get("currency") == "USDT":
                total_equity = Decimal(str(a.get("equity", "0.00")))
                available_usdt = Decimal(str(a.get("availableBalance", "0.00")))
        return {
            "success": True,
            "message": "MEXC Futures API connected successfully. 0.00% Maker Fee active.",
            "total_equity": f"{total_equity:.2f}",
            "available_usdt": f"{available_usdt:.2f}",
            "assets_count": len(assets),
        }

    def get_assets(self) -> list[dict[str, Any]]:
        """Returns account collateral and balance breakdown."""
        data = self._request("GET", "/api/v1/private/account/assets")
        return data.get("data", [])

    def get_open_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Returns active open positions across instruments."""
        params = {"symbol": to_mexc_symbol(symbol)} if symbol else None
        data = self._request("GET", "/api/v1/private/position/open_positions", params=params)
        return data.get("data", [])

    def submit_post_only_order(
        self,
        symbol: str,
        side: int,  # 1: Open Long, 2: Close Short, 3: Open Short, 4: Close Long
        price: Decimal | float | str,
        vol_contracts: int,
        client_order_id: str | None = None,
        leverage: int = 3,
    ) -> dict[str, Any]:
        """Submits a Post-Only Maker order (type=2) on MEXC Futures with 0.00% maker fee.
        
        MEXC automatically rejects/cancels the order if it would cross the spread (become taker),
        guaranteeing strict 0% maker execution.
        """
        body: dict[str, Any] = {
            "symbol": to_mexc_symbol(symbol),
            "price": str(price),
            "vol": vol_contracts,
            "side": side,
            "type": 2,      # 2 = Post-Only Maker Only
            "openType": 1,  # 1 = Isolated Margin
            "leverage": leverage,
        }
        if client_order_id:
            body["externalOid"] = client_order_id

        return self._request("POST", "/api/v1/private/order/submit", body=body)

    def cancel_order(self, order_id: str, symbol: str | None = None) -> dict[str, Any]:
        """Cancels an active order on MEXC Futures."""
        body: dict[str, Any] = {"orderId": order_id}
        if symbol:
            body["symbol"] = to_mexc_symbol(symbol)
        return self._request("POST", "/api/v1/private/order/cancel", body=body)
