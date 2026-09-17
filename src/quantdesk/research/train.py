from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]

from quantdesk.research.datasets import TrainingDataset
from quantdesk.research.evaluate import ModelEvaluator


@dataclass
class ModelManifest:
    """Immutable model specification and performance manifest per §13.2 and §13.3."""

    model_id: str
    algorithm: str
    feature_names: list[str] = field(default_factory=list)
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    feature_schema_hash: str = ""
    train_dataset_hash: str = ""
    performance_metrics: dict[str, float] = field(default_factory=dict)
    model_payload_hash: str = ""
    native_model_text: str = ""
    dataset_origin: str = "demo-only"
    status: str = "TRAINING"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelManifest:
        return cls(**data)


@dataclass
class Prediction:
    """Model inference result with class probability and decision flag per §13.2."""

    probability: float
    decision: bool = False
    model_id: str = ""
    model_payload_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __float__(self) -> float:
        return float(self.probability)

    def __int__(self) -> int:
        return 1 if self.decision else 0

    def __ge__(self, other: Any) -> bool:
        return self.probability >= float(other)

    def __le__(self, other: Any) -> bool:
        return self.probability <= float(other)

    def __gt__(self, other: Any) -> bool:
        return self.probability > float(other)

    def __lt__(self, other: Any) -> bool:
        return self.probability < float(other)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Prediction):
            return self.probability == other.probability and self.model_id == other.model_id
        try:
            return self.probability == float(other)
        except (ValueError, TypeError):
            return False


class Predictor:
    """Runtime model inference wrapper ensuring schema compatibility
    and feature alignment (§13.2).
    """

    def __init__(
        self,
        manifest: ModelManifest,
        native_booster: lgb.Booster | None = None,
        sklearn_model: Any | None = None,
        majority_prob: float | None = None,
    ) -> None:
        self.manifest = manifest
        self.feature_names = manifest.feature_names
        self.model_id = manifest.model_id
        self.model_hash = manifest.model_payload_hash
        self._booster = native_booster
        self._sklearn_model = sklearn_model
        self._majority_prob = majority_prob

        # If booster text is provided in manifest and booster not initialized, load it
        if (
            self._booster is None
            and manifest.algorithm == "lightgbm"
            and manifest.native_model_text
        ):
            self._booster = lgb.Booster(model_str=manifest.native_model_text)

    def predict(
        self,
        features: dict[str, float] | list[float],
        threshold: float = 0.5,
    ) -> Prediction:
        """Predicts positive class probability [0.0, 1.0] with strict feature order alignment."""
        if isinstance(features, dict):
            # Align features strictly by feature_names to prevent dictionary reordering bugs
            x_vec = [float(features.get(f_name, 0.0)) for f_name in self.feature_names]
        else:
            x_vec = [float(val) for val in features]

        raw_prob: float = 0.5
        if self._booster is not None:
            raw_prob = float(self._booster.predict([x_vec])[0])
        elif self._sklearn_model is not None:
            raw_prob = float(self._sklearn_model.predict_proba([x_vec])[0][1])
        elif self._majority_prob is not None:
            raw_prob = float(self._majority_prob)

        return Prediction(
            probability=raw_prob,
            decision=raw_prob >= threshold,
            model_id=self.model_id,
            model_payload_hash=self.model_hash,
        )

    def predict_proba(self, features: dict[str, float] | list[float]) -> float:
        return float(self.predict(features))


class Trainer:
    """Trains baseline models and bounded LightGBM binary classifiers per §13.2."""

    @staticmethod
    def run(spec: dict[str, Any]) -> ModelManifest:
        """Runs a bounded training pipeline according to the provided specification."""
        algorithm = spec.get("algorithm", "lightgbm")
        dataset = spec["dataset"]
        schema_hash = spec.get("feature_schema_hash", "default_schema")
        seed = int(spec.get("seed", 42))

        if algorithm == "lightgbm":
            num_leaves = int(spec.get("num_leaves", 7))
            min_child_samples = int(spec.get("min_child_samples", 100))
            predictor, manifest = Trainer.train_lightgbm(
                dataset=dataset,
                feature_schema_hash=schema_hash,
                seed=seed,
                num_leaves=num_leaves,
                min_child_samples=min_child_samples,
            )
        elif algorithm in ("logistic", "logistic_regression"):
            predictor, manifest = Trainer.train_logistic(
                dataset=dataset,
                feature_schema_hash=schema_hash,
                seed=seed,
            )
        elif algorithm in ("majority", "majority_class"):
            predictor, manifest = Trainer.train_majority(
                dataset=dataset,
                feature_schema_hash=schema_hash,
            )
        else:
            raise ValueError(f"Unknown training algorithm: {algorithm}")

        registry = spec.get("registry")
        if registry is not None:
            registry.register(predictor, manifest)

        return manifest

    @staticmethod
    def train_lightgbm(
        dataset: TrainingDataset,
        feature_schema_hash: str,
        seed: int = 42,
        num_leaves: int = 7,
        min_child_samples: int = 100,
    ) -> tuple[Predictor, ModelManifest]:
        # 1. Dataset validation: check sufficient data and both classes present
        if not dataset.rows or not dataset.features_matrix:
            raise ValueError("INSUFFICIENT_DATA: empty training dataset")

        unique_targets = set(dataset.target_labels)
        if len(unique_targets) < 2:
            counts = {label: dataset.target_labels.count(label) for label in unique_targets}
            raise ValueError(
                f"INSUFFICIENT_DATA: both classes required for binary training: {counts}"
            )

        # 2. Hyperparameter bounds enforcement (§13.2)
        if num_leaves not in (7, 15):
            raise ValueError(f"num_leaves must be in bounded set {{7, 15}}, got {num_leaves}")

        # Ensure min_child_samples allows splitting on smaller development fixtures
        n_samples = len(dataset.target_labels)
        effective_min_child = max(5, min(min_child_samples, n_samples // 4))

        hyperparameters = {
            "num_leaves": num_leaves,
            "max_depth": 4,
            "learning_rate": 0.03,
            "n_estimators": 300,
            "min_child_samples": effective_min_child,
            "reg_lambda": 1.0,
            "is_unbalance": False,
            "random_state": seed,
            "n_jobs": 1,
            "force_col_wise": True,
            "objective": "binary",
            "verbosity": -1,
        }

        # 3. Determine train/validation split indices from folds
        train_idx = list(range(n_samples))
        val_idx: list[int] = []
        if dataset.folds:
            fold0 = dataset.folds[0]
            train_idx = fold0.train_indices
            val_idx = fold0.val_indices

        x_train = np.asarray([dataset.features_matrix[i] for i in train_idx], dtype=np.float64)
        y_train = np.asarray([dataset.target_labels[i] for i in train_idx], dtype=np.int32)

        x_val = (
            np.asarray([dataset.features_matrix[i] for i in val_idx], dtype=np.float64)
            if val_idx
            else x_train
        )
        y_val = (
            np.asarray([dataset.target_labels[i] for i in val_idx], dtype=np.int32)
            if val_idx
            else y_train
        )

        # 4. Train LightGBM Booster
        train_data = lgb.Dataset(x_train, label=y_train, feature_name=dataset.feature_names)
        val_data = lgb.Dataset(
            x_val, label=y_val, reference=train_data, feature_name=dataset.feature_names
        )

        booster = lgb.train(
            params={
                "objective": "binary",
                "num_leaves": num_leaves,
                "max_depth": 4,
                "learning_rate": 0.03,
                "min_child_samples": effective_min_child,
                "lambda_l2": 1.0,
                "is_unbalance": False,
                "seed": seed,
                "num_threads": 1,
                "force_col_wise": True,
                "verbosity": -1,
            },
            train_set=train_data,
            num_boost_round=min(300, max(50, n_samples)),
            valid_sets=[val_data],
            callbacks=[lgb.early_stopping(stopping_rounds=20, verbose=False)],
        )

        # 5. Evaluate Out-of-Sample Performance (§13.3)
        val_preds = [float(p) for p in booster.predict(x_val)]
        performance_metrics = ModelEvaluator.evaluate(val_preds, y_val.tolist())

        # 6. Serialize native text model and compute hashes
        native_text = booster.model_to_string()
        payload_hash = hashlib.sha256(native_text.encode("utf-8")).hexdigest()
        dataset_str = json.dumps(
            {"rows": len(dataset.rows), "features": dataset.feature_names}, sort_keys=True
        )
        dataset_hash = hashlib.sha256(dataset_str.encode("utf-8")).hexdigest()
        origin = str(dataset.metadata.get("origin", "demo-only"))

        manifest = ModelManifest(
            model_id=f"lgbm-{payload_hash[:8]}",
            algorithm="lightgbm",
            feature_names=list(dataset.feature_names),
            hyperparameters=hyperparameters,
            feature_schema_hash=feature_schema_hash,
            train_dataset_hash=dataset_hash,
            performance_metrics=performance_metrics,
            model_payload_hash=payload_hash,
            native_model_text=native_text,
            dataset_origin=origin,
            status="EVALUATED",
            metadata={"seed": seed, "num_leaves": num_leaves},
        )

        predictor = Predictor(manifest=manifest, native_booster=booster)
        return predictor, manifest

    @staticmethod
    def train_logistic(
        dataset: TrainingDataset,
        feature_schema_hash: str,
        seed: int = 42,
    ) -> tuple[Predictor, ModelManifest]:
        if not dataset.rows or not dataset.features_matrix:
            raise ValueError("INSUFFICIENT_DATA: empty training dataset")

        unique_targets = set(dataset.target_labels)
        if len(unique_targets) < 2:
            counts = {label: dataset.target_labels.count(label) for label in unique_targets}
            raise ValueError(
                f"INSUFFICIENT_DATA: both classes required for binary training: {counts}"
            )

        clf = LogisticRegression(random_state=seed, max_iter=300)
        clf.fit(dataset.features_matrix, dataset.target_labels)

        val_preds = [float(clf.predict_proba([x])[0][1]) for x in dataset.features_matrix]
        metrics = ModelEvaluator.evaluate(val_preds, dataset.target_labels)

        weights_repr = json.dumps(
            {"coef": clf.coef_.tolist(), "intercept": clf.intercept_.tolist()}, sort_keys=True
        )
        payload_hash = hashlib.sha256(weights_repr.encode("utf-8")).hexdigest()
        dataset_hash = hashlib.sha256(str(len(dataset.rows)).encode("utf-8")).hexdigest()
        origin = str(dataset.metadata.get("origin", "demo-only"))

        manifest = ModelManifest(
            model_id=f"logistic-{payload_hash[:8]}",
            algorithm="logistic_regression",
            feature_names=list(dataset.feature_names),
            hyperparameters={"max_iter": 300, "random_state": seed},
            feature_schema_hash=feature_schema_hash,
            train_dataset_hash=dataset_hash,
            performance_metrics=metrics,
            model_payload_hash=payload_hash,
            native_model_text=weights_repr,
            dataset_origin=origin,
            status="EVALUATED",
        )

        predictor = Predictor(manifest=manifest, sklearn_model=clf)
        return predictor, manifest

    @staticmethod
    def train_majority(
        dataset: TrainingDataset,
        feature_schema_hash: str,
    ) -> tuple[Predictor, ModelManifest]:
        if not dataset.target_labels:
            raise ValueError("INSUFFICIENT_DATA: empty training dataset")

        pos = sum(dataset.target_labels)
        prob = float(pos / len(dataset.target_labels))
        payload_hash = hashlib.sha256(f"majority_{prob:.6f}".encode()).hexdigest()

        manifest = ModelManifest(
            model_id=f"majority-{payload_hash[:8]}",
            algorithm="majority_class",
            feature_names=list(dataset.feature_names),
            hyperparameters={"prob": prob},
            feature_schema_hash=feature_schema_hash,
            train_dataset_hash="majority",
            performance_metrics={"prob": prob},
            model_payload_hash=payload_hash,
            status="EVALUATED",
        )
        predictor = Predictor(manifest=manifest, majority_prob=prob)
        return predictor, manifest
