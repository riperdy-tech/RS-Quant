"""Generate demo-only synthetic fixtures for backtest and ML pipeline tests per §13 and Task 11."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from quantdesk.research.datasets import DatasetBuilder
from quantdesk.research.labels import LabelBuilder
from quantdesk.research.splits import PurgedWalkForward


def generate_demo_fixtures(output_dir: Path | None = None) -> dict[str, str]:
    if output_dir is None:
        output_dir = Path("fixtures/ml")
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate synthetic market timeline: 300 timestamps at 1-second intervals
    base_time_ns = 1_700_000_000_000_000_000
    interval_ns = 1_000_000_000
    total_steps = 300

    events: list[dict] = []
    market_quotes: dict[int, tuple[float, float]] = {}
    candidates: list[dict] = []

    current_price = 50000.0

    for i in range(total_steps):
        t_ns = base_time_ns + i * interval_ns

        # Deterministic price oscillation with trend and noise
        delta = 20.0 * (1 if (i // 20) % 2 == 0 else -1) + (5.0 if i % 3 == 0 else -5.0)
        current_price += delta
        bid_price = current_price - 1.0
        ask_price = current_price + 1.0

        market_quotes[t_ns] = (bid_price, ask_price)

        # Market Quote envelope dict
        events.append(
            {
                "event_id": f"quote-{i}",
                "event_type": "Quote",
                "exchange_event_ns": t_ns,
                "received_ns": t_ns + 10_000_000,
                "available_ns": t_ns + 20_000_000,
                "engine_seq": i + 1,
                "correlation_id": f"q-{i}",
                "causality_token": f"tok-{i}",
                "payload": {
                    "bid_price_ticks": int(bid_price),
                    "bid_size_lots": 100,
                    "ask_price_ticks": int(ask_price),
                    "ask_size_lots": 100,
                },
            }
        )

        # Periodic closed bar every 10 steps
        if (i + 1) % 10 == 0:
            bar_start = t_ns - 10 * interval_ns
            events.append(
                {
                    "event_id": f"bar-{i // 10}",
                    "event_type": "BarClosed",
                    "exchange_event_ns": t_ns,
                    "received_ns": t_ns + 10_000_000,
                    "available_ns": t_ns + 20_000_000,
                    "engine_seq": i + 1000,
                    "correlation_id": f"b-{i // 10}",
                    "causality_token": f"tok-bar-{i}",
                    "payload": {
                        "start_ns": bar_start,
                        "end_ns": t_ns,
                        "open_ticks": int(current_price - delta * 5),
                        "high_ticks": int(current_price + 30),
                        "low_ticks": int(current_price - 30),
                        "close_ticks": int(current_price),
                        "volume_lots": 1000,
                        "synthetic": True,
                    },
                }
            )

        # Generate ML rule candidates at decision points
        if i % 2 == 0 and i < total_steps - 10:
            side = "BUY" if (i // 15) % 2 == 0 else "SELL"
            candidates.append(
                {
                    "id": f"cand-{i}",
                    "decision_ns": t_ns,
                    "side": side,
                    "features": {
                        "spread_bps": 0.4,
                        "mid": current_price,
                        "momentum_10": delta,
                        "volume_signed": 50.0 if side == "BUY" else -50.0,
                    },
                    "features_hash": f"hash-{i}",
                }
            )

    # 2. Build Causal Labels per §13.1
    labeled_rows = LabelBuilder.build(
        candidates=candidates,
        market_prices=market_quotes,
        horizon_ns=5 * interval_ns,
        taker_fee_rate=Decimal("0.00055"),
        funding_rate=Decimal("0.0001"),
        half_spread_ticks=Decimal("1"),
        latency_ns=20_000_000,
    )

    # Verify both labels are present
    pos_labels = sum(1 for r in labeled_rows if r.is_profitable and r.dropped_reason is None)
    neg_labels = sum(1 for r in labeled_rows if not r.is_profitable and r.dropped_reason is None)
    assert pos_labels > 0, "Demo fixture must contain positive labels"
    assert neg_labels > 0, "Demo fixture must contain negative labels"

    # 3. Build Purged Walk-Forward Folds per §13.2 (3 forward validation folds)
    timestamps = [r.decision_ns for r in labeled_rows if r.dropped_reason is None]
    splitter = PurgedWalkForward(n_folds=3, holdout_pct=0.20, min_train_pct=0.50)
    folds = splitter.split(timestamps, label_horizon_ns=5 * interval_ns, delivery_uncertainty_ns=0)
    assert len(folds) == 3, f"Expected 3 folds, got {len(folds)}"

    # 4. Build TrainingDataset
    dataset = DatasetBuilder.build(
        rows=labeled_rows,
        folds=folds,
        metadata={
            "origin": "demo-only",
            "synthetic": True,
            "warning": (
                "Synthetic demo fixture only. Never use to establish live "
                "profitability or readiness."
            ),
            "total_candidates": len(candidates),
            "valid_rows": len([r for r in labeled_rows if r.dropped_reason is None]),
            "positive_labels": pos_labels,
            "negative_labels": neg_labels,
            "n_folds": len(folds),
        },
    )

    # 5. Save Demo Fixture Artifacts
    events_path = output_dir / "demo_market_events.json"
    events_path.write_text(json.dumps(events, indent=2))

    dataset_path = output_dir / "demo_training_dataset.json"
    dataset_dict = {
        "metadata": dataset.metadata,
        "feature_names": dataset.feature_names,
        "features_matrix": dataset.features_matrix,
        "target_labels": dataset.target_labels,
        "folds": [
            {
                "fold_index": f.fold_index,
                "train_indices": f.train_indices,
                "val_indices": f.val_indices,
                "holdout_indices": f.holdout_indices,
                "purged_indices": f.purged_indices,
                "val_start_ns": f.val_start_ns,
                "val_end_ns": f.val_end_ns,
            }
            for f in dataset.folds
        ],
        "rows": [
            {
                "candidate_id": r.candidate_id,
                "decision_ns": r.decision_ns,
                "side": r.side,
                "entry_price": str(r.entry_price),
                "exit_price": str(r.exit_price),
                "net_return": str(r.net_return),
                "is_profitable": r.is_profitable,
                "features": r.features,
                "dropped_reason": r.dropped_reason,
            }
            for r in dataset.rows
        ],
    }
    dataset_path.write_text(json.dumps(dataset_dict, indent=2))

    return {
        "events_path": str(events_path),
        "dataset_path": str(dataset_path),
        "events_count": str(len(events)),
        "rows_count": str(len(labeled_rows)),
        "pos_count": str(pos_labels),
        "neg_count": str(neg_labels),
        "folds_count": str(len(folds)),
    }


def main():
    print("Generating demo-only fixtures for ML pipeline tests...")
    info = generate_demo_fixtures()
    print(f"Generated {info['events_count']} market events -> {info['events_path']}")
    print(
        f"Generated {info['rows_count']} rows "
        f"(pos={info['pos_count']}, neg={info['neg_count']}, folds={info['folds_count']}) "
        f"-> {info['dataset_path']}"
    )
    print("DEMO_FIXTURE_GENERATION_SUCCESS")


if __name__ == "__main__":
    main()
