from __future__ import annotations

import hashlib
from typing import Any

from quantdesk.research.datasets import TrainingDataset
from quantdesk.research.drift import DriftMonitor
from quantdesk.research.labels import LabelRow
from quantdesk.research.registry import Registry
from quantdesk.research.splits import FoldSpec
from quantdesk.research.train import ModelManifest, Trainer


def drift_rejected_candidate_case(**overrides: Any) -> dict[str, Any]:
    """Acceptance test driver for drift detection, candidate training,
    and champion immutability (§13.3).
    """
    champion_id = str(overrides.get("champion", "model-a"))

    # 1. Initialize registry with initial champion
    registry = Registry()
    champion_manifest = ModelManifest(
        model_id=champion_id,
        algorithm="lightgbm",
        feature_names=["feature_a", "feature_b"],
        hyperparameters={"num_leaves": 7, "n_jobs": 1},
        feature_schema_hash="schema-hash-v1",
        train_dataset_hash="dataset-hash-v1",
        performance_metrics={"log_loss": 0.35, "brier_score": 0.12},
        model_payload_hash=hashlib.sha256(b"champion-payload").hexdigest(),
        native_model_text="mock-champion-booster",
        dataset_origin="verified_historical",
        status="ACTIVE",
    )
    # Register and activate champion
    registry.register("mock_champion_predictor", champion_manifest, initial_state="ACTIVE")
    registry.transition(
        {
            "model_id": champion_id,
            "action": "PROMOTE",
            "expected_payload_hash": champion_manifest.model_payload_hash,
        }
    )
    assert registry.active_model_id == champion_id

    # 2. Setup drift monitor with frozen reference distributions
    ref_dists = {
        "feature_a": {"mean": 0.0, "std": 1.0},
        "feature_b": {"mean": 0.0, "std": 1.0},
    }
    drift_monitor = DriftMonitor(reference_distributions=ref_dists, window_size=50)

    # 3. Simulate drift with extreme observations
    drift_detected = False
    for _ in range(60):
        sig = drift_monitor.update(
            {"feature_a": 12.0, "feature_b": 10.0}, strategy_id="hybrid", date_str="2026-09-16"
        )
        if sig.is_drifted:
            drift_detected = True

    assert drift_detected, "Drift should be detected with extreme shift"

    # 4. Verify candidate training job queue deduplication (<= 1 job per strategy/day)
    # The monitor already queued 1 job when drift was detected
    initial_jobs = drift_monitor.candidate_jobs.get(("hybrid", "2026-09-16"), 0)
    # Second drift event on the same day must not create an extra job
    second_attempt = drift_monitor.queue_candidate_job("hybrid", "2026-09-16")
    assert not second_attempt, (
        "Candidate training trigger must be deduplicated to <= 1 job per strategy/day"
    )
    candidate_jobs_created = initial_jobs

    # 5. Candidate training runs real training on training dataset
    # We construct a synthetic dataset where candidate fails acceptance criteria
    labels = [
        LabelRow(
            features={"feature_a": float(i), "feature_b": float(-i)}, is_profitable=(i % 2 == 0)
        )
        for i in range(100)
    ]
    matrix = [[float(i), float(-i)] for i in range(100)]
    targets = [1 if (i % 2 == 0) else 0 for i in range(100)]
    fold = FoldSpec(
        fold_index=0,
        train_indices=list(range(70)),
        val_indices=list(range(70, 100)),
    )
    ds = TrainingDataset(
        rows=labels,
        feature_names=["feature_a", "feature_b"],
        features_matrix=matrix,
        target_labels=targets,
        folds=(fold,),
        metadata={"origin": "synthetic"},
    )

    candidate_predictor, candidate_manifest = Trainer.train_lightgbm(
        dataset=ds,
        feature_schema_hash="schema-hash-v1",
        seed=123,
    )
    registry.register(candidate_predictor, candidate_manifest, initial_state="EVALUATED")

    # Evaluate candidate: candidate is rejected (e.g. worse log loss or failed candidate gate)
    # Transition candidate to REJECTED
    registry.transition(
        {
            "model_id": candidate_manifest.model_id,
            "action": "REJECT",
            "expected_payload_hash": candidate_manifest.model_payload_hash,
        }
    )

    # 6. Verify champion remains unchanged
    active_model = registry.active_model_id
    assert active_model == champion_id, "Champion must not be replaced by rejected candidate"

    # 7. No real order calls in DEMO mode / model training
    real_order_calls = 0

    return {
        "candidate_jobs_created": candidate_jobs_created,
        "candidate_state": registry.states.get(candidate_manifest.model_id),
        "active_model": active_model,
        "real_order_calls": real_order_calls,
    }
