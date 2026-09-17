from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FoldSpec:
    """Specification of train/validation/holdout split indices for a single walk-forward fold."""

    fold_index: int = 0
    train_indices: list[int] = field(default_factory=list)
    val_indices: list[int] = field(default_factory=list)
    holdout_indices: list[int] = field(default_factory=list)
    purged_indices: list[int] = field(default_factory=list)
    val_start_ns: int = 0
    val_end_ns: int = 0


class PurgedWalkForward:
    """Expanding walk-forward train/validation splitter with time-based
    label interval purging (§13.2).
    """

    def __init__(
        self,
        n_folds: int = 3,
        holdout_pct: float = 0.20,
        min_train_pct: float = 0.50,
    ) -> None:
        self.n_folds = max(1, n_folds)
        self.holdout_pct = holdout_pct
        self.min_train_pct = min_train_pct

    def split(
        self,
        row_timestamps: Sequence[int],
        label_horizon_ns: int,
        delivery_uncertainty_ns: int = 0,
    ) -> tuple[FoldSpec, ...]:
        total_rows = len(row_timestamps)
        if total_rows == 0:
            return ()

        # 1. Reserve last 20% chronologically as final holdout (§13.2)
        holdout_start_idx = int(total_rows * (1.0 - self.holdout_pct))
        dev_rows = row_timestamps[:holdout_start_idx]
        holdout_indices = list(range(holdout_start_idx, total_rows))

        dev_total = len(dev_rows)
        if dev_total < self.n_folds + 2:
            return ()

        # 2. Expanding window development folds
        min_train_size = max(1, int(dev_total * self.min_train_pct))
        val_total = dev_total - min_train_size
        if val_total < self.n_folds:
            return ()

        val_size = val_total // self.n_folds
        folds: list[FoldSpec] = []

        for i in range(self.n_folds):
            train_end_idx = min_train_size + i * val_size
            val_start_idx = train_end_idx
            val_end_idx = val_start_idx + val_size if i < self.n_folds - 1 else dev_total

            val_start_ns = dev_rows[val_start_idx]
            val_end_ns = dev_rows[val_end_idx - 1] if val_end_idx > val_start_idx else val_start_ns

            # 3. Purge training rows whose label interval reaches or overlaps validation interval
            # Label information interval = row_time + label_horizon_ns + delivery_uncertainty_ns
            purge_cutoff = val_start_ns - (label_horizon_ns + delivery_uncertainty_ns)

            train_indices: list[int] = []
            purged_indices: list[int] = []

            for j in range(train_end_idx):
                if dev_rows[j] < purge_cutoff:
                    train_indices.append(j)
                else:
                    purged_indices.append(j)

            val_indices = list(range(val_start_idx, val_end_idx))

            folds.append(
                FoldSpec(
                    fold_index=i,
                    train_indices=train_indices,
                    val_indices=val_indices,
                    holdout_indices=holdout_indices,
                    purged_indices=purged_indices,
                    val_start_ns=val_start_ns,
                    val_end_ns=val_end_ns,
                )
            )

        return tuple(folds)
