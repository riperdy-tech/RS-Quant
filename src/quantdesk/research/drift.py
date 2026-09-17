from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DriftSignal:
    """Drift evaluation signal per §13.3."""

    is_drifted: bool
    feature_name: str | None
    kl_divergence: float | None = None
    psi: float | None = None
    metric_type: str = "psi"
    alert_triggered: bool = False
    candidate_job_queued: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class DriftMonitor:
    """Monitors feature Population Stability Index (PSI), missingness, and cost drift per §13.3."""

    def __init__(
        self,
        reference_distributions: dict[str, dict[str, Any]],
        alert_threshold_psi: float = 0.20,
        window_size: int = 100,
        pseudocount: float = 1e-4,
    ) -> None:
        self.reference = reference_distributions
        self.alert_threshold = alert_threshold_psi
        self.window_size = window_size
        self.pseudocount = pseudocount
        self.observations: dict[str, list[float]] = {k: [] for k in reference_distributions}
        self.missing_counts: dict[str, int] = {k: 0 for k in reference_distributions}
        self.total_observations: dict[str, int] = {k: 0 for k in reference_distributions}
        self.candidate_jobs: dict[tuple[str, str], int] = {}
        self.bins: dict[str, list[float]] = {}
        self.expected_probs: dict[str, list[float]] = {}

        self._initialize_reference_bins()

    def _initialize_reference_bins(self) -> None:
        for feat, ref_data in self.reference.items():
            if "bins" in ref_data and "reference_probs" in ref_data:
                self.bins[feat] = list(ref_data["bins"])
                self.expected_probs[feat] = list(ref_data["reference_probs"])
            else:
                mean = float(ref_data.get("mean", 0.0))
                std = float(ref_data.get("std", 1.0))
                if std <= 1e-6:
                    std = 1.0
                # 5 frozen reference histogram bins around reference mean
                self.bins[feat] = [
                    -math.inf,
                    mean - 1.5 * std,
                    mean - 0.5 * std,
                    mean + 0.5 * std,
                    mean + 1.5 * std,
                    math.inf,
                ]
                self.expected_probs[feat] = [0.2, 0.2, 0.2, 0.2, 0.2]

    def update(
        self,
        features: dict[str, float],
        strategy_id: str = "default",
        date_str: str = "today",
    ) -> DriftSignal:
        """Records feature observation and computes PSI when window size is reached (§13.3)."""
        latest_signal = DriftSignal(False, None, None, None)

        for feat in self.reference:
            self.total_observations[feat] += 1
            if feat not in features or features[feat] is None:
                self.missing_counts[feat] += 1
                continue

            val = float(features[feat])
            self.observations[feat].append(val)

            if len(self.observations[feat]) >= self.window_size:
                # Compute Population Stability Index (PSI) over the window
                psi_val = self.compute_psi(feat, self.observations[feat])
                is_drifted = psi_val > self.alert_threshold

                signal = DriftSignal(
                    is_drifted=is_drifted,
                    feature_name=feat,
                    kl_divergence=psi_val,
                    psi=psi_val,
                    metric_type="psi",
                    alert_triggered=is_drifted,
                    details={
                        "psi": psi_val,
                        "threshold": self.alert_threshold,
                        "n_obs": len(self.observations[feat]),
                    },
                )

                if is_drifted:
                    # Drift detected: optionally queues deduplicated candidate training job
                    job_queued = self.queue_candidate_job(strategy_id, date_str)
                    signal.candidate_job_queued = job_queued
                    return signal

                latest_signal = signal

        return latest_signal

    def compute_psi(self, feature_name: str, observations: list[float]) -> float:
        """Computes PSI with frozen reference histogram bins and documented pseudocount (1e-4)."""
        bin_edges = self.bins[feature_name]
        e_probs = self.expected_probs[feature_name]
        num_bins = len(e_probs)

        # Count actual observations in each bin
        actual_counts = [0] * num_bins
        for val in observations:
            for b in range(num_bins):
                low = bin_edges[b]
                high = bin_edges[b + 1]
                if low <= val < high or (b == num_bins - 1 and val >= low):
                    actual_counts[b] += 1
                    break

        total_act = len(observations)
        if total_act == 0:
            return 0.0

        psi: float = 0.0
        eps = self.pseudocount
        for b in range(num_bins):
            # Apply pseudocount smoothing
            e_adj = (e_probs[b] + eps) / (1.0 + num_bins * eps)
            a_adj = ((actual_counts[b] / total_act) + eps) / (1.0 + num_bins * eps)
            psi += (a_adj - e_adj) * math.log(a_adj / e_adj)

        return float(psi)

    def queue_candidate_job(self, strategy_id: str = "default", date_str: str = "today") -> bool:
        """Deduplicates candidate training triggers: at most 1 job per strategy/day (§13.3)."""
        key = (strategy_id, date_str)
        if self.candidate_jobs.get(key, 0) >= 1:
            return False
        self.candidate_jobs[key] = self.candidate_jobs.get(key, 0) + 1
        return True

    def check_missingness_drift(
        self, feature_name: str, missing_ratio_threshold: float = 0.10
    ) -> DriftSignal:
        """Tracks feature missingness/quality drift separately per §13.3."""
        total = self.total_observations.get(feature_name, 0)
        missing = self.missing_counts.get(feature_name, 0)
        if total == 0:
            return DriftSignal(False, feature_name, metric_type="missingness")

        ratio = missing / total
        is_drifted = ratio > missing_ratio_threshold
        return DriftSignal(
            is_drifted=is_drifted,
            feature_name=feature_name,
            metric_type="missingness",
            alert_triggered=is_drifted,
            details={"missing_ratio": ratio, "threshold": missing_ratio_threshold},
        )

    def check_cost_drift(
        self, realized_cost: float, baseline_cost: float, threshold_pct: float = 0.50
    ) -> DriftSignal:
        """Tracks realized execution-cost drift separately per §13.3."""
        if baseline_cost <= 0:
            return DriftSignal(False, None, metric_type="cost")
        rel_diff = (realized_cost - baseline_cost) / baseline_cost
        is_drifted = rel_diff > threshold_pct
        return DriftSignal(
            is_drifted=is_drifted,
            feature_name=None,
            metric_type="cost",
            alert_triggered=is_drifted,
            details={"cost_drift_pct": rel_diff, "threshold_pct": threshold_pct},
        )

    def check_prediction_loss(
        self, recent_loss: float, baseline_loss: float, threshold_ratio: float = 1.5
    ) -> DriftSignal:
        """Tracks delayed-label prediction loss separately per §13.3."""
        if baseline_loss <= 0:
            return DriftSignal(False, None, metric_type="prediction_loss")
        ratio = recent_loss / baseline_loss
        is_drifted = ratio > threshold_ratio
        return DriftSignal(
            is_drifted=is_drifted,
            feature_name=None,
            metric_type="prediction_loss",
            alert_triggered=is_drifted,
            details={"loss_ratio": ratio, "threshold_ratio": threshold_ratio},
        )
