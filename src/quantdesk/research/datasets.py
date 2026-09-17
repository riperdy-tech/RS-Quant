from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from quantdesk.research.labels import LabelRow
from quantdesk.research.splits import FoldSpec


@dataclass
class TrainingDataset:
    """Consolidated ML training dataset with feature matrix, targets, and walk-forward folds."""

    rows: list[LabelRow] = field(default_factory=list)
    feature_names: list[str] = field(default_factory=list)
    features_matrix: list[list[float]] = field(default_factory=list)
    target_labels: list[int] = field(default_factory=list)
    folds: tuple[FoldSpec, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


class DatasetBuilder:
    """Builds numerical training matrices and target vectors from labeled rows."""

    @staticmethod
    def build(
        rows: list[LabelRow],
        folds: tuple[FoldSpec, ...],
        metadata: dict[str, Any] | None = None,
    ) -> TrainingDataset:
        valid_rows = [r for r in rows if r.dropped_reason is None]
        all_features: set[str] = set()
        for r in valid_rows:
            all_features.update(r.features.keys())
        feature_names = sorted(all_features)

        matrix: list[list[float]] = []
        targets: list[int] = []

        for r in valid_rows:
            vector = [float(r.features.get(fname, 0.0)) for fname in feature_names]
            matrix.append(vector)
            targets.append(1 if r.is_profitable else 0)

        return TrainingDataset(
            rows=valid_rows,
            feature_names=feature_names,
            features_matrix=matrix,
            target_labels=targets,
            folds=folds,
            metadata=metadata or {},
        )


class TrainOnlyScaler:
    """Standard scaler fitted strictly on training rows to prevent lookahead leakage per §13.2."""

    def __init__(self) -> None:
        self.means: list[float] = []
        self.stds: list[float] = []
        self.is_fitted = False

    def fit(self, matrix: list[list[float]], train_indices: list[int]) -> TrainOnlyScaler:
        if not matrix or not train_indices:
            return self
        n_features = len(matrix[0])
        self.means = [0.0] * n_features
        self.stds = [1.0] * n_features

        n_train = len(train_indices)
        for j in range(n_features):
            vals = [matrix[i][j] for i in train_indices]
            mean_j = sum(vals) / n_train
            var_j = sum((x - mean_j) ** 2 for x in vals) / max(1, n_train - 1)
            std_j = var_j**0.5
            self.means[j] = mean_j
            self.stds[j] = std_j if std_j > 1e-9 else 1.0

        self.is_fitted = True
        return self

    def transform(
        self, matrix: list[list[float]], indices: list[int] | None = None
    ) -> list[list[float]]:
        if not self.is_fitted:
            raise ValueError("TrainOnlyScaler must be fitted before transform")
        target_indices = indices if indices is not None else list(range(len(matrix)))
        transformed: list[list[float]] = []
        for i in target_indices:
            row = matrix[i]
            scaled = [(row[j] - self.means[j]) / self.stds[j] for j in range(len(row))]
            transformed.append(scaled)
        return transformed
