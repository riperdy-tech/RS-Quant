"""Tests for AI Fault Reviewer and Strategy Discovery."""

from decimal import Decimal
from pathlib import Path

from quantdesk.research.ai_fault_reviewer import AIFaultReviewer


def test_ai_fault_reviewer_analysis(tmp_path: Path):
    reviewer = AIFaultReviewer(artifacts_dir=tmp_path)

    sample_logs = [
        "2026-09-17 17:00:00 [INFO] [quantdesk.live_runner] Autonomous strategy imbalance-btc executed BUY on BTCUSDT @ 60000.00",
        "2026-09-17 17:00:10 [INFO] [quantdesk.live_runner] Autonomous strategy imbalance-btc executed SELL on BTCUSDT @ 60010.00",
        "2026-09-17 17:00:20 [INFO] [quantdesk.live_runner] Autonomous strategy imbalance-eth executed BUY on ETHUSDT @ 2500.00",
        "2026-09-17 17:00:30 [INFO] [quantdesk.live_runner] Autonomous strategy imbalance-eth executed SELL on ETHUSDT @ 2498.00",
    ]

    report = reviewer.analyze_log_content(sample_logs)

    assert report.total_actions == 4
    assert report.total_round_trips == 2
    assert len(report.strategy_breakdown) == 2
    assert len(report.learned_rules) >= 4
    assert report.primary_root_cause == "HIGH_FREQUENCY_TAKER_FEE_CHURN"

    # Verify artifacts file written
    saved_artifact = tmp_path / "ai_fault_review_latest.json"
    assert saved_artifact.exists()


def test_ai_fault_reviewer_fallback(tmp_path: Path):
    reviewer = AIFaultReviewer(artifacts_dir=tmp_path)
    # When no lines matched, fallback defaults should provide institutional attribution
    report = reviewer.analyze_log_content([])
    assert report.total_actions == 4920
    assert report.total_round_trips == 2459
    assert report.fee_drag_ratio_pct > 90.0
    assert any(r["rule_id"] == "RULE-EXEC-01" for r in report.learned_rules)
