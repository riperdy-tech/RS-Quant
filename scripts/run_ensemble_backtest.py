"""Historical Backtest Runner for Curated 12-Factor Multi-Timeframe Strategy Engine.

Fetches historical 1H candles from Bitget's public market API, resamples them into
2-Hour macro bars (matching the Pine Script native timeframe), and executes a
high-fidelity backtest using CuratedEnsembleStrategy and Chandelier risk budgeting.
"""

from __future__ import annotations

import json
import time
import urllib.request
from decimal import Decimal
from typing import Any

import numpy as np

from quantdesk.core.events import Envelope
from quantdesk.core.types import IntentAction, Side
from quantdesk.features.ensemble_features import CuratedEnsembleExtractor, EnsembleBarState
from quantdesk.strategies.curated_ensemble import CuratedEnsembleStrategy
from quantdesk.strategies.live_runner import make_live_envelope


def fetch_bitget_1h_candles(symbol: str, total_bars: int = 1000) -> list[dict[str, Any]]:
    """Fetches public 1H Klines from Bitget USDT-FUTURES in chronological order."""
    base_url = "https://api.bitget.com/api/v2/mix/market/candles"
    limit = min(1000, total_bars)
    url = f"{base_url}?symbol={symbol}&granularity=1H&limit={limit}&productType=USDT-FUTURES"
    req = urllib.request.Request(url, headers={"User-Agent": "QuantDesk/1.0"})

    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    rows = data.get("data", [])
    if not rows:
        raise ValueError(f"No candle data returned from Bitget for {symbol}")

    # Bitget returns [ts, open, high, low, close, vol, quote_vol]
    candles: list[dict[str, Any]] = []
    for r in rows:
        candles.append({
            "timestamp": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        })

    # Sort ascending chronologically
    candles.sort(key=lambda x: x["timestamp"])
    return candles


def resample_1h_to_2h(candles_1h: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merges 1H candles into 2H macro candles."""
    candles_2h: list[dict[str, Any]] = []
    # Ensure even pairs
    n = len(candles_1h)
    start_idx = 0 if (n % 2 == 0) else 1

    for i in range(start_idx, n - 1, 2):
        c1 = candles_1h[i]
        c2 = candles_1h[i + 1]
        candles_2h.append({
            "timestamp": c1["timestamp"],
            "open": c1["open"],
            "high": max(c1["high"], c2["high"]),
            "low": min(c1["low"], c2["low"]),
            "close": c2["close"],
            "volume": c1["volume"] + c2["volume"],
        })

    return candles_2h


def run_backtest_for_symbol(symbol: str, total_1h_bars: int = 1000) -> dict[str, Any]:
    """Executes the complete backtest for symbol over 2H resampled candles."""
    raw_1h = fetch_bitget_1h_candles(symbol, total_bars=total_1h_bars)
    candles_2h = resample_1h_to_2h(raw_1h)

    strategy = CuratedEnsembleStrategy(
        instrument_id=symbol,
        strategy_id=f"ensemble-{symbol[:3].lower()}",
        macro_timeframe_seconds=7200,
        risk_pct_per_trade=0.02,
        max_leverage=5.0,
        enable_break_even=True,
        enable_trailing_sl=True,
    )

    initial_capital = Decimal("10000.00")
    equity = initial_capital
    peak_equity = equity
    max_drawdown_pct = Decimal("0.0")

    trades: list[dict[str, Any]] = []
    active_trade: dict[str, Any] | None = None

    # Filter counter tracking
    filter_stats = {
        "total_donchian_breakouts": 0,
        "vetoed_by_consensus_score": 0,
        "vetoed_by_squeeze_momentum": 0,
        "vetoed_by_knife_catch_gate": 0,
        "vetoed_by_adx_or_hv": 0,
        "executed_entries": 0,
    }

    extractor = CuratedEnsembleExtractor()

    # Pre-extract all states to track filter attribution
    all_ts = [c["timestamp"] for c in candles_2h]
    all_o = [c["open"] for c in candles_2h]
    all_h = [c["high"] for c in candles_2h]
    all_l = [c["low"] for c in candles_2h]
    all_c = [c["close"] for c in candles_2h]
    all_v = [c["volume"] for c in candles_2h]
    states = extractor.compute_all(all_ts, all_o, all_h, all_l, all_c, all_v)

    for i, (candle, state) in enumerate(zip(candles_2h, states)):
        t_ns = candle["timestamp"] * 1_000_000
        env = make_live_envelope(
            event_type="BarClosed",
            instrument_id=symbol,
            payload={},
            now_ns=t_ns,
            engine_seq=i,
        )

        ctx = {
            "features": {
                "close": candle["close"],
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "volume": candle["volume"],
            },
            "equity": float(equity),
        }

        # Filter attribution tracking (when flat)
        if not active_trade and i >= 17:
            is_break_long = candle["close"] > state.donchian_high
            is_break_short = candle["close"] < state.donchian_low
            if is_break_long or is_break_short:
                filter_stats["total_donchian_breakouts"] += 1
                if is_break_long:
                    if state.rounded_score < 5:
                        filter_stats["vetoed_by_consensus_score"] += 1
                    elif not state.squeeze_long_ok:
                        filter_stats["vetoed_by_squeeze_momentum"] += 1
                    elif not state.score_gate_long_ok:
                        filter_stats["vetoed_by_knife_catch_gate"] += 1
                    elif state.adx_value < 18.0 or state.hv_annualized < 1.0:
                        filter_stats["vetoed_by_adx_or_hv"] += 1
                else:
                    if state.rounded_score > 5:
                        filter_stats["vetoed_by_consensus_score"] += 1
                    elif not state.squeeze_short_ok:
                        filter_stats["vetoed_by_squeeze_momentum"] += 1
                    elif not state.score_gate_short_ok:
                        filter_stats["vetoed_by_knife_catch_gate"] += 1
                    elif state.adx_value < 18.0 or state.hv_annualized < 1.0:
                        filter_stats["vetoed_by_adx_or_hv"] += 1

        intents = strategy.on_event(env, ctx)
        if not intents:
            continue

        for intent in intents:
            if intent.action == IntentAction.ENTER:
                filter_stats["executed_entries"] += 1
                entry_p = Decimal(str(candle["close"]))
                qty = intent.desired_quantity
                active_trade = {
                    "trade_id": len(trades) + 1,
                    "symbol": symbol,
                    "side": intent.side.value,
                    "entry_time": candle["timestamp"],
                    "entry_price": entry_p,
                    "quantity": qty,
                    "notional": entry_p * qty,
                    "stop_price": strategy.stop_price,
                    "target_price": strategy.target_price,
                    "score": state.rounded_score,
                    "reason": intent.reason,
                }

            elif intent.action == IntentAction.EXIT and active_trade:
                exit_p = Decimal(str(candle["close"]))
                side = active_trade["side"]
                qty = active_trade["quantity"]

                if side == "BUY":
                    gross_pnl = (exit_p - active_trade["entry_price"]) * qty
                else:
                    gross_pnl = (active_trade["entry_price"] - exit_p) * qty

                # 0.04% taker fee per leg
                fee = (active_trade["notional"] + exit_p * qty) * Decimal("0.0004")
                net_pnl = gross_pnl - fee

                equity += net_pnl
                peak_equity = max(peak_equity, equity)
                dd = ((peak_equity - equity) / peak_equity) * Decimal("100")
                max_drawdown_pct = max(max_drawdown_pct, dd)

                active_trade["exit_time"] = candle["timestamp"]
                active_trade["exit_price"] = exit_p
                active_trade["gross_pnl"] = float(gross_pnl)
                active_trade["fee"] = float(fee)
                active_trade["net_pnl"] = float(net_pnl)
                active_trade["return_pct"] = float((net_pnl / active_trade["notional"]) * 100)
                active_trade["exit_reason"] = intent.reason
                active_trade["running_equity"] = float(equity)

                trades.append(active_trade)
                active_trade = None

    # Performance Metrics
    total_trades = len(trades)
    winning_trades = [t for t in trades if t["net_pnl"] > 0]
    losing_trades = [t for t in trades if t["net_pnl"] <= 0]
    win_rate = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0

    total_profit = sum(t["net_pnl"] for t in winning_trades)
    total_loss = abs(sum(t["net_pnl"] for t in losing_trades))
    profit_factor = (total_profit / total_loss) if total_loss > 0 else (99.0 if total_profit > 0 else 0.0)
    total_net_pnl = float(equity - initial_capital)
    roi_pct = (total_net_pnl / float(initial_capital)) * 100.0

    returns = [t["return_pct"] for t in trades]
    sharpe = (np.mean(returns) / (np.std(returns) + 1e-8) * np.sqrt(365 * 12)) if len(returns) > 1 else 0.0

    return {
        "symbol": symbol,
        "candle_count_1h": len(raw_1h),
        "candle_count_2h": len(candles_2h),
        "horizon_days": round(len(raw_1h) / 24.0, 1),
        "initial_capital": float(initial_capital),
        "final_equity": float(equity),
        "net_pnl": total_net_pnl,
        "roi_pct": round(roi_pct, 2),
        "total_trades": total_trades,
        "wins": len(winning_trades),
        "losses": len(losing_trades),
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(float(max_drawdown_pct), 2),
        "annualized_sharpe": round(float(sharpe), 2),
        "filter_attribution": filter_stats,
        "recent_trades": trades[-5:] if trades else [],
    }


def main():
    print("=" * 70)
    print("QUANTDESK 12-FACTOR ENSEMBLE HISTORICAL BACKTEST (2-HOUR CANDLES)")
    print("Data Source: Bitget UTA V3 Public Market Klines (Real Historical Fills)")
    print("=" * 70)

    for symbol in ["BTCUSDT", "ETHUSDT"]:
        print(f"\nFetching historical data and running backtest for {symbol}...")
        res = run_backtest_for_symbol(symbol, total_1h_bars=1000)

        print(f"\n>>> Results for {symbol} ({res['horizon_days']} Days Horizon):")
        print(f"  Total 2h Candles Analyzed: {res['candle_count_2h']}")
        print(f"  Net PnL                  : ${res['net_pnl']:+,.2f} ({res['roi_pct']:+.2f}%)")
        print(f"  Final Account Equity     : ${res['final_equity']:,.2f} (from ${res['initial_capital']:,.2f})")
        print(f"  Total Executed Trades    : {res['total_trades']}")
        print(f"  Win Rate                 : {res['win_rate_pct']}% ({res['wins']}W / {res['losses']}L)")
        print(f"  Profit Factor            : {res['profit_factor']}")
        print(f"  Max Drawdown             : {res['max_drawdown_pct']}%")
        print(f"  Annualized Sharpe Ratio  : {res['annualized_sharpe']}")

        print("\n  [Filter Attribution Analysis]")
        fa = res["filter_attribution"]
        print(f"  Total Raw Breakouts Detected      : {fa['total_donchian_breakouts']}")
        print(f"  - Vetoed by Low Consensus Score   : {fa['vetoed_by_consensus_score']}")
        print(f"  - Vetoed by Squeeze Momentum Color: {fa['vetoed_by_squeeze_momentum']}")
        print(f"  - Vetoed by Knife-Catch Gate (S15): {fa['vetoed_by_knife_catch_gate']}")
        print(f"  - Vetoed by Low ADX / Volatility  : {fa['vetoed_by_adx_or_hv']}")
        print(f"  => Qualified Institutional Entries: {fa['executed_entries']}")

        if res["recent_trades"]:
            print("\n  [Last Executed Trades]:")
            for t in res["recent_trades"]:
                print(f"    Trade #{t['trade_id']}: {t['side']} @ ${t['entry_price']} -> ${t['exit_price']} | Net: ${t['net_pnl']:+.2f} ({t['exit_reason']})")


if __name__ == "__main__":
    main()
