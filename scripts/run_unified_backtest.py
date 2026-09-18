"""Historical Backtest Runner for Unified Self-Learning Regressive Agentic AI Alpha Engine.

Evaluates multi-horizon 4-Tier funnel + 3-speed quant heartbeat on real Bitget
USDT-Futures candles with exact 3x leverage, 33.33% locked margin, and real exchange
commissions (0.02% Maker entry / 0.02% Maker or 0.06% Taker exit).
"""

from __future__ import annotations

import json
import logging
import math
import time
import urllib.request
from decimal import Decimal
from typing import Any

import numpy as np

from quantdesk.core.events import Envelope
from quantdesk.core.types import IntentAction, Side
from quantdesk.features.ensemble_features import CuratedEnsembleExtractor
from quantdesk.strategies.live_runner import make_live_envelope
from quantdesk.strategies.unified_agentic import (
    AttributionTag,
    DirectionalBias,
    MarketRegimeType,
    TradeEpisode,
    UnifiedAgenticAlphaEngine,
)
from quantdesk.venues.bitget_uta.contract_specs import BitgetContractSpecsRegistry

logger = logging.getLogger("quantdesk.backtest.unified")


def fetch_bitget_candles(symbol: str, granularity: str = "5m", limit: int = 1000) -> list[dict[str, Any]]:
    """Fetches public Klines from Bitget USDT-FUTURES in chronological order."""
    base_url = "https://api.bitget.com/api/v2/mix/market/candles"
    url = f"{base_url}?symbol={symbol}&granularity={granularity}&limit={limit}&productType=USDT-FUTURES"
    req = urllib.request.Request(url, headers={"User-Agent": "QuantDesk/1.0"})

    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    rows = data.get("data", [])
    if not rows:
        raise ValueError(f"No candle data returned from Bitget for {symbol}")

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

    candles.sort(key=lambda x: x["timestamp"])
    return candles


def run_unified_backtest(symbol: str, granularity: str = "5m", limit: int = 1000) -> dict[str, Any]:
    """Runs high-fidelity backtest of UnifiedAgenticAlphaEngine over historical Bitget candles."""
    candles = fetch_bitget_candles(symbol, granularity=granularity, limit=limit)
    n = len(candles)
    if n < 60:
        raise ValueError(f"Insufficient candles: {n}")

    engine = UnifiedAgenticAlphaEngine(
        instrument_id=symbol,
        strategy_id=f"unified-{symbol[:3].lower()}",
        max_leverage=3.0,
    )
    extractor = CuratedEnsembleExtractor()
    spec = BitgetContractSpecsRegistry.get_spec(symbol)

    initial_capital = Decimal("10000.00")  # 10,000 USDT initial equity
    equity = initial_capital
    peak_equity = equity
    max_drawdown = Decimal("0.00")

    trades: list[dict[str, Any]] = []
    active_position: dict[str, Any] | None = None

    # Rolling bar history for feature extraction
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    volumes: list[float] = []

    # Fee parameters (Bitget standard tier)
    MAKER_FEE_RATE = Decimal("0.0002")  # 0.02%
    TAKER_FEE_RATE = Decimal("0.0006")  # 0.06%

    all_ts = [c["timestamp"] for c in candles]
    all_o = [c["open"] for c in candles]
    all_h = [c["high"] for c in candles]
    all_l = [c["low"] for c in candles]
    all_c = [c["close"] for c in candles]
    all_v = [c["volume"] for c in candles]

    # Pre-extract all vectorized ensemble indicator states
    states = extractor.compute_all(all_ts, all_o, all_h, all_l, all_c, all_v)

    # Resample candles into macro timeframe
    gran_map = {"1m": 60, "5m": 12, "15m": 4, "30m": 4, "1h": 4}
    chunk_size = gran_map.get(granularity, 12)
    macro_1h_candles = []
    for k in range(0, len(candles) - chunk_size + 1, chunk_size):
        chunk = candles[k : k + chunk_size]
        macro_1h_candles.append({
            "timestamp": chunk[-1]["timestamp"],
            "open": chunk[0]["open"],
            "high": max(c["high"] for c in chunk),
            "low": min(c["low"] for c in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(c["volume"] for c in chunk),
        })

    m_ts = [c["timestamp"] for c in macro_1h_candles]
    m_o = [c["open"] for c in macro_1h_candles]
    m_h = [c["high"] for c in macro_1h_candles]
    m_l = [c["low"] for c in macro_1h_candles]
    m_c = [c["close"] for c in macro_1h_candles]
    m_v = [c["volume"] for c in macro_1h_candles]
    macro_states = extractor.compute_all(m_ts, m_o, m_h, m_l, m_c, m_v)

    for i, c in enumerate(candles):
        closes.append(c["close"])
        highs.append(c["high"])
        lows.append(c["low"])
        volumes.append(c["volume"])

        # Warmup period (need at least 35 bars for indicators)
        if i < 35:
            continue

        bar_close = c["close"]
        bar_high = c["high"]
        bar_low = c["low"]
        ts_ns = c["timestamp"] * 1_000_000

        st = states[i]
        atr14 = st.atr_14

        # Find corresponding macro state
        macro_idx = min(len(macro_states) - 1, max(0, i // chunk_size))
        macro_st = macro_states[macro_idx] if macro_idx < len(macro_states) else st

        # Synthesize full feature snapshot for Unified Agentic Alpha Engine
        # Proxy sub-second micro indicators from candle intra-bar range
        is_bull_candle = bar_close >= c["open"]
        candle_range = bar_high - bar_low
        candle_body = abs(bar_close - c["open"])
        vol_signed = c["volume"] if is_bull_candle else -c["volume"]
        depth5_imbalance = (candle_body / candle_range) * (1.0 if is_bull_candle else -1.0) if candle_range > 0 else 0.0
        microprice = bar_close + (0.5 * (bar_close - c["open"]))

        features: dict[str, Any] = {
            "mid": bar_close,
            "atr14": atr14,
            "consensus_score": macro_st.raw_score,
            "score": macro_st.rounded_score,
            "squeeze_color": st.squeeze_color.value,
            "mcginley_value": st.mcginley_value,
            "chandelier_long_stop": macro_st.long_stop,
            "chandelier_short_stop": macro_st.short_stop,
            "rqk_trend": st.rqk_trend,
            "mcginley_trend": st.mcginley_trend,
            "cmf_trend": st.cmf_trend,
            "stc_trend": st.stc_trend,
            "qqe_trend": st.qqe_trend,
            "adx_trend": st.adx_trend,
            "chandelier_dir": st.chandelier_dir,
            "depth5_imbalance": depth5_imbalance,
            "microprice": microprice,
            "volume_1s_signed": vol_signed,
            "l1_ofi": depth5_imbalance,
        }

        # Check Active Position Exits against candle High/Low
        if active_position is not None:
            pos_side = active_position["side"]
            stop_px = Decimal(str(active_position["stop_price"]))
            target_px = Decimal(str(active_position["target_price"]))
            entry_px = Decimal(str(active_position["entry_price"]))
            qty = Decimal(str(active_position.get("units", spec.quantize_qty(Decimal("15000.00") / entry_px))))
            notional = entry_px * qty  # Exact USDT notional allocation

            exit_px: Decimal | None = None
            exit_reason: str | None = None
            is_taker_exit = False

            # Dynamic Chandelier trailing ratchet evaluation (Macro 1H Chandelier)
            if pos_side == Side.BUY and macro_st.long_stop:
                ch_dec = Decimal(str(round(macro_st.long_stop, 2)))
                if ch_dec > entry_px and ch_dec < Decimal(str(bar_close)):
                    stop_px = max(stop_px, ch_dec)
                    active_position["stop_price"] = float(stop_px)
            elif pos_side == Side.SELL and macro_st.short_stop:
                ch_dec = Decimal(str(round(macro_st.short_stop, 2)))
                if ch_dec < entry_px and ch_dec > Decimal(str(bar_close)):
                    stop_px = min(stop_px, ch_dec)
                    active_position["stop_price"] = float(stop_px)

            # Check candle price path
            if pos_side == Side.BUY:
                if Decimal(str(bar_low)) <= stop_px:
                    exit_px = stop_px
                    exit_reason = "stop_loss_hit"
                    is_taker_exit = True
                elif Decimal(str(bar_high)) >= target_px:
                    exit_px = target_px
                    exit_reason = "take_profit_hit"
                    is_taker_exit = False  # Maker limit target
            elif pos_side == Side.SELL:
                if Decimal(str(bar_high)) >= stop_px:
                    exit_px = stop_px
                    exit_reason = "stop_loss_hit"
                    is_taker_exit = True
                elif Decimal(str(bar_low)) <= target_px:
                    exit_px = target_px
                    exit_reason = "take_profit_hit"
                    is_taker_exit = False

            if exit_px is not None and exit_reason is not None:
                # Calculate gross PnL
                if pos_side == Side.BUY:
                    gross_pnl = (exit_px - entry_px) * qty
                else:
                    gross_pnl = (entry_px - exit_px) * qty

                # Fees: Entry maker (0.02%), Exit maker (0.02%) or taker (0.06%)
                entry_fee = notional * MAKER_FEE_RATE
                exit_notional = exit_px * qty
                exit_fee = exit_notional * (TAKER_FEE_RATE if is_taker_exit else MAKER_FEE_RATE)
                total_fee = entry_fee + exit_fee
                net_pnl = gross_pnl - total_fee

                # Tag attribution
                hold_sec = max(1, int((ts_ns - active_position["entry_time_ns"]) / 1_000_000_000))

                # Record Exit in Engine (Runs fast attribution & triggers self-learning heartbeat)
                engine.record_trade_exit(
                    entry_price=entry_px,
                    exit_price=exit_px,
                    side=pos_side.value,
                    qty_units=qty,
                    hold_time_s=hold_sec,
                    gross_pnl=gross_pnl,
                    fee=total_fee,
                    net_pnl=net_pnl,
                    now_ns=ts_ns,
                )

                last_episode = engine.memory.episodes[-1]
                attribution = last_episode.attribution

                equity += net_pnl
                if equity > peak_equity:
                    peak_equity = equity
                dd = (peak_equity - equity) / peak_equity
                if dd > max_drawdown:
                    max_drawdown = dd

                trades.append({
                    "trade_num": len(trades) + 1,
                    "side": pos_side.value,
                    "entry_price": float(entry_px),
                    "exit_price": float(exit_px),
                    "gross_pnl": float(gross_pnl),
                    "fees": float(total_fee),
                    "net_pnl": float(net_pnl),
                    "hold_duration_s": hold_sec,
                    "attribution": attribution.value,
                    "exit_reason": exit_reason,
                    "running_equity": float(equity),
                })
                active_position = None

        # If no active position, evaluate engine entry on candle close
        if active_position is None:
            env = make_live_envelope(
                event_type="BookSnapshot",
                instrument_id=symbol,
                payload={"bids": [[str(bar_close), "1.0"]], "asks": [[str(bar_close), "1.0"]]},
                now_ns=ts_ns,
                engine_seq=i,
            )
            intents = engine.on_event(env, {"features": features})
            if intents:
                for it in intents:
                    if it.action == IntentAction.ENTER:
                        entry_dec = spec.quantize_price(Decimal(str(engine.entry_price or bar_close)))
                        stop_dec = spec.quantize_price(Decimal(str(engine.stop_price or (bar_close * 0.995))))
                        target_dec = spec.quantize_price(Decimal(str(engine.target_price or (bar_close * 1.012))))
                        qty_dec = it.desired_quantity if (it.desired_quantity and it.desired_quantity > Decimal("0")) else spec.quantize_qty(Decimal("15000.00") / entry_dec)

                        valid, err_msg = spec.validate_order(qty_dec, entry_dec)
                        if not valid:
                            logger.warning(f"Order rejected by Bitget spec: {err_msg}")
                            continue

                        active_position = {
                            "side": it.side,
                            "entry_price": float(entry_dec),
                            "stop_price": float(stop_dec),
                            "target_price": float(target_dec),
                            "units": qty_dec,
                            "entry_time_ns": ts_ns,
                            "features_at_entry": dict(engine.entry_feature_snapshot),
                        }

    # Summary Statistics
    total_trades = len(trades)
    winning_trades = [t for t in trades if t["net_pnl"] > 0]
    losing_trades = [t for t in trades if t["net_pnl"] <= 0]
    win_rate = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0

    total_gross = sum(t["gross_pnl"] for t in trades)
    total_fees = sum(t["fees"] for t in trades)
    total_net = sum(t["net_pnl"] for t in trades)

    gross_gains = sum(t["gross_pnl"] for t in winning_trades)
    gross_losses = abs(sum(t["gross_pnl"] for t in losing_trades))
    profit_factor = (gross_gains / gross_losses) if gross_losses > 0 else (99.9 if gross_gains > 0 else 0.0)

    # Attribution Breakdown
    attr_counts: dict[str, int] = {}
    for t in trades:
        a = t["attribution"]
        attr_counts[a] = attr_counts.get(a, 0) + 1

    return {
        "symbol": symbol,
        "granularity": granularity,
        "total_candles": len(candles),
        "days_horizon": round(len(candles) * 5 / 1440, 1) if granularity == "5m" else round(len(candles) / 24, 1),
        "initial_capital": float(initial_capital),
        "final_equity": float(equity),
        "total_net_pnl": float(total_net),
        "return_pct": float(round((equity - initial_capital) / initial_capital * 100, 2)),
        "total_trades": total_trades,
        "winning_trades": len(winning_trades),
        "losing_trades": len(losing_trades),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "total_gross_pnl": round(total_gross, 2),
        "total_fees_paid": round(total_fees, 2),
        "max_drawdown_pct": float(round(max_drawdown * 100, 2)),
        "attributions": attr_counts,
        "final_weights": {k: round(w, 2) for k, w in engine.params.indicator_weights.items()},
        "recent_trades": trades[-5:],
    }


def main() -> None:
    print("=" * 75)
    print("QUANTDESK UNIFIED AGENTIC ALPHA ENGINE: HISTORICAL BACKTEST")
    print("3x Isolated Margin | Bitget UTA Fills (0.02% Maker / 0.06% Taker Fees)")
    print("=" * 75)

    test_cases = [
        ("BTCUSDT", "5m"),
        ("ETHUSDT", "5m"),
        ("BTCUSDT", "15m"),
        ("ETHUSDT", "15m"),
    ]

    for sym, gran in test_cases:
        print(f"\n[Running Backtest for {sym} ({gran} candles, limit 1,000)...]")
        try:
            res = run_unified_backtest(sym, granularity=gran, limit=1000)
            print(f">>> Results for {sym} [{gran}] ({res['days_horizon']} Days Horizon):")
            print(f"  Total Candles Analyzed   : {res['total_candles']}")
            print(f"  Initial Capital          : {res['initial_capital']:,.2f} USDT")
            print(f"  Final Account Equity     : {res['final_equity']:,.2f} USDT ({'+' if res['return_pct'] >= 0 else ''}{res['return_pct']}%)")
            print(f"  Total Net PnL (After Fees): {'+' if res['total_net_pnl'] >= 0 else ''}{res['total_net_pnl']:,.2f} USDT")
            print(f"  Gross Alpha Generated    : {res['total_gross_pnl']:,.2f} USDT")
            print(f"  Total Bitget Fees Paid   : -{res['total_fees_paid']:,.2f} USDT")
            print(f"  Total Executed Trades    : {res['total_trades']}")
            print(f"  Win Rate                 : {res['win_rate_pct']}% ({res['winning_trades']}W / {res['losing_trades']}L)")
            print(f"  Profit Factor            : {res['profit_factor']}")
            print(f"  Max Drawdown             : {res['max_drawdown_pct']}%")

            print("\n  [Attribution Breakdown]:")
            for attr, count in res["attributions"].items():
                pct = round(count / res["total_trades"] * 100, 1) if res["total_trades"] > 0 else 0.0
                print(f"    - {attr:22s}: {count:2d} ({pct}%)")

            print("\n  [Final Autoregressively Calibrated Weights (w_i)]:")
            for k, w in res["final_weights"].items():
                print(f"    - {k:14s}: {w:.2f}x")

            if res["recent_trades"]:
                print("\n  [Recent Trades Sample]:")
                for tr in res["recent_trades"]:
                    print(f"    Trade #{tr['trade_num']:2d} [{tr['side']}]: {tr['entry_price']:.1f} USDT -> {tr['exit_price']:.1f} USDT | Net: {'+' if tr['net_pnl'] >= 0 else ''}{tr['net_pnl']:.2f} USDT ({tr['attribution']})")

        except Exception as e:
            print(f"  Error running backtest for {sym} {gran}: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 75)


if __name__ == "__main__":
    main()
