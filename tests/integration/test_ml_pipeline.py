from __future__ import annotations

from decimal import Decimal
from typing import Any

from quantdesk.core.events import Envelope, StrategyIntent
from quantdesk.core.types import IntentAction, Side
from quantdesk.research.datasets import TrainingDataset
from quantdesk.research.labels import LabelRow
from quantdesk.research.registry import Registry
from quantdesk.research.splits import FoldSpec
from quantdesk.research.train import ModelManifest, Trainer
from quantdesk.strategies.base import Strategy
from quantdesk.strategies.hybrid import HybridStrategy


def _make_intent(
    action: IntentAction,
    side: Side = Side.BUY,
    intent_id: str = "intent-1",
) -> StrategyIntent:
    return StrategyIntent(
        intent_id=intent_id,
        strategy_id="strat1",
        instrument_id="BTCUSDT",
        decision_seq=1,
        feature_snapshot_id="snap-1",
        config_hash="cfg-1",
        model_hash_or_none=None,
        action=action,
        side=side,
        desired_quantity=Decimal("1.0"),
        risk_budget=Decimal("100.0"),
        price_policy="MARKET",
        expires_at_ns=1_000_000_000,
        stop_policy="NONE",
        reason="test",
    )


def _make_envelope() -> Envelope:
    return Envelope(
        event_type="SignalTrigger",
        schema_version=1,
        run_id="run-ml",
        account_id=None,
        venue="bitget",
        environment="DEMO",
        instrument_id="BTCUSDT",
        source_channel="market",
        connection_epoch="epoch-1",
        source_message_id=None,
        source_sequence=None,
        exchange_event_ns=1000,
        exchange_transaction_ns=None,
        receive_wall_ns=1000,
        receive_monotonic_ns=1000,
        available_ns=1000,
        causation_id=None,
        correlation_id="corr-1",
        raw_ref=None,
        producer_version="v1",
        payload=b"{}",
        event_id="evt-1",
        engine_seq=1,
    )


def _create_synthetic_dataset(n_samples: int = 120) -> TrainingDataset:
    rows = []
    matrix = []
    targets = []
    for i in range(n_samples):
        # Create non-trivial synthetic feature mapping
        f1 = float(i) / 10.0
        f2 = float(n_samples - i) / 5.0
        is_pos = (i % 2 == 0) and (f1 > 2.0)
        rows.append(LabelRow(features={"feat_A": f1, "feat_B": f2}, is_profitable=is_pos))
        matrix.append([f1, f2])
        targets.append(1 if is_pos else 0)

    train_len = int(n_samples * 0.7)
    fold = FoldSpec(
        fold_index=0,
        train_indices=list(range(train_len)),
        val_indices=list(range(train_len, n_samples)),
    )

    return TrainingDataset(
        rows=rows,
        feature_names=["feat_A", "feat_B"],
        features_matrix=matrix,
        target_labels=targets,
        folds=(fold,),
        metadata={"origin": "synthetic"},
    )


def test_drift_trains_candidate_without_replacing_champion(case):
    """Primary gate test per §13.3 and Task 12 of IMPLEMENTATION_PLAN.md."""
    r = case("drift_rejected_candidate", champion="model-a")
    assert r["candidate_jobs_created"] == 1
    assert r["candidate_state"] == "REJECTED"
    assert r["active_model"] == "model-a"
    assert r["real_order_calls"] == 0


def test_model_manifest_and_bounded_lightgbm():
    """Verifies bounded LightGBM training, manifest hashes, and predictor inference (§13.2)."""
    ds = _create_synthetic_dataset(120)

    # Train model with bounded search parameters
    model, manifest = Trainer.train_lightgbm(ds, "schema_hash_1", seed=42, num_leaves=7)

    # Verify hyperparameter bounds
    assert manifest.hyperparameters["num_leaves"] in (7, 15)
    assert manifest.hyperparameters["n_jobs"] == 1
    assert manifest.hyperparameters["max_depth"] == 4
    assert manifest.hyperparameters["learning_rate"] == 0.03
    assert manifest.hyperparameters["force_col_wise"] is True
    assert manifest.algorithm == "lightgbm"
    assert manifest.model_payload_hash != ""

    # Register and activate
    registry = Registry()
    registry.register(model, manifest)

    res = registry.transition(
        {
            "model_id": manifest.model_id,
            "expected_payload_hash": manifest.model_payload_hash,
        }
    )
    assert res.success

    loaded = registry.get_active_model()
    assert loaded is not None

    # Predictor output inspection
    pred = loaded.predict({"feat_A": 5.0, "feat_B": 10.0})
    assert 0.0 <= float(pred) <= 1.0
    assert 0.0 <= loaded.predict_proba({"feat_A": 5.0, "feat_B": 10.0}) <= 1.0


class DummyRuleStrategy(Strategy):
    def __init__(self, intents: tuple[StrategyIntent, ...]) -> None:
        self._intents = intents

    def on_event(self, event: Envelope, context: dict[str, Any]) -> tuple[StrategyIntent, ...]:
        return self._intents


def test_hybrid_strategy_gating_and_non_entry_passthrough():
    """HybridStrategy gates ENTER intents, unconditionally passes EXIT/CANCEL, and fails closed."""
    enter_intent = _make_intent(action=IntentAction.ENTER, side=Side.BUY, intent_id="enter-1")
    exit_intent = _make_intent(action=IntentAction.EXIT, side=Side.SELL, intent_id="exit-1")
    cancel_intent = _make_intent(
        action=IntentAction.CANCEL_ENTRY, side=Side.BUY, intent_id="cancel-1"
    )

    rule_strat = DummyRuleStrategy((enter_intent, exit_intent, cancel_intent))
    hybrid = HybridStrategy(rule_strategy=rule_strat, threshold=0.60)

    # 1. No registry set -> fails closed on ENTER, passes EXIT and CANCEL_ENTRY
    out = hybrid.on_event(_make_envelope(), {})
    assert len(out) == 2
    assert out[0].action == IntentAction.EXIT
    assert out[1].action == IntentAction.CANCEL_ENTRY

    # 2. Registry with model predicting probability 0.40 (< 0.60 threshold)
    reg = Registry()
    man_low = ModelManifest(
        model_id="low-prob",
        algorithm="mock",
        feature_names=["f1"],
        model_payload_hash="hash-low",
    )

    class LowModel:
        model_hash = "hash-low"

        def predict(self, features):
            return 0.40

    reg.register(LowModel(), man_low, initial_state="ACTIVE")
    reg.transition({"model_id": "low-prob", "action": "PROMOTE"})
    hybrid.set_registry(reg)

    out_low = hybrid.on_event(_make_envelope(), {"features": {"f1": 1.0}})
    assert len(out_low) == 2
    assert out_low[0].action == IntentAction.EXIT
    assert out_low[1].action == IntentAction.CANCEL_ENTRY

    # 3. Registry with model predicting probability 0.85 (>= 0.60 threshold)
    man_high = ModelManifest(
        model_id="high-prob",
        algorithm="mock",
        feature_names=["f1"],
        model_payload_hash="hash-high",
    )

    class HighModel:
        model_hash = "hash-high"

        def predict(self, features):
            return 0.85

    reg.register(HighModel(), man_high, initial_state="ACTIVE")
    reg.transition({"model_id": "high-prob", "action": "PROMOTE"})

    out_high = hybrid.on_event(_make_envelope(), {"features": {"f1": 1.0}})
    assert out_high[0].action == IntentAction.ENTER
    assert out_high[0].model_hash_or_none == "hash-high"
    assert out_high[1].action == IntentAction.EXIT
    assert out_high[2].action == IntentAction.CANCEL_ENTRY

    # 4. Errored / latency failure model fails closed
    class ErrorModel:
        def predict(self, features):
            raise TimeoutError("Model latency exceeded budget")

    man_err = ModelManifest(model_id="err-model", algorithm="mock", model_payload_hash="hash-err")
    reg.register(ErrorModel(), man_err)
    reg.transition({"model_id": "err-model", "action": "PROMOTE"})

    out_err = hybrid.on_event(_make_envelope(), {"features": {"f1": 1.0}})
    assert len(out_err) == 2  # Only EXIT and CANCEL_ENTRY survived, ENTER dropped


def test_trainer_run_baselines_and_manifest_spec():
    """Verifies Trainer.run(spec) -> ModelManifest for LightGBM, logistic, and majority models."""
    ds = _create_synthetic_dataset(100)

    # 1. Run LightGBM spec
    registry = Registry()
    lgb_manifest = Trainer.run(
        {
            "algorithm": "lightgbm",
            "dataset": ds,
            "feature_schema_hash": "schema_v1",
            "num_leaves": 15,
            "registry": registry,
        }
    )
    assert lgb_manifest.algorithm == "lightgbm"
    assert lgb_manifest.hyperparameters["num_leaves"] == 15
    assert registry.get_model(lgb_manifest.model_id) is not None

    # 2. Run Logistic Regression spec
    log_manifest = Trainer.run(
        {
            "algorithm": "logistic",
            "dataset": ds,
            "feature_schema_hash": "schema_v1",
            "registry": registry,
        }
    )
    assert log_manifest.algorithm == "logistic_regression"
    assert registry.get_model(log_manifest.model_id) is not None

    # 3. Run Majority Class spec
    maj_manifest = Trainer.run(
        {
            "algorithm": "majority",
            "dataset": ds,
            "feature_schema_hash": "schema_v1",
            "registry": registry,
        }
    )
    assert maj_manifest.algorithm == "majority_class"
    assert registry.get_model(maj_manifest.model_id) is not None
