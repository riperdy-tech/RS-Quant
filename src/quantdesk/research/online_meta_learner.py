"""Tier 2 Online Streaming Machine Learning Meta-Labeler.

Maintains an online LightGBM binary classification model predicting
P(Win | Live Market State). Continuously retrains from real closed trade
episodes in an online replay buffer.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import logging
import numpy as np
from typing import Any
import lightgbm as lgb

logger = logging.getLogger("quantdesk.online_meta_learner")

META_FEATURE_NAMES = [
    "rqk_dev_pct",
    "mcginley_ratio",
    "squeeze_val_norm",
    "squeeze_color_code",
    "cmf_value",
    "stc_norm",
    "qqe_line_norm",
    "adx_norm",
    "chandelier_dist_pct",
    "volume_zscore",
    "depth5_imbalance",
    "spread_bps",
]


@dataclass
class OnlineEpisodeRecord:
    features: list[float]
    label: int  # 1 for profitable, 0 for loss
    net_pnl: float
    timestamp_ns: int


class OnlineMetaLearner:
    """Online incremental LightGBM meta-learner scoring trade entries and updating online."""

    def __init__(self, instrument_id: str, min_train_episodes: int = 10, retrain_interval: int = 5) -> None:
        self.instrument_id = instrument_id
        self.min_train_episodes = min_train_episodes
        self.retrain_interval = retrain_interval
        self.replay_buffer: deque[OnlineEpisodeRecord] = deque(maxlen=200)
        self.episodes_since_retrain = 0
        self.model: lgb.Booster | None = None
        self.total_retrains = 0
        self.last_accuracy: float = 0.50
        self.feature_names = list(META_FEATURE_NAMES)

        # Seed initial synthetic bootstrap prior so model can predict immediately
        self._seed_bootstrap_model()

    def _seed_bootstrap_model(self) -> None:
        """Trains an initial conservative prior model so predict_win_probability works on tick 1."""
        np.random.seed(42)
        n_samples = 60
        # Synthetic features around normalized range
        X = np.random.normal(0.0, 0.5, size=(n_samples, len(self.feature_names)))
        # Target: positive when volume_zscore > 0 and squeeze_color > 0
        y = ((X[:, 3] > 0.0) & (X[:, 9] > -0.2)).astype(int)

        train_data = lgb.Dataset(X, label=y, feature_name=self.feature_names, free_raw_data=False)
        params = {
            "objective": "binary",
            "metric": "binary_logloss",
            "boosting_type": "gbdt",
            "num_leaves": 7,
            "learning_rate": 0.05,
            "min_data_in_leaf": 3,
            "verbose": -1,
        }
        self.model = lgb.train(params, train_data, num_boost_round=15)

    def extract_features(self, raw_features: dict[str, Any], mid: float, atr: float) -> list[float]:
        """Converts raw strategy features into normalized continuous vector."""
        close = mid if mid > 0 else 1.0
        atr_val = atr if atr > 0 else 1.0

        rqk = float(raw_features.get("rqk_value", close))
        mcg = float(raw_features.get("mcginley_value", close))
        sq_val = float(raw_features.get("squeeze_val", 0.0))
        sq_color = raw_features.get("squeeze_color", "BLUE")
        color_code = 1.0 if sq_color == "BLUE" else (0.5 if sq_color == "ORANGE" else (-0.5 if sq_color == "GREEN" else -1.0))
        cmf = float(raw_features.get("cmf_value", 0.0) or 0.0)
        stc = float(raw_features.get("stc_value", 50.0) or 50.0)
        qqe = float(raw_features.get("qqe_line", 0.0) or 0.0)
        adx = float(raw_features.get("adx_value", 20.0) or 20.0)
        ch_stop = float(raw_features.get("chandelier_long_stop", close - atr_val) or (close - atr_val))
        z_vol = float(raw_features.get("volume_zscore", 0.0) or 0.0)
        d5 = float(raw_features.get("depth5_imbalance", 0.0) or 0.0)
        spread = float(raw_features.get("spread_bps", 0.15) or 0.15)

        return [
            float((close - rqk) / close),
            float(close / mcg if mcg > 0 else 1.0),
            float(sq_val / atr_val),
            float(color_code),
            float(cmf),
            float(stc / 100.0),
            float(qqe / atr_val),
            float(adx / 100.0),
            float(abs(close - ch_stop) / close),
            float(z_vol),
            float(d5),
            float(spread),
        ]

    def predict_win_probability(self, features: list[float]) -> float:
        """Predicts probability P(Win | Microstructure) in range [0.0, 1.0]."""
        if self.model is None:
            return 0.50
        x_arr = np.array([features], dtype=np.float32)
        preds = self.model.predict(x_arr)
        return float(np.clip(preds[0], 0.01, 0.99))

    def record_episode(self, features: list[float], net_pnl: float, now_ns: int) -> bool:
        """Records a closed trade outcome and triggers online incremental retraining when due."""
        label = 1 if net_pnl > 0 else 0
        self.replay_buffer.append(OnlineEpisodeRecord(
            features=features,
            label=label,
            net_pnl=net_pnl,
            timestamp_ns=now_ns,
        ))
        self.episodes_since_retrain += 1

        # Check if ready for incremental online retrain
        if len(self.replay_buffer) >= self.min_train_episodes and self.episodes_since_retrain >= self.retrain_interval:
            self._retrain_online()
            self.episodes_since_retrain = 0
            return True
        return False

    def _retrain_online(self) -> None:
        """Runs incremental warm-start retraining over the rolling replay buffer."""
        X = np.array([ep.features for ep in self.replay_buffer], dtype=np.float32)
        y = np.array([ep.label for ep in self.replay_buffer], dtype=np.int32)

        # Skip if single-class in buffer
        if len(np.unique(y)) < 2:
            return

        train_data = lgb.Dataset(X, label=y, feature_name=self.feature_names, free_raw_data=False)
        params = {
            "objective": "binary",
            "metric": "binary_logloss",
            "boosting_type": "gbdt",
            "num_leaves": 7,
            "learning_rate": 0.08,
            "min_data_in_leaf": 2,
            "verbose": -1,
        }
        # Incremental retrain using warm-start tree additions
        self.model = lgb.train(params, train_data, num_boost_round=10, init_model=self.model)
        self.total_retrains += 1

        # Update in-sample training accuracy
        preds = (self.model.predict(X) >= 0.5).astype(int)
        self.last_accuracy = float(np.mean(preds == y))
        logger.info(
            f"OnlineMetaLearner updated for {self.instrument_id}: "
            f"Retrain #{self.total_retrains}, Buffer={len(self.replay_buffer)}, In-Sample Acc={self.last_accuracy:.1%}"
        )
