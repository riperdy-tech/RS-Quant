"""Unit tests for LightGBM Meta-Labeler and Triple Barrier Method."""

import numpy as np
import pytest

from quantdesk.features.ensemble_features import CuratedEnsembleExtractor, EnsembleBarState
from quantdesk.research.meta_labeler import (
    META_FEATURE_NAMES,
    extract_meta_features,
    label_breakouts_triple_barrier,
    train_meta_labeler_model,
)


def generate_simulated_states(n: int = 80) -> list[EnsembleBarState]:
    """Generates synthetic EnsembleBarState sequence with breakout triggers."""
    np.random.seed(42)
    t = np.arange(n)
    close = 100.0 + 0.5 * t + np.sin(t / 4.0) * 3.0
    high = close + 1.5
    low = close - 1.5
    open_p = (high + low) / 2.0
    volume = np.random.uniform(200.0, 800.0, n)
    timestamps = 1700000000 + t * 7200

    extractor = CuratedEnsembleExtractor()
    return extractor.compute_all(timestamps, open_p, high, low, close, volume)


def test_extract_meta_features():
    states = generate_simulated_states(30)
    feat_dict = extract_meta_features(states[-1])
    assert set(feat_dict.keys()) == set(META_FEATURE_NAMES)
    for k, v in feat_dict.items():
        assert isinstance(v, float)
        assert np.isfinite(v)


def test_triple_barrier_labeling():
    states = generate_simulated_states(70)
    # Manually ensure some bars have breakout triggers
    for i in [20, 30, 40, 50]:
        states[i].is_donchian_breakout_long = True
        states[i].rounded_score = 7

    X, y = label_breakouts_triple_barrier(states, horizon_bars=10)
    assert len(X) > 0
    assert len(X) == len(y)
    assert X.shape[1] == len(META_FEATURE_NAMES)
    assert set(np.unique(y)).issubset({0, 1})


def test_train_meta_labeler_model():
    np.random.seed(42)
    # 40 samples, 14 features
    X = np.random.normal(0, 1.0, (40, len(META_FEATURE_NAMES)))
    y = (X[:, 0] + X[:, 1] > 0).astype(int)

    predictor = train_meta_labeler_model(X, y, num_leaves=7, min_child_samples=5)
    assert predictor.manifest.algorithm == "lightgbm"
    assert predictor.manifest.status == "ACTIVE"

    # Test inference
    sample_feat = {name: float(X[0, i]) for i, name in enumerate(META_FEATURE_NAMES)}
    prob = predictor.predict_proba(sample_feat)
    assert 0.0 <= prob <= 1.0

    prediction = predictor.predict(sample_feat, threshold=0.55)
    assert isinstance(prediction.decision, bool)
    assert float(prediction) == prob
