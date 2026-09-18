from __future__ import annotations

from decimal import Decimal
import pytest

from quantdesk.research.attribution_analyzer import LossAttributionAnalyzer
from quantdesk.research.sandbox_validator import SandboxValidator
from quantdesk.research.agentic_researcher import AgenticResearchLoop
from quantdesk.strategies.unified_agentic import AttributionTag, TradeEpisode, UnifiedAgenticAlphaEngine


def test_loss_attribution_analyzer():
    analyzer = LossAttributionAnalyzer(instrument_id="BTCUSDT")

    # Seed 5 episodes with fee drag losses
    episodes = [
        TradeEpisode(
            episode_id=f"ep-{i}",
            timestamp_ns=1000 * i,
            instrument_id="BTCUSDT",
            side="BUY",
            entry_price=Decimal("65000.00"),
            exit_price=Decimal("65002.00"),
            qty_units=Decimal("0.2"),
            duration_s=45,
            gross_pnl=Decimal("0.40"),
            fee=Decimal("1.80"),
            net_pnl=Decimal("-1.40"),
            attribution=AttributionTag.FEE_DRAG_LOSS,
            features_at_entry={"volume_delta": 0.1},
        )
        for i in range(5)
    ]

    report = analyzer.analyze(episodes)
    assert report["total_trades"] == 5
    assert report["win_rate_pct"] == 0.0
    assert report["top_alpha_leak"] == "FEE_DRAG_LOSS"
    assert "ELEVATE_VOLATILITY_HURDLE" in report["recommended_hypothesis"]


def test_sandbox_validator_gates():
    validator = SandboxValidator(instrument_id="BTCUSDT")

    # Generate 100 upward trending bars with ATR
    bars = []
    p = 60000.0
    for i in range(100):
        p += 15.0 if i % 2 == 0 else -5.0
        bars.append({
            "close": p,
            "high": p + 20.0,
            "low": p - 20.0,
            "atr14": 50.0,
            "ema7": p - 2.0,
            "sma15": p - 10.0,
        })

    base_params = {"conviction_threshold": 0.35, "volatility_hurdle_bps": 5.0, "atr_target_mult": 2.0}
    cand_params = {"conviction_threshold": 0.35, "volatility_hurdle_bps": 5.0, "atr_target_mult": 2.5}

    verdict = validator.validate_candidate(bars, base_params, cand_params)
    assert isinstance(verdict.approved, bool)
    assert len(verdict.gate_checks) == 4
    assert "Gate 1: Delta Sharpe >= +0.15" in verdict.gate_checks
    assert "Gate 2: Net PnL > 0" in verdict.gate_checks


def test_agentic_research_loop_lifecycle():
    loop = AgenticResearchLoop(instrument_id="BTCUSDT")
    engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")

    # Seed bars
    bars = [
        {"close": 60000.0 + i * 5.0, "high": 60010.0 + i * 5.0, "low": 59990.0 + i * 5.0, "atr14": 40.0, "ema7": 60000.0, "sma15": 59990.0}
        for i in range(60)
    ]

    hypo = loop.run_research_cycle(engine, bars)
    assert hypo is not None
    assert hypo.hypothesis_id.startswith("hypo-")
    assert hypo.status in ("PROMOTED", "REJECTED")

    status = loop.get_status()
    assert status["total_evaluated"] >= 1
    assert len(status["recent_hypotheses"]) >= 1
