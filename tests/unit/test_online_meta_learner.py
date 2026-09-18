from __future__ import annotations

import pytest
from quantdesk.research.online_meta_learner import OnlineMetaLearner, META_FEATURE_NAMES


def test_online_meta_learner_initialization():
    learner = OnlineMetaLearner(instrument_id="BTCUSDT", min_train_episodes=5, retrain_interval=2)
    assert learner.instrument_id == "BTCUSDT"
    assert learner.model is not None
    assert len(learner.feature_names) == 12
    assert learner.feature_names == META_FEATURE_NAMES
    assert learner.total_retrains == 0


def test_online_meta_learner_extract_features():
    learner = OnlineMetaLearner(instrument_id="BTCUSDT")
    raw = {
        "rqk_value": 60000.0,
        "mcginley_value": 60100.0,
        "squeeze_val": 15.0,
        "squeeze_color": "BLUE",
        "cmf_value": 0.12,
        "stc_value": 75.0,
        "qqe_line": 5.0,
        "adx_value": 28.0,
        "chandelier_long_stop": 59500.0,
        "volume_zscore": 1.45,
        "depth5_imbalance": 0.35,
        "spread_bps": 0.12,
    }
    feats = learner.extract_features(raw, mid=60200.0, atr=250.0)
    assert len(feats) == 12
    assert all(isinstance(f, float) for f in feats)
    # Check squeeze_color BLUE -> 1.0
    assert feats[3] == 1.0


def test_online_meta_learner_predict():
    learner = OnlineMetaLearner(instrument_id="BTCUSDT")
    raw = {
        "rqk_value": 60000.0,
        "mcginley_value": 60100.0,
        "squeeze_val": 15.0,
        "squeeze_color": "BLUE",
        "volume_zscore": 1.5,
    }
    feats = learner.extract_features(raw, mid=60200.0, atr=250.0)
    prob = learner.predict_win_probability(feats)
    assert 0.01 <= prob <= 0.99
    assert isinstance(prob, float)


def test_online_meta_learner_retrain_trigger():
    learner = OnlineMetaLearner(instrument_id="BTCUSDT", min_train_episodes=4, retrain_interval=2)
    sample_feats = [0.01, 1.01, 0.1, 1.0, 0.15, 0.8, 0.05, 0.3, 0.02, 1.2, 0.4, 0.1]

    # Push 3 episodes (2 wins, 1 loss)
    assert not learner.record_episode(sample_feats, net_pnl=15.0, now_ns=1000)
    assert not learner.record_episode(sample_feats, net_pnl=-10.0, now_ns=2000)
    assert not learner.record_episode(sample_feats, net_pnl=25.0, now_ns=3000)
    assert learner.total_retrains == 0

    # 4th episode: min_train_episodes reached, episodes_since_retrain = 4 >= 2 -> retrain triggered!
    retrained = learner.record_episode(sample_feats, net_pnl=-5.0, now_ns=4000)
    assert retrained is True
    assert learner.total_retrains == 1
    assert 0.0 <= learner.last_accuracy <= 1.0
