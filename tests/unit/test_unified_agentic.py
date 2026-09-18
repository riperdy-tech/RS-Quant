"""Unit tests for UnifiedAgenticAlphaEngine.

Validates:
1. Macro and Regime Compass (12-Factor Pine consensus + Whale Z-score gating)
2. Tactical Setup Engine (Squeeze momentum, McGinley trend, Volatility Fee Hurdle)
3. Microstructural Sniper Execution (L2 depth imbalance, microprice, OFI/trade flow)
4. Dynamic Chandelier Trailing Stops & Take Profit Ratchet
5. Multi-Speed Nested Learning Heartbeat (Fast attribution, medium IC parameter adaptation)
6. Dynamic parameter updating (exponential smoothing, OBI threshold scaling, ATR target multiplier)
"""

from decimal import Decimal
import time
from typing import Any
import pytest

from quantdesk.core.events import Envelope
from quantdesk.core.types import IntentAction, Side
from quantdesk.strategies.live_runner import make_live_envelope
from quantdesk.strategies.unified_agentic import (
    AttributionTag,
    DirectionalBias,
    DynamicParameters,
    EpisodicMemoryBuffer,
    MarketRegimeType,
    TradeEpisode,
    UnifiedAgenticAlphaEngine,
)


def create_book_envelope(symbol: str, seq: int, now_ns: int) -> Envelope:
    return make_live_envelope(
        event_type="BookSnapshot",
        instrument_id=symbol,
        payload={"bids": [["70000.00", "1.0"]], "asks": [["70001.00", "1.0"]]},
        now_ns=now_ns,
        engine_seq=seq,
    )


class TestUnifiedAgenticAlphaEngine:
    def test_initialization_defaults(self):
        engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT", max_leverage=3.0)
        assert engine.instrument_id == "BTCUSDT"
        assert engine.strategy_id == "unified-btc"
        assert engine.max_leverage == Decimal("3.0")
        assert engine.current_bias == DirectionalBias.STAND_ASIDE
        assert engine.current_regime == MarketRegimeType.NEUTRAL_CHOP
        assert engine.params.depth5_threshold == 0.35
        assert engine.params.entry_cooldown_s == 60
        assert engine.params.volatility_hurdle_bps == 6.0
        assert engine.params.atr_target_mult == Decimal("3.0")
        assert len(engine.params.indicator_weights) == 10
        assert all(w == 1.0 for w in engine.params.indicator_weights.values())

    def test_pipeline1_macro_compass_bullish(self):
        engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
        
        # Bullish consensus score >= 7.0 without whale distribution
        bias, regime = engine.evaluate_macro_compass({
            "consensus_score": 8.0,
            "warn_bearish": False,
            "whale_net_flow_zscore": 0.5,
        })
        assert regime == MarketRegimeType.STRONG_BULL
        assert bias == DirectionalBias.LONG_ONLY

    def test_pipeline1_macro_compass_whale_veto(self):
        engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
        
        # Bullish score but whale distribution (z-score < -1.5) vetoes long
        bias, regime = engine.evaluate_macro_compass({
            "consensus_score": 8.5,
            "warn_bearish": False,
            "whale_net_flow_zscore": -2.0,
        })
        assert regime == MarketRegimeType.STRONG_BULL
        assert bias == DirectionalBias.STAND_ASIDE

    def test_pipeline1_macro_compass_bearish(self):
        engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
        
        # Bearish consensus score <= 3.0
        bias, regime = engine.evaluate_macro_compass({
            "consensus_score": 2.5,
            "warn_bearish": True,
            "whale_net_flow_zscore": -0.5,
        })
        assert regime == MarketRegimeType.STRONG_BEAR
        assert bias == DirectionalBias.SHORT_ONLY

    def test_pipeline2_tactical_setup_fee_hurdle_rejection(self):
        engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
        engine.current_bias = DirectionalBias.LONG_ONLY
        
        # Mid = 70,000, ATR = 35 -> Volatility = 35 / 70000 * 10000 = 5 bps (< 12 bps hurdle)
        features = {
            "atr14": 35.0,
            "squeeze_color": "BLUE",
            "mcginley_value": 69900.0,
        }
        cand = engine.evaluate_tactical_setup(features, mid=70000.0)
        assert cand is None
        assert "FEE_HURDLE_TOO_LOW" in engine.tactical_state

    def test_pipeline2_tactical_setup_bullish_pass(self):
        engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
        engine.current_bias = DirectionalBias.LONG_ONLY
        
        # Mid = 70,000, ATR = 140 -> Volatility = 140 / 70000 * 10000 = 20 bps (> 12 bps hurdle)
        features = {
            "atr14": 140.0,
            "squeeze_color": "BLUE",
            "mcginley_value": 69800.0,
        }
        cand = engine.evaluate_tactical_setup(features, mid=70000.0)
        assert cand == Side.BUY
        assert engine.tactical_state == "BULLISH_SETUP_ARMED"

    def test_pipeline3_micro_sniper_confirmation(self):
        engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
        
        # Valid Buy Sniper
        features = {
            "depth5_imbalance": 0.45,  # > 0.35 threshold
            "microprice": 70000.50,
            "volume_1s_signed": 1.2,
            "l1_ofi": 0.8,
        }
        assert engine.evaluate_micro_sniper(Side.BUY, features, mid=70000.0) is True

        # Neutral OBI should reject
        features["depth5_imbalance"] = 0.10
        assert engine.evaluate_micro_sniper(Side.BUY, features, mid=70000.0) is False

    def test_end_to_end_entry_intent(self):
        engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
        now_ns = 1700000000_000_000_000
        env = create_book_envelope("BTCUSDT", 1, now_ns)
        
        features = {
            "mid": 70000.0,
            "consensus_score": 8.0,
            "warn_bearish": False,
            "whale_net_flow_zscore": 0.5,
            "atr14": 200.0,
            "squeeze_color": "BLUE",
            "mcginley_value": 69800.0,
            "depth5_imbalance": 0.45,
            "microprice": 70000.50,
            "volume_1s_signed": 1.5,
            "l1_ofi": 1.0,
        }
        
        intents = engine.on_event(env, {"features": features})
        assert len(intents) == 1
        intent = intents[0]
        assert intent.action == IntentAction.ENTER
        assert intent.side == Side.BUY
        assert intent.price_policy == "POST_ONLY"
        assert intent.stop_policy == "CHANDELIER_3X"
        assert engine.position_lots == Decimal("0.2142")
        assert intent.desired_quantity == Decimal("0.2142")
        assert engine.position_side == Side.BUY
        assert engine.entry_price == Decimal("70000.00")
        assert engine.target_price > Decimal("70000.00")
        assert engine.stop_price < Decimal("70000.00")

    def test_chandelier_trailing_stop_and_take_profit_exit(self):
        engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
        now_ns = 1700000000_000_000_000
        
        # Pre-seed open BUY position
        engine.position_lots = Decimal("1")
        engine.position_side = Side.BUY
        engine.entry_price = Decimal("70000.00")
        engine.stop_price = Decimal("69700.00")
        engine.target_price = Decimal("70600.00")
        
        # Test 1: Mid rises to 70400, Chandelier stop rises to 70200 (in profit)
        env = create_book_envelope("BTCUSDT", 2, now_ns + 10_000_000_000)
        features = {
            "mid": 70400.0,
            "chandelier_long_stop": 70200.0,
        }
        intents = engine.on_event(env, {"features": features})
        assert len(intents) == 0  # Still holding
        assert engine.stop_price == Decimal("70200.00")  # Ratcheted up in profit!

        # Test 2: Mid drops below ratcheted stop to 70150 -> Stop loss exit triggered
        env_exit = create_book_envelope("BTCUSDT", 3, now_ns + 20_000_000_000)
        features_exit = {
            "mid": 70150.0,
            "chandelier_long_stop": 70200.0,
        }
        intents_exit = engine.on_event(env_exit, {"features": features_exit})
        assert len(intents_exit) == 1
        exit_intent = intents_exit[0]
        assert exit_intent.action == IntentAction.EXIT
        assert exit_intent.side == Side.SELL
        assert exit_intent.reason == "stop_loss_hit"
        assert engine.position_lots == Decimal("0")

    def test_fast_rhythm_attribution_and_cooldown(self):
        engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
        now_ns = 1700000000_000_000_000
        
        # Case A: Profitable trade exit
        engine.record_trade_exit(
            entry_price=Decimal("70000.00"),
            exit_price=Decimal("70500.00"),
            side="BUY",
            qty_units=Decimal("0.1"),
            hold_time_s=120,
            gross_pnl=Decimal("50.00"),
            fee=Decimal("1.40"),
            net_pnl=Decimal("48.60"),
            now_ns=now_ns,
        )
        assert len(engine.memory) == 1
        assert engine.memory.episodes[-1].attribution == AttributionTag.PROFIT_TARGET_HIT
        assert engine.params.consecutive_losses == 0
        assert engine.params.entry_cooldown_s == 15  # Asymmetric short cooldown on win

        # Case B: Rapid chop loss (< 45s)
        engine.record_trade_exit(
            entry_price=Decimal("70000.00"),
            exit_price=Decimal("69800.00"),
            side="BUY",
            qty_units=Decimal("0.1"),
            hold_time_s=25,
            gross_pnl=Decimal("-20.00"),
            fee=Decimal("1.40"),
            net_pnl=Decimal("-21.40"),
            now_ns=now_ns + 100_000_000_000,
        )
        assert len(engine.memory) == 2
        assert engine.memory.episodes[-1].attribution == AttributionTag.RAPID_STOP_CHOP
        assert engine.params.consecutive_losses == 1
        assert engine.params.entry_cooldown_s == 45  # Backoff started

        # Case C: Fee drag loss (gross > 0, net < 0)
        engine.record_trade_exit(
            entry_price=Decimal("70000.00"),
            exit_price=Decimal("70005.00"),
            side="BUY",
            qty_units=Decimal("0.1"),
            hold_time_s=60,
            gross_pnl=Decimal("0.50"),
            fee=Decimal("1.40"),
            net_pnl=Decimal("-0.90"),
            now_ns=now_ns + 200_000_000_000,
        )
        assert engine.memory.episodes[-1].attribution == AttributionTag.FEE_DRAG_LOSS

    def test_medium_rhythm_autoregressive_parameter_update(self):
        engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
        now_ns = 1700000000_000_000_000
        
        # Populate 10 episodes with known indicator signals and returns
        for i in range(10):
            # Let RQK be strongly positively correlated with return
            ret = Decimal("20.00") if i % 2 == 0 else Decimal("-5.00")
            rqk_sig = 1.0 if ret > 0 else -1.0
            
            engine.entry_feature_snapshot = {
                "rqk": rqk_sig,
                "mcginley": 0.0,
                "squeeze": 1.0,
                "cmf": 0.1,
                "stc": 50.0,
                "qqe": 0.0,
                "adx": 20.0,
                "chandelier": 1.0,
                "volume_delta": 100.0,
                "donchian": 1.0,
            }
            engine.record_trade_exit(
                entry_price=Decimal("70000.00"),
                exit_price=Decimal("70000.00") + ret,
                side="BUY",
                qty_units=Decimal("0.1"),
                hold_time_s=60,
                gross_pnl=ret * Decimal("0.1"),
                fee=Decimal("0.10"),
                net_pnl=(ret * Decimal("0.1")) - Decimal("0.10"),
                now_ns=now_ns + i * 60_000_000_000,
            )
            
        # The 10th episode should have triggered run_autoregressive_parameter_update
        assert engine.params.total_episodes_evaluated == 10
        assert engine.params.last_medium_tune_ns > 0
        assert "rqk" in engine.rolling_ic
        # RQK weight should have moved away from 1.0 based on positive IC
        assert engine.params.indicator_weights["rqk"] > 1.0

    def test_get_agentic_status_telemetry(self):
        engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")
        status = engine.get_agentic_status()
        assert status["instrument_id"] == "BTCUSDT"
        assert status["strategy_id"] == "unified-btc"
        assert "current_bias" in status
        assert "current_regime" in status
        assert "tactical_state" in status
        assert "dynamic_parameters" in status
        assert "rolling_ic" in status
        assert "memory_summary" in status
