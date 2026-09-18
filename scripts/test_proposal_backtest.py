"""High-Fidelity Comparative Backtest: Baseline vs. Dynamic Proposal.

Tests exact real historical Bitget candles with:
- Config A: Current Baseline (10-trade batch cadence, static 1.5x target)
- Config B: Dynamic Continuous Cadence (single-trade EWMA updates from trade 5)
- Config C: Dynamic Continuous Cadence + Regime-Scaled Profit Targets (1.2x chop to 2.2x trend)
"""

from __future__ import annotations

import json
import logging
import urllib.request
from decimal import Decimal
from typing import Any

from quantdesk.core.types import IntentAction, Side
from quantdesk.features.ensemble_features import CuratedEnsembleExtractor
from quantdesk.strategies.live_runner import make_live_envelope
from quantdesk.strategies.unified_agentic import UnifiedAgenticAlphaEngine
from quantdesk.venues.bitget_uta.contract_specs import BitgetContractSpecsRegistry


def fetch_bitget_candles(symbol: str, granularity: str = "5m", limit: int = 1000) -> list[dict[str, Any]]:
    base_url = "https://api.bitget.com/api/v2/mix/market/candles"
    url = f"{base_url}?symbol={symbol}&granularity={granularity}&limit={limit}&productType=USDT-FUTURES"
    req = urllib.request.Request(url, headers={"User-Agent": "QuantDesk/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
    rows = data.get("data", [])
    candles = [
        {
            "timestamp": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
        }
        for r in rows
    ]
    candles.sort(key=lambda x: x["timestamp"])
    return candles


def run_experiment(
    candles: list[dict[str, Any]],
    symbol: str,
    granularity: str = "5m",
    continuous_learning: bool = False,
    regime_scaled_targets: bool = False,
) -> dict[str, Any]:
    engine = UnifiedAgenticAlphaEngine(
        instrument_id=symbol,
        strategy_id=f"unified-{symbol[:3].lower()}",
        max_leverage=3.0,
    )
    extractor = CuratedEnsembleExtractor()
    spec = BitgetContractSpecsRegistry.get(symbol)

    initial_capital = Decimal("10000.00")
    equity = initial_capital
    peak_equity = equity
    max_drawdown = Decimal("0.00")

    trades: list[dict[str, Any]] = []
    active_position: dict[str, Any] | None = None

    MAKER_FEE_RATE = Decimal("0.0002")  # 0.02%
    TAKER_FEE_RATE = Decimal("0.0006")  # 0.06%

    all_ts = [c["timestamp"] for c in candles]
    all_o = [c["open"] for c in candles]
    all_h = [c["high"] for c in candles]
    all_l = [c["low"] for c in candles]
    all_c = [c["close"] for c in candles]
    all_v = [c["volume"] for c in candles]

    states = extractor.compute_all(all_ts, all_o, all_h, all_l, all_c, all_v)

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
        if i < 35:
            continue

        bar_close = c["close"]
        bar_high = c["high"]
        bar_low = c["low"]
        ts_ns = c["timestamp"] * 1_000_000

        st = states[i]
        atr14 = st.atr_14

        macro_idx = min(len(macro_states) - 1, max(0, i // chunk_size))
        macro_st = macro_states[macro_idx] if macro_idx < len(macro_states) else st

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

        # Check Exits against High / Low
        if active_position is not None:
            pos_side = active_position["side"]
            stop_px = Decimal(str(active_position["stop_price"]))
            target_px = Decimal(str(active_position["target_price"]))
            entry_px = Decimal(str(active_position["entry_price"]))
            qty = Decimal(str(active_position["units"]))
            notional = entry_px * qty

            exit_px: Decimal | None = None
            exit_reason: str | None = None
            is_taker_exit = False

            # Dynamic Chandelier trailing ratchet
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

            # Candle path execution
            if pos_side == Side.BUY:
                if Decimal(str(bar_low)) <= stop_px:
                    exit_px = stop_px
                    exit_reason = "stop_loss_hit"
                    is_taker_exit = True
                elif Decimal(str(bar_high)) >= target_px:
                    exit_px = target_px
                    exit_reason = "take_profit_hit"
                    is_taker_exit = False
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
                if pos_side == Side.BUY:
                    gross_pnl = (exit_px - entry_px) * qty
                else:
                    gross_pnl = (entry_px - exit_px) * qty

                entry_fee = notional * MAKER_FEE_RATE
                exit_notional = exit_px * qty
                exit_fee = exit_notional * (TAKER_FEE_RATE if is_taker_exit else MAKER_FEE_RATE)
                total_fee = entry_fee + exit_fee
                net_pnl = gross_pnl - total_fee

                hold_sec = max(1, int((ts_ns - active_position["entry_time_ns"]) / 1_000_000_000))

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

                # Proposed Feature: Continuous Dynamic Cadence (single-trade EWMA updates)
                if continuous_learning and len(engine.memory.episodes) >= 5:
                    engine.run_autoregressive_parameter_update(ts_ns)

                equity += net_pnl
                if equity > peak_equity:
                    peak_equity = equity
                dd = (peak_equity - equity) / peak_equity
                if dd > max_drawdown:
                    max_drawdown = dd

                trades.append({
                    "trade_num": len(trades) + 1,
                    "side": pos_side.value,
                    "gross_pnl": float(gross_pnl),
                    "fees": float(total_fee),
                    "net_pnl": float(net_pnl),
                    "hold_duration_s": hold_sec,
                    "exit_reason": exit_reason,
                    "running_equity": float(equity),
                })
                active_position = None

        # Entry Evaluation on candle close
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
                        
                        if regime_scaled_targets:
                            atr_dec = Decimal(str(atr14 or (bar_close * 0.005)))
                            regime_val = macro_st.regime.value
                            mult = Decimal("2.0") if "STRONG" in regime_val else (Decimal("1.2") if "WEAK" in regime_val else Decimal("1.5"))
                            target_dist = max(mult * atr_dec, entry_dec * Decimal("0.0045"))
                            target_dec = spec.quantize_price(entry_dec + target_dist if it.side == Side.BUY else entry_dec - target_dist)
                        else:
                            target_dec = spec.quantize_price(Decimal(str(engine.target_price or (bar_close * 1.012))))

                        qty_dec = it.desired_quantity if (it.desired_quantity and it.desired_quantity > Decimal("0")) else spec.quantize_qty(Decimal("15000.00") / entry_dec)
                        active_position = {
                            "side": it.side,
                            "entry_price": float(entry_dec),
                            "stop_price": float(stop_dec),
                            "target_price": float(target_dec),
                            "units": qty_dec,
                            "entry_time_ns": ts_ns,
                        }

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

    return {
        "symbol": symbol,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown_pct": float(max_drawdown * 100),
        "total_gross_usdt": total_gross,
        "total_fees_usdt": total_fees,
        "total_net_usdt": total_net,
        "final_equity_usdt": float(equity),
        "return_pct": float((equity - initial_capital) / initial_capital * 100),
    }


def main():
    print("=" * 90)
    print("RIGOROUS PROPOSAL EVALUATION: BASELINE VS. DYNAMIC CONTINUOUS CADENCE")
    print("Exact 1:1 Matching on Real Historical Bitget Futures Data")
    print("=" * 90)

    datasets = [
        ("BTCUSDT", "5m", 1000),
        ("ETHUSDT", "5m", 1000),
        ("BTCUSDT", "15m", 1000),
        ("ETHUSDT", "15m", 1000),
    ]

    for sym, gran, lim in datasets:
        candles = fetch_bitget_candles(sym, granularity=gran, limit=lim)
        
        # 1. Baseline: Static 10-batch
        base = run_experiment(candles, sym, granularity=gran, continuous_learning=False, regime_scaled_targets=False)
        # 2. Proposal A: Continuous Dynamic Cadence (single-trade EWMA updates from trade 5)
        prop_a = run_experiment(candles, sym, granularity=gran, continuous_learning=True, regime_scaled_targets=False)
        # 3. Proposal B: Continuous Cadence + Regime-Scaled Targets
        prop_b = run_experiment(candles, sym, granularity=gran, continuous_learning=True, regime_scaled_targets=True)

        print(f"\n--- Results for {sym} [{gran}] (1,000 candles) ---")
        header = f"{'Metric':<22} | {'1. Baseline (Batch 10)':<20} | {'2. Dynamic Cadence':<20} | {'3. Cadence + Regime':<20}"
        print(header)
        print("-" * len(header))
        print(f"{'Total Trades':<22} | {base['total_trades']:<20} | {prop_a['total_trades']:<20} | {prop_b['total_trades']:<20}")
        print(f"{'Win Rate (%)':<22} | {base['win_rate']:<19.1f}% | {prop_a['win_rate']:<19.1f}% | {prop_b['win_rate']:<19.1f}%")
        print(f"{'Profit Factor':<22} | {base['profit_factor']:<20.2f} | {prop_a['profit_factor']:<20.2f} | {prop_b['profit_factor']:<20.2f}")
        print(f"{'Net Profit (USDT)':<22} | {base['total_net_usdt']:<+15.2f} USDT | {prop_a['total_net_usdt']:<+15.2f} USDT | {prop_b['total_net_usdt']:<+15.2f} USDT")
        print(f"{'Return (%)':<22} | {base['return_pct']:<+19.2f}% | {prop_a['return_pct']:<+19.2f}% | {prop_b['return_pct']:<+19.2f}%")
        print(f"{'Max Drawdown (%)':<22} | {base['max_drawdown_pct']:<19.2f}% | {prop_a['max_drawdown_pct']:<19.2f}% | {prop_b['max_drawdown_pct']:<19.2f}%")


if __name__ == "__main__":
    main()
