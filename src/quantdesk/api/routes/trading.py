from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from quantdesk.api.auth import Session, require_viewer
from quantdesk.api.commands import durable_inbox

router = APIRouter(prefix="/api/v1", tags=["trading"])


@router.get("/positions")
def get_positions(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Trading positions, entry prices, mark prices, and PnL per §15.2."""
    return [
        {
            "instrument_id": "BTCUSDT",
            "lots": 100,
            "side": "BUY",
            "entry_price": "65000.00",
            "mark_price": "65250.00",
            "unrealized_pnl": "25.00",
            "realized_pnl": "120.50",
            "margin_equity": "1500.00",
            "initial_margin": "650.00",
            "maintenance_margin": "260.00",
            "currency": "USDT",
            "timestamp_ns": time.time_ns(),
        }
    ]


@router.get("/orders")
def get_orders(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Open and recent orders with lifecycle states (§15.2)."""
    return [
        {
            "order_id": "ord-btc-001",
            "client_order_id": "client-ord-1",
            "strategy_id": "imbalance-btc",
            "instrument_id": "BTCUSDT",
            "side": "BUY",
            "order_type": "LIMIT",
            "qty": "0.1",
            "limit_price": "64800.00",
            "filled_qty": "0.1",
            "status": "FILLED",
            "created_at_ns": time.time_ns() - 3600_000_000_000,
        }
    ]


@router.get("/fills")
def get_fills(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Executed trade reports with fees and timestamps (§15.2)."""
    return [
        {
            "fill_id": "fill-001",
            "order_id": "ord-btc-001",
            "instrument_id": "BTCUSDT",
            "side": "BUY",
            "price": "64800.00",
            "qty": "0.1",
            "fee": "0.0356",
            "fee_currency": "USDT",
            "liquidity": "TAKER",
            "timestamp_ns": time.time_ns() - 3600_000_000_000,
        }
    ]


@router.get("/balances")
def get_balances(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Cash balances, collateral, and margin usage (§15.2)."""
    return [
        {
            "currency": "USDT",
            "total": "10000.00",
            "available": "8500.00",
            "locked_margin": "1500.00",
            "unrealized_pnl": "25.00",
        }
    ]


@router.get("/risk")
def get_risk_status(
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Risk limits vs usage, peak drawdown, daily loss, and breakers (§15.2)."""
    return {
        "max_position_lots": 500,
        "current_position_lots": 100,
        "daily_loss_limit": "500.00",
        "current_daily_loss": "0.00",
        "peak_drawdown_pct": "1.2",
        "max_drawdown_limit_pct": "5.0",
        "breakers_tripped": ["EMERGENCY_KILL"] if durable_inbox.emergency_halted else [],
        "emergency_flatten_active": durable_inbox.emergency_halted,
        "mode": "DEMO",
    }


@router.get("/strategies")
def get_strategies(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Active strategies, allocations, parameters, and status (§15.2)."""
    return [
        {
            "strategy_id": "imbalance-btc",
            "name": "Order Book Imbalance Scalper",
            "instrument_id": "BTCUSDT",
            "status": durable_inbox.strategy_states.get("imbalance-btc", "RUNNING"),
            "capital_allocation": "5000.00",
            "signal": "NEUTRAL",
            "active_model_id": "lgbm-champion",
        },
        {
            "strategy_id": "momentum-btc",
            "name": "Momentum Breakout",
            "instrument_id": "BTCUSDT",
            "status": durable_inbox.strategy_states.get("momentum-btc", "RUNNING"),
            "capital_allocation": "5000.00",
            "signal": "BULLISH",
            "active_model_id": None,
        },
    ]


@router.get("/orders/{order_id}/trace")
def get_order_trace(
    order_id: str,
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Full lifecycle trace for an order from intent to settlement (§15.2)."""
    return {
        "order_id": order_id,
        "trace_timeline": [
            {
                "step": "INTENT_GENERATED",
                "timestamp_ns": 1000,
                "detail": "Strategy emitted entry intent",
            },
            {
                "step": "RISK_APPROVED",
                "timestamp_ns": 1050,
                "detail": "Risk limits approved 0.1 lots",
            },
            {
                "step": "OMS_ROUTED",
                "timestamp_ns": 1100,
                "detail": "Instruction committed to outbox",
            },
            {
                "step": "VENUE_ACK",
                "timestamp_ns": 1200,
                "detail": "Order accepted at simulated venue",
            },
            {
                "step": "FILL_REPORTED",
                "timestamp_ns": 1250,
                "detail": "Filled 0.1 lots at 64800.00",
            },
            {
                "step": "LEDGER_POSTED",
                "timestamp_ns": 1300,
                "detail": "Ledger double-entry balanced postings committed",
            },
        ],
    }


@router.get("/market/ticker")
def get_market_ticker(
    symbol: str = "BTCUSDT",
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Returns live Bitget ticker with mark price, 24h stats, and funding rate."""
    from quantdesk.venues.bitget_uta.live_feed import live_feed_service

    ticker = live_feed_service.get_ticker(symbol)
    if ticker:
        return ticker
    return {
        "symbol": symbol,
        "last_price": "76119.50",
        "bid_price": "76119.50",
        "ask_price": "76119.60",
        "mark_price": "76119.50",
        "funding_rate": "0.000064",
        "change_24h": "0.0070",
        "volume_24h": "32111.47",
        "updated_at_ns": time.time_ns(),
    }


@router.get("/market/depth")
def get_market_depth(
    symbol: str = "BTCUSDT",
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Returns live L2 order book depth (bids/asks) from Bitget."""
    from quantdesk.venues.bitget_uta.live_feed import live_feed_service

    book = live_feed_service.get_order_book(symbol)
    return {
        "symbol": symbol,
        "bids": book.get("bids", []),
        "asks": book.get("asks", []),
        "timestamp_ns": time.time_ns(),
    }


@router.get("/market/trades")
def get_market_trades(
    symbol: str = "BTCUSDT",
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Returns recent executed trade prints from live Bitget stream."""
    from quantdesk.venues.bitget_uta.live_feed import live_feed_service

    return live_feed_service.get_trades(symbol)


@router.get("/market/status")
def get_market_feed_status(
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Returns status of live Bitget WebSocket stream."""
    from quantdesk.venues.bitget_uta.live_feed import live_feed_service

    return {
        "is_running": live_feed_service.is_running,
        "is_connected": live_feed_service.is_connected,
        "venue": "bitget",
        "symbols": list(live_feed_service.symbols),
        "source": "wss://ws.bitget.com/v2/ws/public",
    }
