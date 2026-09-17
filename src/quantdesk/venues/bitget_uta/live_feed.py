"""Live WebSocket market data feed from Bitget UTA V3 public streams."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from quantdesk.api.routes.events import event_hub

logger = logging.getLogger("quantdesk.live_feed")


class BitgetLiveFeedService:
    """Manages persistent public WebSocket connection to Bitget and streams live ticks."""

    def __init__(
        self,
        symbols: tuple[str, ...] = ("BTCUSDT", "ETHUSDT"),
        ws_url: str = "wss://ws.bitget.com/v2/ws/public",
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
        self._task = asyncio.create_task(self._run_loop(), name="bitget-live-feed")
        logger.info("BitgetLiveFeedService started.")

    async def stop(self) -> None:
        """Stops the background feed task cleanly."""
        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self.is_connected = False
        logger.info("BitgetLiveFeedService stopped.")

    async def _run_loop(self) -> None:
        backoff = 1.0
        while self.is_running:
            try:
                logger.info(f"Connecting to Bitget public WebSocket: {self.ws_url}")
                async with connect(
                    self.ws_url,
                    ping_interval=None,
                    max_size=4 * 1024 * 1024,
                    open_timeout=10,
                ) as ws:
                    self.is_connected = True
                    backoff = 1.0
                    logger.info("Connected to Bitget live WebSocket.")

                    # Subscribe to ticker, books15, and trade for configured symbols
                    sub_args = []
                    for sym in self.symbols:
                        sub_args.append(
                            {"instType": "USDT-FUTURES", "channel": "ticker", "instId": sym}
                        )
                        sub_args.append(
                            {"instType": "USDT-FUTURES", "channel": "books15", "instId": sym}
                        )
                        sub_args.append(
                            {"instType": "USDT-FUTURES", "channel": "trade", "instId": sym}
                        )

                    sub_msg = {"op": "subscribe", "args": sub_args}
                    await ws.send(json.dumps(sub_msg))

                    # Ping keepalive task
                    ping_task = asyncio.create_task(self._ping_loop(ws))

                    try:
                        async for raw_msg in ws:
                            if not self.is_running:
                                break
                            if isinstance(raw_msg, bytes):
                                raw_msg = raw_msg.decode("utf-8", errors="replace")
                            if raw_msg == "pong":
                                continue
                            try:
                                data = json.loads(raw_msg)
                                self._handle_message(data)
                            except Exception as handle_err:
                                logger.error(f"Error handling WS message: {handle_err}", exc_info=True)
                    finally:
                        ping_task.cancel()
            except (ConnectionClosed, OSError, TimeoutError, Exception) as exc:
                self.is_connected = False
                if not self.is_running:
                    break
                logger.warning(
                    f"Bitget WebSocket disconnected ({exc}). Reconnecting in {backoff:.1f}s..."
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 30.0)

    async def _ping_loop(self, ws: Any) -> None:
        """Sends periodic ping every 25 seconds per Bitget WS protocol."""
        while self.is_running:
            try:
                await asyncio.sleep(25.0)
                await ws.send("ping")
            except Exception:
                break

    def _handle_message(self, msg: dict[str, Any]) -> None:
        """Dispatches inbound WebSocket message to relevant handlers."""
        action = msg.get("action")
        arg = msg.get("arg", {})
        channel = arg.get("channel")
        inst_id = arg.get("instId")

        if not channel or not inst_id:
            return

        payload_data = msg.get("data", [])
        if not payload_data:
            return

        now_ns = time.time_ns()
        from quantdesk.strategies.live_runner import autonomous_live_engine

        if channel == "ticker":
            row = payload_data[0]
            ticker_info = {
                "symbol": inst_id,
                "last_price": row.get("lastPr"),
                "bid_price": row.get("bidPr"),
                "ask_price": row.get("askPr"),
                "bid_size": row.get("bidSz"),
                "ask_size": row.get("askSz"),
                "mark_price": row.get("markPrice"),
                "index_price": row.get("indexPrice"),
                "high_24h": row.get("high24h"),
                "low_24h": row.get("low24h"),
                "change_24h": row.get("change24h"),
                "volume_24h": row.get("baseVolume"),
                "quote_volume_24h": row.get("quoteVolume"),
                "funding_rate": row.get("fundingRate"),
                "next_funding_time": row.get("nextFundingTime"),
                "ts_ms": row.get("ts"),
                "updated_at_ns": now_ns,
            }
            self.tickers[inst_id] = ticker_info
            if ticker_info.get("mark_price") and ticker_info.get("last_price"):
                autonomous_live_engine.process_ticker_update(
                    inst_id, ticker_info["mark_price"], ticker_info["last_price"]
                )
            self._throttle_publish(
                f"ticker:{inst_id}",
                topic="market_delta",
                payload={"type": "TICKER", "data": ticker_info},
                min_interval_ms=250,
            )

        elif channel in ("books", "books15", "books5"):
            row = payload_data[0]
            bids = row.get("bids", [])
            asks = row.get("asks", [])
            if action == "snapshot" or not self.order_books[inst_id]["bids"]:
                self.order_books[inst_id] = {"bids": bids, "asks": asks}
            else:
                self._apply_book_diff(inst_id, bids, asks)

            book_snap = {
                "symbol": inst_id,
                "bids": self.order_books[inst_id]["bids"][:15],
                "asks": self.order_books[inst_id]["asks"][:15],
                "ts_ms": row.get("ts"),
                "updated_at_ns": now_ns,
            }
            autonomous_live_engine.process_book_update(
                inst_id, book_snap["bids"], book_snap["asks"], row.get("ts")
            )
            self._throttle_publish(
                f"books:{inst_id}",
                topic="market_delta",
                payload={"type": "DEPTH", "data": book_snap},
                min_interval_ms=250,
            )

        elif channel == "trade":
            trades_list = []
            for item in payload_data:
                trade_record = {
                    "trade_id": item.get("tradeId", f"tr-{now_ns}"),
                    "symbol": inst_id,
                    "price": item.get("price"),
                    "size": item.get("size"),
                    "side": item.get("side", "buy").upper(),
                    "ts_ms": item.get("ts"),
                    "time_ns": now_ns,
                }
                self.recent_trades[inst_id].appendleft(trade_record)
                trades_list.append(trade_record)
                autonomous_live_engine.process_trade_update(
                    inst_id,
                    trade_record["price"],
                    trade_record["size"],
                    trade_record["side"],
                    trade_record.get("ts_ms"),
                )

            self._throttle_publish(
                f"trade:{inst_id}",
                topic="market_delta",
                payload={"type": "TRADES", "symbol": inst_id, "trades": trades_list},
                min_interval_ms=100,
            )

    def _apply_book_diff(
        self, inst_id: str, new_bids: list[list[str]], new_asks: list[list[str]]
    ) -> None:
        """Applies depth delta to in-memory order book."""
        book = self.order_books[inst_id]
        bid_map = {row[0]: row[1] for row in book["bids"]}
        for price, sz in new_bids:
            if float(sz) == 0:
                bid_map.pop(price, None)
            else:
                bid_map[price] = sz
        sorted_bids = sorted(bid_map.items(), key=lambda x: float(x[0]), reverse=True)
        book["bids"] = [[p, s] for p, s in sorted_bids[:15]]

        ask_map = {row[0]: row[1] for row in book["asks"]}
        for price, sz in new_asks:
            if float(sz) == 0:
                ask_map.pop(price, None)
            else:
                ask_map[price] = sz
        sorted_asks = sorted(ask_map.items(), key=lambda x: float(x[0]))
        book["asks"] = [[p, s] for p, s in sorted_asks[:15]]

    def _throttle_publish(
        self, key: str, topic: str, payload: dict[str, Any], min_interval_ms: int = 250
    ) -> None:
        """Publishes event to SSE hub coalescing to max 4 Hz per channel."""
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


# Global singleton instance
live_feed_service = BitgetLiveFeedService()
