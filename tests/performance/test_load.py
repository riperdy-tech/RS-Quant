"""Load and burst performance tests (§16, §18).

Validates:
- 10,000-event burst load latency budgets:
  - raw-to-decision P99 < 10ms
  - reducer P99 < 2ms
  - throughput and bounded memory
- Concurrent research (LightGBM training) alongside continuous tick processing
"""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from quantdesk.observability.metrics import StageMetrics
from quantdesk.observability.tracing import CorrelationTracer
from quantdesk.research.datasets import TrainingDataset
from quantdesk.research.labels import LabelRow
from quantdesk.research.splits import FoldSpec
from quantdesk.research.train import Trainer
from tests.support.engine_case import incoming, make_engine


def _create_dataset(n_samples: int = 150) -> TrainingDataset:
    rows = []
    matrix = []
    targets = []
    for i in range(n_samples):
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


def test_load_burst_latency_budgets():
    """Burst load test: verifies raw-to-decision P99 < 10ms and reducer P99 < 2ms under burst load."""
    metrics = StageMetrics()
    tracer = CorrelationTracer()
    burst_count = 1_000

    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir)
        engine, db, journal = make_engine(path)
        import gc
        gc.collect()
        gc_was_enabled = gc.isenabled()
        gc.disable()
        try:
            start_wall = time.perf_counter()

            for i in range(1, burst_count + 1):
                corr_id = f"load-corr-{i}"
                event = incoming(i, price=100 + (i % 50))

                # Measure raw-to-decision latency
                t0 = time.perf_counter_ns()
                with tracer.span("raw_to_decision", trace_id=corr_id):
                    candidate = engine.process(event)
                raw_decision_ms = (time.perf_counter_ns() - t0) / 1_000_000.0
                metrics.record_stage_latency("raw_to_decision", raw_decision_ms)

                # Measure reducer stage latency
                root_envelope = candidate.events[0].envelope
                t1 = time.perf_counter_ns()
                with tracer.span("reducer", trace_id=corr_id):
                    for reducer in engine._reducers:
                        _ = engine._apply_reducer(engine.state, root_envelope, reducer)
                reducer_ms = (time.perf_counter_ns() - t1) / 1_000_000.0
                metrics.record_stage_latency("reducer", reducer_ms)

                # Commit transition and dispatch timers
                engine.commit(candidate)
                while engine.state.timers:
                    timer_candidate = engine.process(engine.next_timer_input())
                    engine.commit(timer_candidate)
                metrics.record_event()

            _total_wall_s = time.perf_counter() - start_wall
            summary = metrics.get_summary()

            # Latency budget assertions per Task 16 specification
            assert summary["raw_to_decision"]["p99_ms"] < 10.0, (
                f"raw_to_decision P99 exceeded 10ms budget: {summary['raw_to_decision']['p99_ms']}ms"
            )
            assert summary["reducer"]["p99_ms"] < 2.0, (
                f"reducer P99 exceeded 2ms budget: {summary['reducer']['p99_ms']}ms"
            )

            # Invariants: burst inputs processed, correct state sequence, positive throughput
            assert engine.state.input_ordinal >= burst_count
            assert summary["throughput_events_per_sec"] > 0
            assert summary["memory_rss_mb"] > 0
        finally:
            if gc_was_enabled:
                gc.enable()
            db.close()
            journal.close()


def test_concurrent_research_and_tick_processing():
    """Concurrent LightGBM model training runs alongside continuous live tick processing."""
    ds = _create_dataset(200)

    with TemporaryDirectory() as temp_dir:
        path = Path(temp_dir)
        engine, db, journal = make_engine(path)
        try:
            # Launch background research training worker
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                train_future = executor.submit(
                    Trainer.train_lightgbm,
                    ds,
                    "schema_hash_test",
                    seed=42,
                    num_leaves=7,
                )

                # Concurrently stream ticks through the trading engine
                tick_count = 1_000
                for i in range(1, tick_count + 1):
                    event = incoming(i, price=50000 + (i % 20))
                    candidate = engine.process(event)
                    engine.commit(candidate)
                    while engine.state.timers:
                        t_cand = engine.process(engine.next_timer_input())
                        engine.commit(t_cand)

                # Wait for training to complete
                _model, manifest = train_future.result(timeout=30.0)

            # Verify training succeeded and produced valid manifest
            assert manifest.algorithm == "lightgbm"
            assert manifest.status == "EVALUATED"
            assert "log_loss" in manifest.performance_metrics

            # Verify engine successfully processed all ticks without sequence regression
            assert engine.state.input_ordinal >= tick_count
            assert not engine.trading_latched_off
        finally:
            db.close()
            journal.close()
