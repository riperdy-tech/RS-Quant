from __future__ import annotations

import pytest

from quantdesk.research.datasets import TrainingDataset
from quantdesk.research.drift import DriftMonitor
from quantdesk.research.labels import LabelRow
from quantdesk.research.registry import Registry
from quantdesk.research.train import ModelManifest, Predictor, Trainer


def _create_manifest(
    model_id: str = "lgbm-1",
    payload_hash: str = "payload_hash1",
    schema_hash: str = "schema_hash1",
    origin: str = "verified_historical",
    status: str = "EVALUATED",
) -> ModelManifest:
    return ModelManifest(
        model_id=model_id,
        algorithm="lightgbm",
        feature_names=["feat_A", "feat_B"],
        hyperparameters={"num_leaves": 7, "n_jobs": 1},
        feature_schema_hash=schema_hash,
        train_dataset_hash="ds_hash1",
        performance_metrics={"log_loss": 0.40, "brier_score": 0.15},
        model_payload_hash=payload_hash,
        native_model_text="booster_data",
        dataset_origin=origin,
        status=status,
    )


def test_registry_hash_validation():
    r = Registry()
    assert r.get_active_model() is None

    manifest = _create_manifest()
    r.register("mock_model", manifest)

    # 1. Invalid payload hash transition
    res = r.transition({"model_id": "lgbm-1", "expected_payload_hash": "wrong_hash"})
    assert not res.success
    assert "Payload hash mismatch" in res.reason
    assert r.get_active_model() is None

    # 2. Invalid schema hash transition
    res_schema = r.transition(
        {
            "model_id": "lgbm-1",
            "expected_payload_hash": "payload_hash1",
            "expected_feature_schema_hash": "wrong_schema",
        }
    )
    assert not res_schema.success
    assert "Feature schema mismatch" in res_schema.reason

    # 3. Valid transition
    res_valid = r.transition(
        {
            "model_id": "lgbm-1",
            "expected_payload_hash": "payload_hash1",
            "expected_feature_schema_hash": "schema_hash1",
        }
    )
    assert res_valid.success
    assert r.get_active_model() == "mock_model"
    assert r.active_model_id == "lgbm-1"


def test_all_lifecycle_states_and_rejected_candidate_protection():
    r = Registry()
    manifest = _create_manifest(model_id="cand-1", status="TRAINING")
    r.register("model_obj", manifest, initial_state="TRAINING")
    assert r.get_state("cand-1") == "TRAINING"

    # Move to EVALUATED
    res = r.transition({"model_id": "cand-1", "action": "EVALUATED", "target_state": "EVALUATED"})
    assert res.success
    assert r.get_state("cand-1") == "EVALUATED"

    # Reject candidate
    res_rej = r.transition({"model_id": "cand-1", "action": "REJECT"})
    assert res_rej.success
    assert r.get_state("cand-1") == "REJECTED"

    # Attempting to promote a rejected candidate must fail closed
    res_promo = r.transition({"model_id": "cand-1", "action": "PROMOTE"})
    assert not res_promo.success
    assert "Cannot promote a rejected model candidate" in res_promo.reason


def test_synthetic_data_live_block():
    r = Registry()
    manifest = _create_manifest(model_id="demo-cand", origin="synthetic")
    r.register("mock_demo_model", manifest)

    # Promoting synthetic model in DEMO works
    res_demo = r.transition(
        {
            "model_id": "demo-cand",
            "action": "PROMOTE",
            "environment": "DEMO",
        }
    )
    assert res_demo.success

    # Promoting synthetic model in LIVE must fail closed (§13.3)
    res_live = r.transition(
        {
            "model_id": "demo-cand",
            "action": "PROMOTE",
            "environment": "LIVE",
            "operator_confirmed": True,
        }
    )
    assert not res_live.success
    assert (
        "Candidate models trained on synthetic data can never become live champions"
        in res_live.reason
    )


def test_position_flat_guard_for_live_promotion():
    r = Registry()
    manifest = _create_manifest(model_id="live-cand", origin="verified_historical")
    r.register("mock_live_model", manifest)

    # 1. Non-flat position blocks live promotion (§13.3)
    res_pos = r.transition(
        {
            "model_id": "live-cand",
            "action": "PROMOTE",
            "environment": "LIVE",
            "current_position_qty": 5,
            "has_unresolved_orders": False,
            "operator_confirmed": True,
        }
    )
    assert not res_pos.success
    assert "Cannot change active model while position is open" in res_pos.reason

    # 2. Unresolved orders block live promotion
    res_orders = r.transition(
        {
            "model_id": "live-cand",
            "action": "PROMOTE",
            "environment": "LIVE",
            "current_position_qty": 0,
            "has_unresolved_orders": True,
            "operator_confirmed": True,
        }
    )
    assert not res_orders.success

    # 3. Missing operator confirmation blocks live promotion
    res_op = r.transition(
        {
            "model_id": "live-cand",
            "action": "PROMOTE",
            "environment": "LIVE",
            "current_position_qty": 0,
            "has_unresolved_orders": False,
            "operator_confirmed": False,
        }
    )
    assert not res_op.success
    assert "operator confirmation" in res_op.reason.lower()

    # 4. Flat and confirmed succeeds
    res_ok = r.transition(
        {
            "model_id": "live-cand",
            "action": "PROMOTE",
            "environment": "LIVE",
            "current_position_qty": 0,
            "has_unresolved_orders": False,
            "operator_confirmed": True,
        }
    )
    assert res_ok.success


def test_registry_rollback_to_immutable_earlier_artifact():
    r = Registry()
    man_a = _create_manifest(model_id="model-A", payload_hash="hashA")
    man_b = _create_manifest(model_id="model-B", payload_hash="hashB")
    r.register("model_A_obj", man_a)
    r.register("model_B_obj", man_b)

    # Activate A
    r.transition({"model_id": "model-A", "action": "PROMOTE", "strategy_id": "strat1"})
    assert r.get_active_model("strat1") == "model_A_obj"

    # Promote B
    r.transition({"model_id": "model-B", "action": "PROMOTE", "strategy_id": "strat1"})
    assert r.get_active_model("strat1") == "model_B_obj"
    assert r.get_state("model-A") == "RETIRED"

    # Rollback to A
    res_rb = r.rollback(strategy_id="strat1")
    assert res_rb.success
    assert res_rb.active_model_id == "model-A"
    assert r.get_active_model("strat1") == "model_A_obj"
    assert r.get_state("model-A") == "ACTIVE"
    assert r.get_state("model-B") == "RETIRED"

    # Second rollback fails because history is empty
    res_rb2 = r.rollback(strategy_id="strat1")
    assert not res_rb2.success


def test_drift_monitor():
    dm = DriftMonitor({"feat_A": {"mean": 0.0, "std": 1.0}}, window_size=100)

    # Add < 100 observations, no drift
    for _ in range(50):
        sig = dm.update({"feat_A": 10.0})
        assert not sig.is_drifted

    # Exceed 100 observations with extreme shift
    for _ in range(60):
        sig = dm.update({"feat_A": 10.0})

    assert sig.is_drifted
    assert sig.feature_name == "feat_A"
    assert sig.psi is not None and sig.psi > 0.20


def test_drift_monitor_deduplication_and_auxiliary_metrics():
    dm = DriftMonitor({"f1": {"mean": 5.0, "std": 1.0}}, window_size=20)

    # Deduplicated candidate jobs (<= 1 per strategy/day)
    assert dm.queue_candidate_job("stratX", "2026-09-16") is True
    assert dm.queue_candidate_job("stratX", "2026-09-16") is False
    assert dm.queue_candidate_job("stratX", "2026-09-17") is True

    # Missingness check
    for _ in range(10):
        dm.update({})  # f1 missing
    sig_miss = dm.check_missingness_drift("f1", missing_ratio_threshold=0.5)
    assert sig_miss.is_drifted

    # Cost drift check
    sig_cost = dm.check_cost_drift(realized_cost=15.0, baseline_cost=8.0, threshold_pct=0.50)
    assert sig_cost.is_drifted
    assert sig_cost.metric_type == "cost"


def test_trainer_insufficient_and_one_class_datasets():
    empty_ds = TrainingDataset()
    with pytest.raises(ValueError, match="INSUFFICIENT_DATA"):
        Trainer.train_lightgbm(empty_ds, "schema_1")

    # One-class dataset
    labels = [LabelRow(features={"f1": float(i)}, is_profitable=True) for i in range(20)]
    matrix = [[float(i)] for i in range(20)]
    targets = [1] * 20  # only class 1!
    one_class_ds = TrainingDataset(
        rows=labels,
        feature_names=["f1"],
        features_matrix=matrix,
        target_labels=targets,
    )
    with pytest.raises(ValueError, match="INSUFFICIENT_DATA"):
        Trainer.train_lightgbm(one_class_ds, "schema_1")


def test_feature_reordering_invariance():
    manifest = _create_manifest()
    manifest.feature_names = ["feat_A", "feat_B"]

    # Predictor with simple mock
    class SimpleModel:
        def predict(self, x):
            # returns x[0] * 0.1 + x[1] * 0.2
            return [x[0][0] * 0.1 + x[0][1] * 0.2]

    predictor = Predictor(manifest=manifest, native_booster=SimpleModel())

    # Features passed in normal order
    p1 = predictor.predict({"feat_A": 2.0, "feat_B": 3.0})
    # Features passed in reverse order
    p2 = predictor.predict({"feat_B": 3.0, "feat_A": 2.0})

    assert float(p1) == float(p2) == pytest.approx(2.0 * 0.1 + 3.0 * 0.2)
