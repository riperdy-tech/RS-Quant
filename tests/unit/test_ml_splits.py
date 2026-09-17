from decimal import Decimal

from quantdesk.research.datasets import DatasetBuilder, TrainOnlyScaler
from quantdesk.research.evaluate import ModelEvaluator
from quantdesk.research.labels import LabelBuilder
from quantdesk.research.splits import PurgedWalkForward


def test_purged_walk_forward():
    # 100 timestamps, 1s apart
    ts = [i * 1_000_000_000 for i in range(100)]

    pwf = PurgedWalkForward(n_folds=2, holdout_pct=0.20, min_train_pct=0.50)
    # label horizon 5s
    folds = pwf.split(ts, label_horizon_ns=5_000_000_000)

    assert len(folds) == 2

    # 20% holdout -> 20 rows
    assert len(folds[0].holdout_indices) == 20
    assert folds[0].holdout_indices == list(range(80, 100))

    # dev set is 80 rows
    # train min 50% = 40 rows
    # 2 folds -> remaining 40 rows divided by 2 = 20 validation rows per fold

    # Fold 1: train ends at 40, val is 40-60
    # Val start is ts[40] = 40s
    # purge cutoff = 40s - 5s = 35s
    # train indices should be those < 35s, so 0 to 34
    assert folds[0].val_indices == list(range(40, 60))
    assert len(folds[0].train_indices) == 35
    assert folds[0].train_indices[-1] == 34

    # Fold 2: train ends at 60, val is 60-80
    # Val start is ts[60] = 60s
    # purge cutoff = 60s - 5s = 55s
    assert folds[1].val_indices == list(range(60, 80))
    assert len(folds[1].train_indices) == 55
    assert folds[1].train_indices[-1] == 54


def test_purged_walk_forward_three_plus_folds_and_zero_leakage():
    # 200 timestamps at 1-second intervals
    ts = [i * 1_000_000_000 for i in range(200)]
    horizon_ns = 5_000_000_000
    uncertainty_ns = 1_000_000_000

    # 3 forward validation folds, 20% holdout, 50% min train window per §13.2
    pwf = PurgedWalkForward(n_folds=3, holdout_pct=0.20, min_train_pct=0.50)
    folds = pwf.split(
        ts,
        label_horizon_ns=horizon_ns,
        delivery_uncertainty_ns=uncertainty_ns,
    )

    assert len(folds) == 3

    # Total rows = 200. Holdout = 40 (last 20%: 160 to 199)
    holdout_set = set(range(160, 200))
    for f in folds:
        assert set(f.holdout_indices) == holdout_set
        # Zero holdout leakage into train or val
        assert len(holdout_set.intersection(f.train_indices)) == 0
        assert len(holdout_set.intersection(f.val_indices)) == 0

        # Zero train label overlap into validation
        purge_limit = f.val_start_ns - (horizon_ns + uncertainty_ns)
        for idx in f.train_indices:
            assert ts[idx] < purge_limit, f"Train row {idx} overlaps validation window!"


def test_purged_walk_forward_irregular_ticks():
    # Irregular event arrival times
    irregular_ts = [
        1000,
        1500,
        2100,
        3400,
        4200,
        5000,
        6800,
        7500,
        8900,
        10000,
        11200,
        12500,
        13100,
        14000,
        15600,
        16200,
        17500,
        18900,
        19500,
        20000,
    ]
    horizon_ns = 1500
    pwf = PurgedWalkForward(n_folds=2, holdout_pct=0.20, min_train_pct=0.50)
    folds = pwf.split(irregular_ts, label_horizon_ns=horizon_ns)

    assert len(folds) == 2
    for f in folds:
        for train_idx in f.train_indices:
            assert irregular_ts[train_idx] + horizon_ns <= f.val_start_ns


def test_purged_walk_forward_insufficient_data():
    ts = [1000, 2000, 3000]
    pwf = PurgedWalkForward(n_folds=3, holdout_pct=0.20, min_train_pct=0.50)
    folds = pwf.split(ts, label_horizon_ns=1000)
    assert folds == ()


def test_label_builder_conservative_marketable_entry_exit():
    quotes = {
        1000: (99.0, 101.0),  # bid=99, ask=101
        2000: (109.0, 111.0),  # bid=109, ask=111
    }
    # Long trade candidate at t=0, latency=1000 -> entry at t=1000 ask=101, exit at t=2000 bid=109
    candidates = [
        {
            "id": "cand-long",
            "decision_ns": 0,
            "side": "BUY",
            "features": {"f1": 1.5, "target_leak": 999.0},
            "features_hash": "h-long",
        }
    ]
    rows = LabelBuilder.build(
        candidates=candidates,
        market_prices=quotes,
        horizon_ns=2000,
        taker_fee_rate=Decimal("0.001"),  # 10 bps
        funding_rate=Decimal("0"),
        latency_ns=1000,
    )
    assert len(rows) == 1
    r = rows[0]
    assert r.entry_price == Decimal("101.0")
    assert r.exit_price == Decimal("109.0")
    # gross_diff = 109 - 101 = 8
    # entry_fee = 101 * 0.001 = 0.101
    # exit_fee = 109 * 0.001 = 0.109
    # net = 8 - 0.101 - 0.109 = 7.790
    assert r.net_return == Decimal("7.790")
    assert r.is_profitable is True
    # Verify target leakage stripped from sanitized features
    assert "target_leak" not in r.features
    assert "f1" in r.features


def test_label_builder_quality_drop_reasons():
    # Only quote at t=1000, missing quote at horizon t=5000
    quotes = {1000: (100.0, 102.0)}
    candidates = [
        {
            "id": "cand-gap",
            "decision_ns": 0,
            "side": "BUY",
            "features": {"f": 1.0},
            "features_hash": "h-gap",
        }
    ]
    rows = LabelBuilder.build(
        candidates=candidates,
        market_prices=quotes,
        horizon_ns=5000,
        latency_ns=1000,
        max_staleness_ns=2000,
    )
    assert len(rows) == 1
    assert rows[0].dropped_reason == "DROPPED_UNAVAILABLE_EXIT_QUOTE"


def test_dataset_builder_and_train_only_scaling():
    ts = [i * 1_000_000_000 for i in range(100)]
    quotes = {t: (100.0 + i, 102.0 + i) for i, t in enumerate(ts)}
    candidates = [
        {
            "id": f"c-{i}",
            "decision_ns": ts[i],
            "side": "BUY" if i % 2 == 0 else "SELL",
            "features": {"vol": float(i * 10), "spread": 2.0},
            "features_hash": f"h-{i}",
        }
        for i in range(90)
    ]
    labeled_rows = LabelBuilder.build(
        candidates=candidates,
        market_prices=quotes,
        horizon_ns=5_000_000_000,
        latency_ns=1_000_000_000,
    )
    valid_rows = [r for r in labeled_rows if r.dropped_reason is None]
    splitter = PurgedWalkForward(n_folds=3, holdout_pct=0.20, min_train_pct=0.50)
    folds = splitter.split([r.decision_ns for r in valid_rows], label_horizon_ns=5_000_000_000)

    dataset = DatasetBuilder.build(rows=valid_rows, folds=folds)
    assert len(dataset.features_matrix) == len(valid_rows)
    assert dataset.feature_names == ["spread", "vol"]

    # Fit TrainOnlyScaler strictly on training indices of fold 0
    scaler = TrainOnlyScaler()
    scaler.fit(dataset.features_matrix, folds[0].train_indices)
    assert scaler.is_fitted

    # Transform training and validation rows
    scaled_train = scaler.transform(dataset.features_matrix, folds[0].train_indices)
    scaled_val = scaler.transform(dataset.features_matrix, folds[0].val_indices)
    assert len(scaled_train) == len(folds[0].train_indices)
    assert len(scaled_val) == len(folds[0].val_indices)

    # Train mean of feature 1 (vol) should be ~0 after standard scaling
    train_vol_scaled = [row[1] for row in scaled_train]
    mean_vol = sum(train_vol_scaled) / len(train_vol_scaled)
    assert abs(mean_vol) < 1e-6


def test_model_evaluator_metrics():
    preds = [0.9, 0.8, 0.2, 0.1, 0.7]
    truth = [1, 1, 0, 0, 0]  # 4 correct, 1 FP (0.7 pred vs 0 truth)

    metrics = ModelEvaluator.evaluate(preds, truth)
    assert "log_loss" in metrics
    assert "brier_score" in metrics
    assert metrics["accuracy"] == 0.8
    assert abs(metrics["precision"] - 2 / 3) < 1e-3  # TP=2, FP=1 -> 0.6667
    assert metrics["recall"] == 1.0  # TP=2, FN=0 -> 1.0
    assert metrics["log_loss"] > 0.0
    assert metrics["brier_score"] > 0.0
