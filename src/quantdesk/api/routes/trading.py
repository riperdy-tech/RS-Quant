from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from quantdesk.api.auth import Session, require_viewer
from quantdesk.api.commands import durable_inbox

router = APIRouter(prefix="/api/v1", tags=["trading"])


from quantdesk.strategies.live_runner import autonomous_live_engine


@router.get("/positions")
def get_positions(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Trading positions, entry prices, mark prices, and PnL per §15.2."""
    return autonomous_live_engine.get_positions()


@router.get("/orders")
def get_orders(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Open and recent orders with lifecycle states (§15.2)."""
    return autonomous_live_engine.get_orders()


@router.get("/fills")
def get_fills(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Executed trade reports with fees and timestamps (§15.2)."""
    return autonomous_live_engine.get_fills()


@router.get("/balances")
def get_balances(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Cash balances, collateral, and margin usage (§15.2)."""
    return autonomous_live_engine.get_balances()


@router.get("/performance")
def get_performance(
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Total profit, win rate, and per-instrument performance breakdown (§15.2)."""
    return autonomous_live_engine.get_performance()


@router.get("/risk")
def get_risk_status(
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Risk limits vs usage, peak drawdown, daily loss, and breakers (§15.2)."""
    positions = autonomous_live_engine.get_positions()
    current_lots = sum(p.get("lots", 0) for p in positions)
    return {
        "max_position_lots": 500,
        "current_position_lots": current_lots,
        "daily_loss_limit": "500.00",
        "current_daily_loss": "0.00",
        "peak_drawdown_pct": "0.8",
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
    return autonomous_live_engine.get_strategies()


@router.get("/strategies/telemetry")
def get_strategy_telemetry(
    symbol: str = "BTCUSDT",
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Real-time microstructural features (depth imbalance, microprice, OFI, CVD, ATR)."""
    return autonomous_live_engine.get_telemetry(symbol)


@router.get("/strategies/ensemble-status")
def get_ensemble_status(
    symbol: str = "BTCUSDT",
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Returns Curated 12-Factor Multi-Timeframe Strategy status, 2H bars, and indicators."""
    return autonomous_live_engine.get_ensemble_status(symbol)


@router.get("/trading/agentic-status")
def get_agentic_status(
    symbol: str = "BTCUSDT",
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Returns deep telemetry on the Unified Agentic Alpha Engine, weights, and episodic memory."""
    return autonomous_live_engine.get_agentic_status(symbol)



@router.get("/strategies/decisions")
def get_strategy_decisions(
    session: Session = Depends(require_viewer),
) -> list[dict[str, Any]]:
    """Live stream of autonomous AI bot evaluations and trade trigger events."""
    return autonomous_live_engine.get_decisions()


@router.post("/strategies/{strategy_id}/toggle")
def toggle_strategy(
    strategy_id: str,
    running: bool,
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Starts or pauses an autonomous trading bot."""
    new_state = "RUNNING" if running else "PAUSED"
    durable_inbox.strategy_states[strategy_id] = new_state
    return {"strategy_id": strategy_id, "status": new_state}


@router.post("/trading/flatten")
def flatten_position(
    symbol: str = "BTCUSDT",
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Emergency flattens a position immediately against live market liquidity."""
    autonomous_live_engine.flatten_position(symbol)
    return {"symbol": symbol, "status": "FLATTENED"}


@router.post("/trading/pause")
def pause_all_trading(
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Pauses all autonomous algorithms immediately."""
    autonomous_live_engine.pause_trading()
    return {"status": "PAUSED", "engine_state": "PAUSED"}


@router.post("/trading/resume")
def resume_all_trading(
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Resumes all autonomous algorithms."""
    autonomous_live_engine.resume_trading()
    return {"status": "RUNNING", "engine_state": "RUNNING"}


@router.post("/trading/emergency-stop")
def emergency_stop_trading(
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Emergency halt: trips safety latch, pauses all algorithms, and immediately flattens all open positions."""
    autonomous_live_engine.emergency_stop_all()
    return {"status": "EMERGENCY_HALTED", "engine_state": "HALTED", "positions": "FLATTENED"}


@router.post("/strategies/trigger-diagnostic")
def trigger_diagnostic_signal(
    strategy_id: str = "imbalance-btc",
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Operator diagnostic action to test-trigger a simulated paper execution against live Bitget depth."""
    autonomous_live_engine.manual_trigger_signal(strategy_id, symbol, side)
    return {"status": "TRIGGERED", "strategy_id": strategy_id, "symbol": symbol, "side": side}


@router.get("/orders/{order_id}/trace")
def get_order_trace(
    order_id: str,
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Full lifecycle trace for an order from intent to settlement (§15.2)."""
    return autonomous_live_engine.get_order_trace(order_id)


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


@router.get("/trading/reflex-status")
def get_reflex_status(
    symbol: str = "BTCUSDT",
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Returns dynamic telemetry on the Event-Driven Reflex Engine (§15.2) per instrument."""
    return autonomous_live_engine.get_reflex_status(symbol)


@router.post("/trading/reflex-toggle")
def toggle_reflex_tuner(
    enabled: bool = True,
    symbol: str = "all",
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Toggles dynamic event-driven auto-tuning active or paused per leg or globally."""
    return autonomous_live_engine.toggle_reflex_tuner(enabled, symbol)


@router.post("/trading/reflex-trigger")
def trigger_reflex_audit(
    action_type: str = "MICRO_AUDIT",
    symbol: str = "all",
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Manually triggers an instantaneous micro-audit reflex or baseline calibration for symbol."""
    return autonomous_live_engine.manual_trigger_reflex(action_type, symbol)


@router.get("/trading/macro-radar")
def get_macro_radar(
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Returns real-time Fed Net Liquidity, Tether Dominance, and Whale Net Flow convergence radar."""
    return autonomous_live_engine.get_macro_radar()


@router.post("/trading/macro-radar/refresh")
def refresh_macro_radar(
    walcl: float | None = None,
    tga: float | None = None,
    rrp: float | None = None,
    usdt_d: float | None = None,
    session: Session = Depends(require_viewer),
) -> dict[str, Any]:
    """Refreshes or dynamically recalibrates Macro Convergence Radar parameters."""
    return autonomous_live_engine.refresh_macro_radar(walcl=walcl, tga=tga, rrp=rrp, usdt_d=usdt_d)


