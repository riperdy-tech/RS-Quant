"""LightGBM Meta-Labeling for Curated Ensemble Breakouts.

Applies Marcos López de Prado's Triple Barrier Method to label breakout events
from the Curated Ensemble Strategy and trains a bounded LightGBM binary
classifier to predict breakout follow-through probability P(success | X).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

import lightgbm as lgb
import numpy as np

from quantdesk.features.ensemble_features import EnsembleBarState, SqueezeColor
from quantdesk.research.train import ModelManifest, Predictor


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
    "hv_annualized",
    "volume_ratio",
    "score_norm",
    "score_slope",
    "score_sma15_norm",
]


def extract_meta_features(state: EnsembleBarState) -> dict[str, float]:
    """Extracts normalized continuous feature representation for LightGBM meta-labeling."""
    close = state.close if state.close > 0 else 1.0
    atr = state.atr_14 if state.atr_14 > 0 else 1.0

    color_map = {
        SqueezeColor.BLUE: 1.0,
        SqueezeColor.ORANGE: 0.5,
        SqueezeColor.GREEN: -0.5,
        SqueezeColor.RED: -1.0,
    }

    return {
        "rqk_dev_pct": float((close - state.rqk_value) / close),
        "mcginley_ratio": float(close / (state.mcginley_value if state.mcginley_value > 0 else close)),
        "squeeze_val_norm": float(state.squeeze_val / atr),
        "squeeze_color_code": float(color_map.get(state.squeeze_color, 0.0)),
        "cmf_value": float(state.cmf_value),
        "stc_norm": float(state.stc_value / 100.0),
        "qqe_line_norm": float(state.qqe_line / atr),
        "adx_norm": float(state.adx_value / 100.0),
        "chandelier_dist_pct": float(abs(close - state.long_stop) / close),
        "hv_annualized": float(state.hv_annualized),
        "volume_ratio": float(state.volume_ratio),
        "score_norm": float(state.raw_score / 10.0),
        "score_slope": float(state.score_slope),
        "score_sma15_norm": float(state.trailing_score_sma15 / 10.0),
    }


def label_breakouts_triple_barrier(
    states: Sequence[EnsembleBarState],
    horizon_bars: int = 12,
    tp_atr_mult: float = 2.5,
    sl_atr_mult: float = 2.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Labels Donchian breakout candidates using the Triple Barrier Method.

    Barrier 1 (Upper): Entry + tp_atr_mult * ATR (for Long)
    Barrier 2 (Lower): Entry - sl_atr_mult * ATR (for Long)
    Barrier 3 (Time): Expired after horizon_bars

    Returns:
        (X, y) where X is (N, len(META_FEATURE_NAMES)) and y is binary {0, 1}.
    """
    n = len(states)
    x_rows: list[list[float]] = []
    y_labels: list[int] = []

    for t in range(n - horizon_bars):
        st = states[t]
        # Only label bars where a Donchian breakout candidate occurred
        is_long = st.is_donchian_breakout_long and st.rounded_score >= 5
        is_short = st.is_donchian_breakout_short and st.rounded_score <= 5

        if not (is_long or is_short):
            continue

        entry_price = st.close
        atr = st.atr_14 if st.atr_14 > 0 else 1.0
        label = 0

        if is_long:
            tp = entry_price + tp_atr_mult * atr
            sl = entry_price - sl_atr_mult * atr
            # Scan future bars
            for f in range(1, horizon_bars + 1):
                future_bar = states[t + f]
                if future_bar.high >= tp:
                    label = 1
                    break
                if future_bar.low <= sl:
                    label = 0
                    break
        elif is_short:
            tp = entry_price - tp_atr_mult * atr
            sl = entry_price + sl_atr_mult * atr
            for f in range(1, horizon_bars + 1):
                future_bar = states[t + f]
                if future_bar.low <= tp:
                    label = 1
                    break
                if future_bar.high >= sl:
                    label = 0
                    break

        feat_dict = extract_meta_features(st)
        x_rows.append([feat_dict[k] for k in META_FEATURE_NAMES])
        y_labels.append(label)

    if not x_rows:
        return np.empty((0, len(META_FEATURE_NAMES))), np.empty((0,), dtype=int)

    return np.array(x_rows, dtype=float), np.array(y_labels, dtype=int)


def train_meta_labeler_model(
    x_train: np.ndarray,
    y_train: np.ndarray,
    seed: int = 42,
    num_leaves: int = 15,
    max_depth: int = 4,
    learning_rate: float = 0.03,
    min_child_samples: int = 10,
    model_id: str = "lgbm-meta-ensemble-v1",
) -> Predictor:
    """Trains a bounded LightGBM binary classifier on breakout meta-labels."""
    if len(x_train) == 0:
        raise ValueError("Cannot train meta-labeler on empty dataset")

    # If all labels are identical, train simple constant or majority baseline
    unique_labels = np.unique(y_train)
    if len(unique_labels) < 2:
        maj_prob = 1.0 if unique_labels[0] == 1 else 0.0
        manifest = ModelManifest(
            model_id=model_id,
            algorithm="majority_constant",
            feature_names=META_FEATURE_NAMES,
            performance_metrics={"training_accuracy": 1.0},
            model_payload_hash=hashlib.sha256(b"majority").hexdigest(),
            status="ACTIVE",
        )
        return Predictor(manifest=manifest, majority_prob=maj_prob)

    train_data = lgb.Dataset(x_train, label=y_train, feature_name=META_FEATURE_NAMES)
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "boosting_type": "gbdt",
        "num_leaves": num_leaves,
        "max_depth": max_depth,
        "learning_rate": learning_rate,
        "min_child_samples": min_child_samples,
        "seed": seed,
        "verbose": -1,
    }

    booster = lgb.train(params, train_data, num_boost_round=50)
    model_str = booster.model_to_string()
    model_hash = hashlib.sha256(model_str.encode("utf-8")).hexdigest()

    # In-sample evaluation
    preds = booster.predict(x_train)
    binary_preds = (preds >= 0.5).astype(int)
    acc = float(np.mean(binary_preds == y_train))

    manifest = ModelManifest(
        model_id=model_id,
        algorithm="lightgbm",
        feature_names=META_FEATURE_NAMES,
        hyperparameters=params,
        performance_metrics={"in_sample_accuracy": acc},
        model_payload_hash=model_hash,
        native_model_text=model_str,
        status="ACTIVE",
    )

    return Predictor(manifest=manifest, native_booster=booster)
