"""Live WebSocket market data feed from MEXC Contract V1 public streams."""

from __future__ import annotations

import asyncio
from collections import deque
import contextlib
import json
import logging
import time
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from quantdesk.api.routes.events import event_hub
from quantdesk.venues.mexc.contract_specs import from_mexc_symbol, to_mexc_symbol

logger = logging.getLogger("quantdesk.mexc_feed")

MEXC_WS_URL = "wss://contract.mexc.com/edge"


class MEXCLiveFeedService:
    """Manages persistent public WebSocket connection to MEXC and streams live market depth."""

    def __init__(
        self,
        symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT"),
        ws_url: str = MEXC_WS_URL,
    ) -> None:
        self.symbols = symbols
        self.ws_url = ws_url
        self.is_running = False
        self.is_connected = False
        self._task: asyncio.Task[None] | None = None
        self._last_publish_ns: dict[str, int] = {}

        # In-memory live cache
        self.tickers: dict[str, dict[str, Any]] = {}
        self.order_books: dict[str, dict[str, list[list[str]]]] = {
            s: {"bids": [], "asks": []} for s in symbols
        }
        self.recent_trades: dict[str, deque[dict[str, Any]]] = {
            s: deque(maxlen=50) for s in symbols
        }

    async def start(self) -> None:
        """Starts the background feed task if not already running."""
        if self.is_running:
            return
        self.is_running = True
        self._task = asyncio.create_task(self._run_loop(), name="mexc-live-feed")
        logger.info("MEXCLiveFeedService started.")

    async def stop(self) -> None:
        """Stops the background feed task cleanly."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.is_connected = False
        logger.info("MEXCLiveFeedService stopped.")

    async def _run_loop(self) -> None:
        backoff = 1.0
        while self.is_running:
            try:
                logger.info(f"Connecting to MEXC public WebSocket: {self.ws_url}")
                async with connect(
                    self.ws_url,
                    ping_interval=None,
                    max_size=4 * 1024 * 1024,
                    open_timeout=10,
                ) as ws:
                    self.is_connected = True
                    backoff = 1.0
                    logger.info("Connected to MEXC live WebSocket.")

                    # Subscribe to sub.depth.full, sub.ticker, sub.deal, sub.funding.rate, sub.kline
                    for sym in self.symbols:
                        mexc_sym = to_mexc_symbol(sym)
                        await ws.send(json.dumps({"method": "sub.depth.full", "param": {"symbol": mexc_sym, "limit": 20}}))
                        await ws.send(json.dumps({"method": "sub.ticker", "param": {"symbol": mexc_sym}}))
                        await ws.send(json.dumps({"method": "sub.deal", "param": {"symbol": mexc_sym}}))
                        await ws.send(json.dumps({"method": "sub.funding.rate", "param": {"symbol": mexc_sym}}))
                        await ws.send(json.dumps({"method": "sub.kline", "param": {"symbol": mexc_sym, "interval": "Min1"}}))

                    # Ping keepalive task (MEXC heartbeat requires ping frame every 15s)
                    ping_task = asyncio.create_task(self._ping_loop(ws))

                    try:
                        async for raw_msg in ws:
                            if not self.is_running:
                                break
                            try:
                                msg = json.loads(raw_msg)
                                self._handle_message(msg)
                            except Exception as e:
                                logger.debug(f"Error decoding MEXC WS frame: {e}")
                    finally:
                        ping_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await ping_task

            except (ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                self.is_connected = False
                logger.warning(f"MEXC WebSocket disconnected ({e}). Reconnecting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)
            except Exception as e:
                self.is_connected = False
                logger.error(f"Unexpected error in MEXC WebSocket loop: {e}. Retrying in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)

    async def _ping_loop(self, ws: Any) -> None:
        """Sends periodic ping messages to keep MEXC WebSocket connection alive."""
        while self.is_running:
            await asyncio.sleep(15)
            try:
                await ws.send(json.dumps({"method": "ping"}))
            except Exception:
                break

    def _handle_message(self, msg: dict[str, Any]) -> None:
        channel = msg.get("channel")
        if not channel or channel == "pong":
            return

        raw_sym = msg.get("symbol") or msg.get("data", {}).get("symbol")
        if not raw_sym:
            return

        inst_id = from_mexc_symbol(str(raw_sym))
        if inst_id not in self.symbols:
            return

        now_ns = time.time_ns()
        from quantdesk.strategies.live_runner import autonomous_live_engine

        if channel in ("push.depth", "push.depth.full"):
            data = msg.get("data", {})
            bids_raw = data.get("bids", [])
            asks_raw = data.get("asks", [])

            # Strictly sort: bids DESCENDING (highest price first), asks ASCENDING (lowest price first)
            sorted_bids = sorted(bids_raw, key=lambda x: float(x[0]), reverse=True)
            sorted_asks = sorted(asks_raw, key=lambda x: float(x[0]), reverse=False)

            # Format to [[price, qty], ...]
            formatted_bids = [[str(b[0]), str(b[1])] for b in sorted_bids[:15]]
            formatted_asks = [[str(a[0]), str(a[1])] for a in sorted_asks[:15]]

            self.order_books[inst_id] = {"bids": formatted_bids, "asks": formatted_asks}

            ts_ms = msg.get("ts", int(time.time() * 1000))
            autonomous_live_engine.process_book_update(
                inst_id, formatted_bids, formatted_asks, ts_ms
            )

            # Update synthetic ticker from best bid/ask
            if formatted_bids and formatted_asks:
                best_bid = float(formatted_bids[0][0])
                best_ask = float(formatted_asks[0][0])
                mid = (best_bid + best_ask) / 2.0
                autonomous_live_engine.process_ticker_update(
                    inst_id, str(mid), str(mid)
                )

            self._throttle_publish(
                f"depth:{inst_id}",
                topic="market_delta",
                payload={"type": "DEPTH", "data": {"symbol": inst_id, "bids": formatted_bids, "asks": formatted_asks}},
                min_interval_ms=100,
            )

        elif channel == "push.ticker":
            data = msg.get("data", {})
            bid1 = data.get("bid1")
            ask1 = data.get("ask1")
            last = data.get("lastPrice")
            if bid1 is not None and ask1 is not None:
                autonomous_live_engine.process_ticker_update(inst_id, str(bid1), str(ask1))
            self.tickers[inst_id] = {
                "symbol": inst_id,
                "last_price": str(last or 0),
                "bid_price": str(bid1 or 0),
                "ask_price": str(ask1 or 0),
                "mark_price": str(data.get("fairPrice") or last or 0),
                "funding_rate": str(data.get("fundingRate") or 0),
                "change_24h": str(data.get("riseFallRate") or 0),
                "volume_24h": str(data.get("volume24") or 0),
            }

        elif channel == "push.deal":
            deals = msg.get("data", [])
            if isinstance(deals, dict):
                deals = [deals]
            for d in deals:
                trade_record = {
                    "symbol": inst_id,
                    "trade_id": str(d.get("t", int(time.time() * 1000))),
                    "price": str(d.get("p", "0")),
                    "size": str(d.get("v", "0")),
                    "side": "BUY" if str(d.get("T")) == "1" else "SELL",
                    "ts_ms": d.get("t", int(time.time() * 1000)),
                }
                self.recent_trades[inst_id].appendleft(trade_record)
                autonomous_live_engine.process_trade_update(
                    inst_id,
                    trade_record["price"],
                    trade_record["size"],
                    trade_record["side"],
                    trade_record.get("ts_ms"),
                )

            self._throttle_publish(
                f"trades:{inst_id}",
                topic="market_delta",
                payload={"type": "TRADES", "data": {"symbol": inst_id, "trades": list(self.recent_trades[inst_id])[:20]}},
                min_interval_ms=250,
            )

        elif channel == "push.funding.rate":
            data = msg.get("data", {})
            rate = float(data.get("fundingRate", 0.0))
            autonomous_live_engine.latest_funding_rates[inst_id] = {
                "funding_rate_bps": rate * 10000.0,
                "funding_time": data.get("nextSettleTime"),
            }

    def _throttle_publish(
        self,
        key: str,
        topic: str,
        payload: dict[str, Any],
        min_interval_ms: int = 250,
    ) -> None:
        now_ns = time.time_ns()
        last_ns = self._last_publish_ns.get(key, 0)
        if (now_ns - last_ns) >= (min_interval_ms * 1_000_000):
            self._last_publish_ns[key] = now_ns
            event_hub.publish(
                topic=topic,
                resource_version=str(now_ns),
                projection_watermark=now_ns,
                payload=payload,
            )

    def get_ticker(self, symbol: str = "BTCUSDT") -> dict[str, Any] | None:
        return self.tickers.get(symbol)

    def get_order_book(self, symbol: str = "BTCUSDT") -> dict[str, list[list[str]]]:
        return self.order_books.get(symbol, {"bids": [], "asks": []})

    def get_trades(self, symbol: str = "BTCUSDT") -> list[dict[str, Any]]:
        return list(self.recent_trades.get(symbol, []))


# Global singleton instance for MEXC live market data
mexc_live_feed_service = MEXCLiveFeedService()
