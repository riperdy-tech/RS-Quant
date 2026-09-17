# QuantDesk build status

Authoritative plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

## Environment

- Workspace: `C:\Users\riper\Downloads\RS Quant`
- Branch: `quantdesk-implementation`
- Python: 3.12.10
- Node: 24.12.0
- npm: 11.6.2
- Git: 2.52.0.windows.1
- uv: 0.12.13 (installed per-user during preflight)

## Progress

| Task | State | Verification |
|---|---|---|
| Preflight | COMPLETE | Plan read in full; Astra audit at ignored SDD evidence path; rulings recorded |
| 01 | COMPLETE | `uv sync --frozen`; 18 tests passed; Ruff clean; mypy clean; isolated import OK; independent review clean after one fix round |
| 02 | COMPLETE | 78 persistence/fault tests; 18 foundation regressions; Ruff/mypy clean; independent review clean after three fix rounds |
| 03 | COMPLETE | 52 engine/replay/property tests; 96 prior regressions; Ruff/mypy clean; independent review clean after one fix round |
| 04 | COMPLETE | 59 data/book/import/export tests; 148 regressions; Ruff/mypy clean; review clean after one fix round |
| 05 | COMPLETE | 51 accounting/property tests; 207 regressions; Ruff/mypy clean; review clean after one fix round |
| 06 | COMPLETE | 63 OMS/property tests; 321 total regressions; Ruff/mypy clean; review clean after one fix round |
| 07 | COMPLETE | Bitget UTA V3 connector/recovery/protection implemented; 182 fault/reconciliation tests passed; 44 contract integration tests passed |
| 08 | COMPLETE | Risk limits/sizer/breakers/emergency implemented; 12 unit/fault tests passed; 547 regressions (01–07) passed; Ruff/mypy clean | 
| 09 | COMPLETE | Technical and orderflow features (§12), 4 rule strategies + exits, golden fixtures |
| 10 | COMPLETE | Execution simulator (§14) with depth walking, lifecycle events, fees/funding, margin breach |
| 11 | COMPLETE | PurgedWalkForward splits and backtest simulation integration tests passed |
| 12 | COMPLETE | LightGBM model pipeline, bounded trainer, and registry governance tests passed |
| 13 | COMPLETE | API duplicate command and recovery tests passed |
| 14 | COMPLETE | Browser workspace workflows, 10 pages, and Playwright E2E test passed against real local backend |
| 15 | COMPLETE | Dangerous controls, confirmation tokens, CAS revisions, viewer RBAC, and browser faults passed |
| 16 | COMPLETE | Integrated fault injection, matrix crash recovery, observability suite, and load benchmarks passed |
| 17 | COMPLETE | Desktop launcher, single-instance lock, backup/restore, packaging, and deployment passed |
| 18 | PENDING | Acceptance, documentation, and handoff |

## Safety

- Default mode remains DEMO.
- LIVE must remain disarmed.
- No production credentials have been requested.
- No mainnet orders or account-setting changes are authorized.

## Preflight evidence

- Independent architecture/safety audit: `.superpowers/sdd/IMPLEMENTATION_PLAN/preflight-astra.md`
- Official Bitget UTA documentation checked on 2026-09-12; endpoint-specific fixtures and links will be recorded in `VENUE_CONTRACT.md` during Task 07.
- Credentialed Bitget sandbox/mainnet checks, timed operational gates, and tiny-live acceptance remain external and are not software PASS results.

## Task 01 evidence

- Commits: `3cb65cd feat: establish deterministic foundation`, `016e5d0 fix: harden foundation contracts`
- TDD report: `.superpowers/sdd/IMPLEMENTATION_PLAN/task-01-report.md`
- Controller rerun: `uv sync --frozen`; `uv run pytest tests/unit/test_foundation.py -q` → 18 passed; `uv run ruff check .` → clean; `uv run mypy src/quantdesk` → clean; isolated package import → `0.1.0 DEMO`; LIVE profile diagnostics → `live_enabled: false`.
- Review: initial review found four Important boundary defects; fix round 1 addressed all four; scoped re-review PASS with no new Critical/Important breakage.
- Environment note: controller shells prepend the per-user Python scripts directory because `uv.exe` is not inherited on the default PATH. CI installs uv explicitly.

## Task 02 evidence

- Commits: `edccfd1` (journal/store/backups), `dcee0fe` (recovery fidelity/confidentiality), `8bd39ab` (sanitized backups/schema validation), `b84fd5f` (parser hardening).
- TDD report: `.superpowers/sdd/IMPLEMENTATION_PLAN/task-02-report.md`.
- Controller rerun: `uv run pytest tests/unit/test_persistence.py tests/faults/test_journal_crash.py -q` → 78 passed; foundation → 18 passed; Ruff and mypy clean.
- Review: four initial Important findings and three adversarial follow-ons were fixed in three bounded rounds; final scoped review PASS.
- Implemented: framed CRC/hash-chained Zstd journal, explicit fsync watermark and torn-tail recovery, encrypted private capture/redaction, one-writer WAL SQLite, atomic event/ledger/projection/outbox commit, exact signed double entry and economic deduplication, checkpoint manifests, consistent encrypted/sanitized backup and disarmed isolated restore.
- External limits: no physical power-cut certification; Windows ACL/key-store setup remains an installer/supervisor acceptance item. No venue credentials or network activity were used.

## Task 03 evidence

- Commits: `ee6d17a` (engine/checkpoint/replay), `ff1ef3c` (stable property timing), `7a9f5c5` (typed validation/stage/recovery/timer fixes).
- TDD report: `.superpowers/sdd/IMPLEMENTATION_PLAN/task-03-report.md`.
- Controller rerun: `uv run pytest tests/replay/test_determinism.py tests/property/test_event_ordering.py -q` → 52 passed; Task 01/02 regressions → 96 passed; Ruff and strict mypy clean.
- Review: four Important correctness gaps were reproduced and fixed; scoped re-review PASS, including an independent nonzero-checkpoint SQLite recovery probe.
- Implemented: stable stage-major reducer/decision scheduling, deterministic IDs and timers, typed payload decoding, immutable candidate/commit boundary, complete causal checkpoints, fact-only recovery, forensic regeneration/verification, fresh counterfactual branches, distinct hash scopes, and hard network-dispatch prohibition.

## Task 04 evidence

- Commits: `b9d1f0b` (instruments/books/bars/import/catalog), `075c8ed` (exact boundaries and content-derived capability fixes).
- TDD report: `.superpowers/sdd/IMPLEMENTATION_PLAN/task-04-report.md`.
- Controller rerun: Task 04 gate → 59 passed; Tasks 01–03 regressions → 148 passed; Ruff and strict mypy clean.
- Review: eight Important adversarial findings were fixed; scoped re-review PASS. Two Minor hardening observations remain explicitly ledgered for final review.
- Implemented: point-in-time instrument revisions and exact conversions, fail-closed book state/sequence contracts, causal immutable bars, raw-to-canonical-to-Parquet traceability, bounded explicit imports, immutable content-addressed catalog, and schema/content-derived dataset capabilities.

## Task 05 evidence

- Commits: `3a14594` (ledger/positions/margin/reconciliation), `a265024` (pending-exposure and historical-dedup fixes).
- TDD report: `.superpowers/sdd/IMPLEMENTATION_PLAN/task-05-report.md`.
- Controller rerun: Task 05 gate → 51 passed; Tasks 01–04 regressions → 207 passed; Ruff and strict mypy clean.
- Review: three Important financial edge cases fixed; scoped re-review PASS plus 567 exhaustive mixed-order/flat-position cases under low Decimal precision.
- Implemented: exact balanced postings, average-cost long/short/reversal state, separated fees/funding/cash/adjustments, restart-safe economic dedup and late aliases, reservations, mark/FX freshness, conservative margin tiers, and immutable reconciliation observations.

## Task 06 evidence

- Commits: `e23bf55` (shared OMS/arbitration/router/fencing), `f67d35f` (uncertainty, unresolved-reserve, and pre-handoff cancellation fixes).
- TDD report: `.superpowers/sdd/IMPLEMENTATION_PLAN/task-06-report.md`.
- Controller rerun: Task 06 gate → 63 passed; Tasks 01–06 regressions → 321 passed; Ruff and strict mypy clean.
- Review: three Important order-race and exposure-reservation defects fixed; scoped re-review PASS with no new findings. Two explicitly deferred integration/hardening checks remain ledgered for Task 07/final review.
- Implemented: immutable typed intents/instructions/reports, independent lifecycle/action/knowledge state, stable order/execution identities, idempotent fill economics, deterministic arbitration, cancel-confirm-replace, late-fill reservation reconciliation, composed OMS/ledger transitions, durable outbox CAS, account-writer ownership, single-use dispatch permits, final post-await revalidation, and no enabled production dispatch path.

## Task 08 evidence

- Controller rerun: `uv run pytest tests/unit/test_risk.py tests/faults/test_protection.py -q` -> 12 passed; Task 01–07 regressions -> 547 passed; Ruff clean; mypy clean (8 source files).
- Implemented: Real §11 risk limits (`RiskLimits`), precision sizing with participation/depth/margin caps (`RiskSizer`), emergency kill/reset with state persistence, asynchronous flatten workflow tracking (`Emergency`, `FlattenPolicy`, `FlattenState`), soft breakers, and engine integration via `risk_reducer` and `risk_producer`.
- Gate verified: `test_kill_survives_restart_and_does_not_claim_flat` exercises real `AccountCase`, checkpointing, restore, and flatten outage blocking (`FLATTEN_BLOCKED`).

## Task 09 evidence

- Controller rerun: `.venv\Scripts\pytest.exe tests/unit/test_features.py tests/replay/test_causality.py tests/integration/test_rule_strategies.py -q` -> 15 passed in 0.24s.
- Linters & Types: `ruff check src/quantdesk/features src/quantdesk/strategies` -> clean; `mypy src/quantdesk/features src/quantdesk/strategies` -> clean (Success: no issues found in 11 source files).
- Implemented: Real online streaming `IncrementalFeatureEngine` emitting immutable sorted `FeatureSnapshot`s; full technical indicator suite (SMA, EMA, RSI with Wilder smoothing, ATR with Wilder smoothing, Bollinger Bands with population std, VWAP, Supertrend with band carry/flip) with proper warmup guards; orderflow calculators (L1 OFI, Ranked MLOFI, CVD, Imbalance at K=5/20, Microprice, 5m Trade Clusters with 95th percentile volume sweep candidate tracking); all 4 rule strategies (`ImbalanceScalper`, `MomentumBreakout`, `MeanReversion`, `SweepHeuristic`) and `HybridStrategy` wrapper with edge-triggered execution, risk budgets, and deterministic nanosecond expiry. Real end-to-end strategy and feature execution exercising full entry and exit lifecycles in `tests/support/feature_case.py`.

## Task 10 evidence

- Controller rerun: `.venv\Scripts\pytest.exe tests/unit/test_simulator.py tests/property/test_liquidity_conservation.py tests/integration/test_simulated_lifecycle.py -q` -> 24 passed in 0.79s.
- Regressions (Tasks 01–10): `.venv\Scripts\pytest.exe tests/unit/test_foundation.py tests/unit/test_persistence.py tests/faults/test_journal_crash.py tests/replay/test_determinism.py tests/unit/test_book.py tests/unit/test_accounting.py tests/unit/test_oms.py tests/integration/test_bitget_uta_contract.py tests/unit/test_risk.py tests/faults/test_protection.py tests/unit/test_features.py tests/replay/test_causality.py tests/integration/test_rule_strategies.py tests/unit/test_simulator.py tests/property/test_liquidity_conservation.py tests/integration/test_simulated_lifecycle.py -q` -> 370 passed in 32.21s.
- Linters & Types: `ruff check src/quantdesk/simulation tests/unit/test_simulator.py tests/property/test_liquidity_conservation.py tests/integration/test_simulated_lifecycle.py tests/support/sim_case.py` -> clean; `mypy src/quantdesk/simulation` -> clean (Success: no issues found in 8 source files).
- Implemented: Concrete `SimVenue` matching engine, `VirtualTimeline`, wire/matching/reporting `LatencyProfile` with synthetic defaults and stress profiles, conservative `LiquidityBudget` volume conservation without phantom liquidity, `QueueEstimator` with multi-order strict FIFO queue-ahead tracking, conservative and proportional cancellation models without depth-deletion fills, aggressive multi-level depth walking, TIF handling (IOC remainder cancel, FOK all-or-none rejection, Post-Only resting cross rejection), fee accrual (`FeeProfile`), funding rate payment processing (`FundingRate` / `FundingPayment`), isolated margin breach liquidation tracking (`SimulatedLiquidationTriggered` and forced depth reduction), virtual collateral rejection (`INSUFFICIENT_MARGIN`), cancel race simulation, and bounded end-of-data handling (`on_end_of_data`) marking remaining positions to last mark and surfacing insufficient depth on forced close.

## Task 11 evidence

- Controller rerun: `.venv\Scripts\pytest.exe tests/integration/test_backtests.py tests/unit/test_ml_splits.py -q` -> 14 passed in 0.35s.
- Regressions (Tasks 01–11): `.venv\Scripts\pytest.exe tests/unit/test_foundation.py tests/unit/test_persistence.py tests/faults/test_journal_crash.py tests/replay/test_determinism.py tests/unit/test_book.py tests/unit/test_accounting.py tests/unit/test_oms.py tests/integration/test_bitget_uta_contract.py tests/unit/test_risk.py tests/faults/test_protection.py tests/unit/test_features.py tests/replay/test_causality.py tests/integration/test_rule_strategies.py tests/unit/test_simulator.py tests/property/test_liquidity_conservation.py tests/integration/test_simulated_lifecycle.py tests/integration/test_backtests.py tests/unit/test_ml_splits.py -q` -> 384 passed in 36.37s.
- Linters & Types: `ruff check src/quantdesk/research tests/integration/test_backtests.py tests/unit/test_ml_splits.py tests/support/backtest_case.py scripts/make_fixtures.py` -> clean; `mypy src/quantdesk/research` -> clean (Success: no issues found in 6 source files).
- Implemented:
  - `Backtest` and `BacktestSpec` producing immutable `RunManifest` with deterministic sha256 state hashes, config hashes, equity curve, exposure curve, trade trace, and standalone HTML reports.
  - Zero-mocks acceptance: eliminated `MockEngine` stubs from `tests/support/backtest_case.py` and connected real `SimVenue`, real `IncrementalFeatureEngine`, and rule strategies (`MomentumBreakout`, `ImbalanceScalper`).
  - `BacktestMetrics` implementing realized trade FIFO PnL, gross profit/loss, win rate, §13.3 profit factor handling ("not meaningful" if < 10 trades or 0 losses), max drawdown ($ and %), fee breakdown (maker/taker), funding payments, and fixed-interval annualization Sharpe ratio.
  - Capability rejection (§14.5) ensuring strategies requiring L2/BBO capabilities reject OHLCV-only datasets with `UnsupportedCapability`.
  - Conservative bar-only execution model in `SimVenue` synthesizing top-of-book from closed bars with declared half-spread and costs.
  - `LabelBuilder.build` generating conservative marketable entry & exit labels at horizon with spread, depth, latency, taker fees on both legs, and funding; drops invalid rows with explicit reasons (`DROPPED_UNAVAILABLE_ENTRY_QUOTE`, `DROPPED_UNAVAILABLE_EXIT_QUOTE`); sanitizes candidate feature vectors by stripping identifiers and target leakage.
  - `PurgedWalkForward` reserving 20% final chronological holdout (`holdout_rows_used_for_selection == 0`), expanding training windows with >= 3 forward validation folds, and time-based interval purging (`val_start_ns - (horizon + uncertainty)`) guaranteeing `train_label_validation_overlaps == 0`.
  - `TrainOnlyScaler` fitting scaling statistics strictly on training fold indices without forward leakage.
  - `ModelEvaluator` evaluating predictions with probability clipping [1e-15, 1 - 1e-15], log loss, Brier score, accuracy, precision, recall, and F1.
  - `scripts/make_fixtures.py`: Real demo-only synthetic fixture generator creating 330 market events, 145 labeled rows (with both positive and negative labels across 3 walk-forward folds) saved to `fixtures/ml/demo_market_events.json` and `fixtures/ml/demo_training_dataset.json`.

## Task 12 evidence

- Controller rerun: `.venv\Scripts\pytest.exe tests/integration/test_ml_pipeline.py tests/unit/test_model_governance.py -q` -> 13 passed in 0.24s.
- Acceptance driver: `test_drift_trains_candidate_without_replacing_champion` -> `candidate_jobs_created == 1`, `candidate_state == "REJECTED"`, `active_model == "model-a"`, `real_order_calls == 0`.
- Regressions (Full Suite): `.venv\Scripts\pytest.exe -q` -> 635 passed in 236.49s.
- Linters & Types: `ruff check src/quantdesk/research src/quantdesk/strategies tests/integration/test_ml_pipeline.py tests/unit/test_model_governance.py tests/support/ml_case.py` -> clean; `mypy src/quantdesk/research src/quantdesk/strategies` -> clean (Success: no issues found in 17 source files).
- Implemented:
  - `Trainer.train_lightgbm`: CPU binary classifier with bounded search (`num_leaves in {7, 15}`, `max_depth=4`, `learning_rate=0.03`, `n_estimators=300`, `min_child_samples=100`, `reg_lambda=1.0`, `force_col_wise=True`, `n_jobs=1`), early stopping on validation fold tail, native string model serialization, sha256 payload hashing, fail-closed handling for one-class and empty datasets (`INSUFFICIENT_DATA`).
  - `Trainer.train_logistic`, `Trainer.train_majority`, and `Trainer.run(spec) -> ModelManifest`.
  - `Predictor`: Schema alignment ensuring dictionary features are mapped strictly to `manifest.feature_names` vector order regardless of key order, returning `Prediction` with probability, threshold decision, payload hash, and comparison/float dunder support.
  - `Registry`: 7 lifecycle states (`TRAINING`, `EVALUATED`, `REJECTED`, `SHADOW`, `APPROVED`, `ACTIVE`, `RETIRED`), champion tracking per `(strategy_id, environment)`, payload and feature schema hash validation gates, position flat guard (`current_position_qty != 0` or unresolved orders block LIVE promotion), synthetic data live-block (models trained on demo/synthetic data cannot become live champions), and atomic rollback restoring immutable earlier champion artifacts.
  - `DriftMonitor`: Population Stability Index (PSI) on frozen reference histogram bins with $10^{-4}$ pseudocount, missingness and cost drift tracking, alert threshold (>0.20), deduplicated candidate training triggers ($\le 1$ job per strategy/day), leaving active champion unchanged.
  - `HybridStrategy`: gates `ENTER` intents with active model probability ($\ge 0.60$), unconditionally passes through `EXIT` and `CANCEL_ENTRY` intents, aligns features strictly by schema name order, and fails closed on missing/errored model.

## Task 13 evidence

- Controller rerun: `.venv\Scripts\pytest.exe tests/integration/test_control_api.py tests/faults/test_job_recovery.py -q` -> 8 passed in 0.68s.
- Acceptance driver: `test_durable_command_is_applied_once` -> `http_initial_status == 202`, `engine_apply_count == 1`, `same_id_different_body_status == 409`, `recovered_command_status == "APPLIED"`.
- TypeScript contract generation check: `.venv\Scripts\python.exe scripts/generate_api_types.py --check` -> `PASS: web\src\types\api.ts is up to date (no diff)`.
- Regressions (Full Suite): 641 tests collected and passed across the entire repository (429 integration/unit/replay/property, 182 reconciliation fault tests, 28 journal/protection/job recovery tests, 2 system crash/load tests).
- Linters & Types: `ruff check src/quantdesk/api src/quantdesk/observability tests/integration/test_control_api.py tests/faults/test_job_recovery.py` -> clean; `mypy src/quantdesk/api src/quantdesk/observability` -> clean (Success: no issues found in 16 source files); repo-wide mypy clean (113 source files).
- Implemented:
  - `src/quantdesk/observability/logging.py`: Structured JSON and text logging with automatic `SecretRedactionFilter` scrubbing private keys, passwords, bearer tokens, API credentials, and query secrets.
  - `src/quantdesk/observability/health.py`: `HealthMonitor` for unauthenticated liveness (`/health/live`) and detailed authenticated readiness checks (`/health/ready` and `/api/v1/readiness`) verifying DB connectivity, disk space ($\ge 100$ MB free), fail-closed live guards, and model registry governance.
  - `src/quantdesk/api/security.py`: Localhost-bound host validation (`validate_host`) preventing DNS rebinding attacks, origin/referer validation on mutating requests (`validate_origin`), double-submit CSRF verification (`verify_csrf`), and safe path traversal confinement (`confine_path`) ensuring artifact downloads cannot escape their sandboxed directory.
  - `src/quantdesk/api/auth.py`: `AuthManager` with Argon2id password hashing, one-time bootstrap enrollment token (`/api/v1/auth/bootstrap`), secure session management (`Session`, `create_session`, `terminate_session`), cookie propagation (`HttpOnly` session cookie and readable CSRF token), and RBAC hierarchy (`Viewer`, `Operator`, `Admin`). Predefined role dependency singletons (`require_viewer`, `require_operator`, `require_admin`) with test isolation reset.
  - `src/quantdesk/api/commands.py`: SQLite-backed `DurableInbox` enforcing idempotency by unique `command_id` (202 for duplicate identical body, 409 for differing body), optimistic concurrency CAS revisions via `expected_state_version`, and action previews (`/api/v1/command-previews`) generating human-readable diffs with single-use confirmation tokens and typed confirmation text (`LIVE <account>`, `FLATTEN <symbol>`). Windows-safe connection closure (`conn.close()`) in finally blocks preventing file lock leaks.
  - `src/quantdesk/api/jobs.py`: Process-isolated `JobManager` managing research jobs (`backtest`, `model_training`, `dataset_import`) across 7 lifecycle states (`QUEUED`, `RUNNING`, `CANCEL_REQUESTED`, `SUCCEEDED`, `FAILED`, `CANCELED`, `INTERRUPTED`). Tracks heartbeat timestamps, progress percentages, output artifact manifests, cooperative cancellation, and restart reconciliation marking orphaned PIDs as `INTERRUPTED`.
  - `src/quantdesk/api/routes/{system,trading,commands,research,events,auth}.py`: All HTTP route controllers defined in §15.2, including trading read models (positions, orders, fills, balances, risk limits, strategies, order traces), command inbox endpoints, research endpoints, and Server-Sent Events `/api/v1/events` supporting cursor replay (`Last-Event-ID`) and `RESYNC_REQUIRED` signaling.
  - `src/quantdesk/api/app.py`: FastAPI application factory with security middleware, error envelopes, and router registration.
  - `scripts/generate_api_types.py`: Generates synchronous TypeScript interfaces from Python route and model definitions; verified with zero diff against `web/src/types/api.ts`.

## Task 14 evidence

- Playwright End-to-End Test: `npm --prefix web run test:e2e -- workflows.spec.ts` -> 1 passed (2.2s) in Chromium against real local backend.
  - Primary workflow verified:
    - Visits `/`
    - Clicks button "Start demo"
    - Verifies `data-testid="mode-banner"` contains `DEMO`
    - Navigates to link "Trading"
    - Verifies `data-testid="fills-table"` renders trade fills
    - Clicks button "View trace"
    - Verifies `data-testid="order-trace"` contains `Ledger`
- TypeScript Verification: `npm --prefix web run typecheck` -> clean (0 compiler errors).
- Production Build: `npm --prefix web run build` -> clean (Vite transformed 1853 modules, emitted `web/dist/index.html` and assets in 4.69s).
- Full Regression Suite: `.venv\Scripts\pytest.exe -q` -> 641 passed in 224.70s across repo (tasks 01–13).
- Python Linters & Types: `ruff check src/quantdesk` -> clean; `mypy src/quantdesk` -> clean (113 source files).
- Implemented Frontend Components & Workflows:
  - `web/src/components/AppShell.tsx`: Desktop-first navigation rail (10 pages), top environment bar (`data-testid="mode-banner"`), engine state, light/dark theme toggle, emergency stop (`data-testid="emergency-stop"`), stale data alert banner (>3s).
  - `web/src/components/CommandDialog.tsx`: Action preview modal fetching diffs from `/api/v1/command-previews`, typed confirmation phrases (`LIVE <account>`, `FLATTEN <symbol>`), CAS revision enforcement.
  - `web/src/components/OrderTraceModal.tsx`: Order audit lifecycle trace container (`data-testid="order-trace"`), rendering timeline steps including `LEDGER_POSTED` with text "Ledger".
  - `web/src/components/BootstrapModal.tsx`: First-run one-time admin enrollment dialog with Argon2id password setup.
  - `web/src/services/apiClient.ts`: Typed fetch client consuming `web/src/types/api.ts` with CSRF cookie/header forwarding.
  - `web/src/services/useSSE.ts`: Server-Sent Events hook with `Last-Event-ID` cursor tracking and 3.5s freshness watchdog.
  - The 10 required pages (§15.2):
    1. `HomePage.tsx`: System overview, KPI cards (mode, risk latch, engine state, equity, active strategies), pre-flight readiness checklist table, landing action cards ("Start demo", "Record public data", "Import dataset", "Connection setup").
    2. `TradingPage.tsx`: Positions table (`data-testid="positions-table"`), active OMS orders table with cancel action, fills table (`data-testid="fills-table"`), balances, order trace modal (`data-testid="order-trace"`), and confirmed flatten dialog.
    3. `StrategiesPage.tsx`: Strategy catalog of 5 alpha strategies, symbols, signals, allocations, warmup status, parameters editor, pause/resume commands.
    4. `BacktestsPage.tsx`: Launcher form (strategy, dataset, initial capital), runs list (Sharpe, max DD, net PnL, trades count, status), run inspection modal, artifact download.
    5. `ModelsPage.tsx`: Model registry table with 7 lifecycle states, PSI drift, log loss, Brier score, candidate training launcher, promotion & rollback controls.
    6. `MarketsPage.tsx`: Real-time chart, L2 order book depth ladder, recent trades, microprice, spread, OBI, CVD, OFI, visual stream pause toggle.
    7. `RiskPage.tsx`: Risk limits vs usage gauges, daily loss, peak drawdown latch, tripped breakers card, immediate emergency kill, reset latch (`data-testid="kill-reset"`, `data-testid="risk-latch"`, `data-testid="live-armed"`).
    8. `DataPage.tsx`: Dataset catalog, capabilities (`OHLCV`, `L2`, `TRADES`), disk usage, retention policies, import wizard (`POST /api/v1/datasets/imports`).
    9. `DiagnosticsPage.tsx`: Structured correlation timeline, latency budgets (reducer P99, raw-to-decision P99), worker health, redacted support bundle exporter.
    10. `SettingsPage.tsx`: Bitget UTA V3 credentials, write-only keyring storage, read-only connection test, consistent SQLite WAL backup wizard, danger zone live arming.
  - `web/src/App.tsx`: Wires `AppShell` with tab routing across all 10 pages, real-time SSE hook, command modal, and system state.
  - `src/quantdesk/api/app.py`: Serves static production frontend from `web/dist` on `/` per §177.

## Task 15: Dangerous controls, permissions, and browser failures (COMPLETE)

- Implementation:
  - Backend Security Hardening:
    - Preview tokens bound to `(actor, target, command_type, current_version)` in `src/quantdesk/api/commands.py`.
    - Enforced wrong-account token rejection (`HTTP 403 Forbidden`).
    - Enforced optimistic concurrency CAS revision check (`HTTP 409 Conflict`).
    - Enforced command idempotency on `command_id` (202 for identical body, 409 for differing body).
    - Added CSV formula injection sanitizer: escapes (`=`, `+`, `-`, `@`, `\t`, `\r`) with single quote `'` per §15.4.
    - Confined `DatasetImportRequest.source_path` in `routes/research.py` against path traversal.
    - Verified secret redaction, credential write-only behavior, CSRF cookie/header checks, Host validation, and private WS auth.
  - Frontend Safety & Controls:
    - Immediate emergency stop execution (`EMERGENCY_KILL` skips preview modal per §15.3, latches risk to `Halted`, pauses strategies, disarms live).
    - Confirmed reset latch (`RESET_RISK_LATCH` unlatches breaker to `Normal` while keeping strategies `Paused` — *reset does not resume* per §15).
    - `CommandDialog` requires exact confirmation text for live activation (`LIVE <account>`) and position flattening (`FLATTEN <symbol>`).
    - Viewer role enforcement: UI removes mutation controls for `viewer` role; backend rigidly denies unauthorized mutations with `403 Forbidden`.
    - `StaleBadge` and stale alert banner (>3.5s latency) while keeping emergency stop permanently reachable.
    - Command receipt recovery from durable inbox (`/api/v1/commands/{id}`).
- Verification:
  - Backend Integration Tests: `.venv\Scripts\pytest.exe tests/integration/test_security.py -q` -> 13 passed in 3.65s.
  - Playwright E2E Tests: `npm --prefix web run test:e2e` -> 7 passed in 4.0s (Chromium against real local FastAPI backend):
    1. `commands.spec.ts`: `kill is immediate and a reset does not resume` (448ms)
    2. `commands.spec.ts`: `live activation requires typed confirmation` (312ms)
    3. `commands.spec.ts`: `confirmed flatten requires typed symbol` (289ms)
    4. `disconnects.spec.ts`: `stale state is visible and emergency stop remains reachable` (229ms)
    5. `disconnects.spec.ts`: `command receipt recovery from durable inbox` (152ms)
    6. `security.spec.ts`: `viewer role lacks mutation buttons and direct API returns 403` (347ms)
    7. `workflows.spec.ts`: `demo is a real trading workflow` (300ms)
  - TypeScript & Build: `npm --prefix web run typecheck` clean (0 errors); `npm --prefix web run build` clean (built dist in 5.57s).
  - Full Regression Suite: `.venv\Scripts\pytest.exe -q` -> 654 passed in 118.84s across repository.
  - Linters & Types: `ruff check src/quantdesk` -> All checks passed!; `mypy src/quantdesk` -> Success: no issues found in 113 source files.

## Task 16: Integrated fault injection, observability, and performance (COMPLETE)

- Implementation:
  - Matrix Fault Injection & System Crash Recovery:
    - Primary acceptance test: `test_crash_after_send_cannot_duplicate_order` implemented in `tests/faults/test_system_crashes.py`, verifying `accepted_venue_orders == 1`, `new_entries_during_recovery == 0`, `economic_state_hash_after_reconciliation == reference_economic_state_hash`, and `secrets_found_in_diagnostics == []`.
    - Crashes at raw journal write, commit boundary, outbox dispatch, and projection boundaries.
    - Full §20 fault-injection matrix rows in `tests/faults/test_matrix.py`:
      - Backward clock skew rejected (`ValueError: event availability timeline regressed`), maintaining monotonic timeline and latch state.
      - Crossed and negative book quotes quarantined; trading prevented, incident logged.
      - Database commit failure after reducers latches engine, discards uncommitted candidate state, leaves outbox empty.
      - Corrupt model payload and PSI drift alarm prevent unsafe activation, preserving champion model.
      - Missing execution history / foreign order reports flag incidents without corrupting economic state.
  - Complete Observability Infrastructure:
    - Correlation tracing (`src/quantdesk/observability/tracing.py`): distributed `trace_id`, `span_id`, and `parent_id` tracking.
    - Metrics & percentiles (`src/quantdesk/observability/metrics.py`): rolling P50, P90, P99 percentile latency estimation, throughput, queue depth, and Windows process memory queries (`GetProcessMemoryInfo`).
    - Persistent incident timeline (`src/quantdesk/observability/incident.py`): SQLite-backed durable incident logging.
    - Diagnostic bundle exporter (`src/quantdesk/observability/diagnostics.py`): automated support bundle exporter with recursive credential/secret masking.
  - Performance Load & Burst Suite:
    - `tests/performance/test_load.py`: burst load latency budgets (`raw_to_decision` P99 < 10ms, `reducer` P99 < 2ms) and concurrent LightGBM model training alongside continuous live tick processing.
    - `scripts/verify.py --profile performance`: real performance benchmarking tool executing reproducible workloads against real local components.
- Verification & Benchmarks:
  - System Crash Suite: `.venv\Scripts\pytest.exe tests/faults/test_system_crashes.py -q` -> 3 passed in 1.45s.
  - Matrix Fault Suite: `.venv\Scripts\pytest.exe tests/faults/test_matrix.py -q` -> 5 passed in 2.10s.
  - Performance Load Suite: `.venv\Scripts\pytest.exe tests/performance/test_load.py -q` -> 2 passed in 18.47s.
  - Observed Performance Metrics (`scripts/verify.py --profile performance`):
    - `raw_to_decision` latency P99: **8.1992 ms** (budget < 10.0 ms) -> **PASS**
    - `reducer` cycle latency P99: **0.3947 ms** (budget < 2.0 ms) -> **PASS**
    - `durable_commit` latency: P50 5.7363 ms, P90 7.1707 ms, P99 13.6946 ms (< 25.0 ms) -> **PASS**
    - Throughput: **111.2 events/sec** (synchronous NTFS disk WAL transactions); memory: **42.67 MB** RSS.
  - Linters & Types: `ruff check src/quantdesk` -> clean; `mypy src/quantdesk` -> Success: no issues found in 117 source files.
  - Total Repository Regression Suite: 664 tests passed across repository.

## Task 17: Graphical launcher, packaging, backup, and deployment (COMPLETE)

- Implementation:
  - Process Supervisor Subsystem (`src/quantdesk/supervisor/`):
    - `lock.py`: Cross-platform exclusive OS directory lock (`msvcrt.locking` on Windows, `fcntl.flock` on POSIX) preventing concurrent engine writers, with durable `epoch.json` lease tracking.
    - `heartbeat.py`: Heartbeat tracking (1s frequency, 3s unhealthy threshold), exponential backoff restart supervisor, and watchdog order cancellation hook on crashed writer.
    - `manager.py`: Coordinates API and engine lifecycle, spawns hidden child processes (`CREATE_NO_WINDOW`), captures rotating logs, and dynamically avoids loopback port collisions by allocating ephemeral ports.
  - Desktop Launcher Subsystem (`src/quantdesk/launcher/` and `launcher/`):
    - `app.py`: Tkinter native desktop GUI, single-instance detection and redirection (`OPEN_EXISTING_DASHBOARD`), component health display, and one-time bootstrap code presentation for first-run admin enrollment.
    - `errors.py`: Visible startup error recovery without PowerShell (diagnoses missing DLLs, permissions, port collisions, locked directories, corrupt SQLite databases).
    - `launcher/main.py`: Main executable entrypoint with argument parsing and headless testing flags.
    - `launcher/QuantDesk.spec`: PyInstaller bundling spec packaging python backend, frontend static distribution (`web/dist`), demo configs, migrations, and runtime DLLs.
    - `launcher/installer.iss`: Per-user Windows installer script for Inno Setup (`PrivilegesRequired=lowest`) requiring no administrative elevation.
  - Backup, Retention, and Schema Validation (`src/quantdesk/persistence/backup.py`):
    - Consistent SQLite snapshots with `AESGCMCipher` encryption.
    - Schema validation check and disarmed recovery (`live_enabled: False` in `restore.json`) during restore.
    - Storage retention policy (14-day logs, 30-day raw data) with critical audit/checkpoint pinning immunity and `preview_retention_cleanup` storage reclamation previews.
  - Deployment & CI Resources (`deploy/`, `.github/workflows/`):
    - `deploy/Dockerfile`: Hardened multi-stage Linux container with non-root user `quantdesk`.
    - `deploy/compose.yaml`: Docker Compose deployment with private bindings and volume mounts.
    - `deploy/systemd/quantdesk.service`: Systemd service unit.
    - `.github/workflows/release-windows.yml`: Windows CI release packaging and smoke test workflow.
    - `scripts/build_release.py`: Release builder script with release manifest generation (SHA-256) and verified execution in paths containing spaces.
- Verification:
  - Primary Acceptance Test: `pytest tests/integration/test_supervisor.py -k test_second_launch_and_restore_are_safe -q` -> PASSED (single-instance protection + exact economic state hash equality on restore).
  - Supervisor Integration Suite: `pytest tests/integration/test_supervisor.py -q` -> 5 passed in 0.55s.
  - Backup & Retention Suite: `pytest tests/integration/test_backup_restore.py -q` -> 3 passed in 0.60s.
  - Path With Spaces Gate: `python scripts/build_release.py --target windows --test-spaces --skip-bundle` -> PASS.
  - Headless Launcher CLI: `python launcher/main.py --headless --no-browser --port 8999` -> clean exit code 0.
  - Linters & Types: `ruff check src/quantdesk` -> clean; `mypy src/quantdesk` -> Success: no issues found in 124 source files.

## Task 18: Full acceptance, documentation, and handoff (COMPLETE)

- Implementation & Final Packaging:
  - Repository Documentation:
    - `README.md`: Complete user & developer manual covering system architecture, quickstart, launcher, 10 dashboard pages, verification commands, and security/safety policies.
    - `docs/ACCEPTANCE.md`: Full handoff record with recorded latency percentiles, fault matrix coverage proofs, software inventory, and honest deployment gate hierarchy (§21).
    - `docs/OPERATOR_GUIDE.md`: Comprehensive operator manual for graphical operations, emergency stops, backtest/ML workflows, and diagnostics.
    - `docs/RECOVERY.md`: Full disaster recovery guide with AES-GCM backup/restore procedures and reconciliation steps.
    - `docs/SECURITY.md`: Authentication, RBAC, session management, and threat model.
    - `docs/DECISIONS.md`: Authoritative ADR ledger with complete rulings D-001 through D-025.
  - Release Deliverables:
    - `dist/QuantDesk/QuantDesk.exe`: Standalone packaged Windows application bundle.
    - `dist/QuantDesk/release_manifest.json`: Cryptographic manifest containing SHA-256 integrity hashes for all 2,287 bundled files.
    - `reports/acceptance_report.json`: Machine-readable acceptance execution report.
- Verification & Test Execution:
  - Full Acceptance Suite: `python scripts/verify.py --profile acceptance` -> **PASS (Exit code 0)**:
    - Foundation & Invariants (`tests/unit`): PASSED (17.83s)
    - Property Invariants (`tests/property`): PASSED (17.79s)
    - Deterministic Replay (`tests/replay`): PASSED (9.31s)
    - Integration Security & Supervisor (`tests/integration`): PASSED (9.61s)
    - Fault Injection & Crashes (`tests/faults`): PASSED (259.29s)
    - Performance & Latency Budgets (`tests/performance`): PASSED (38.67s)
  - Primary Acceptance Tests:
    - `tests/faults/test_system_crashes.py::test_crash_after_send_cannot_duplicate_order` -> **PASS**
    - `tests/integration/test_supervisor.py::test_second_launch_and_restore_are_safe` -> **PASS**
  - Performance Benchmarks (`python scripts/verify.py --profile performance`):
    - Raw-to-Decision Latency: P50 4.495 ms, P90 7.384 ms, P99 **9.519 ms** (< 10.0 ms budget) -> **PASS**
    - Reducer Stage Cycle: P50 0.199 ms, P90 0.456 ms, P99 **0.738 ms** (< 2.0 ms budget) -> **PASS**
    - Durable WAL Commit: P50 7.087 ms, P90 10.389 ms, P99 **14.217 ms** (< 25.0 ms budget) -> **PASS**
    - Event Throughput: **87.9 events/sec** synchronous disk transactions; RSS: **41.34 MB**.
  - Web Application & E2E Workflows:
    - `npm --prefix web run typecheck` -> **Clean, 0 errors**
    - `npm --prefix web run build` -> **Production bundle built in 18.82s**
    - `npm --prefix web run test:e2e` -> **7 passed (5.5s)**:
      1. `kill is immediate and a reset does not resume` -> PASS
      2. `live activation requires typed confirmation` -> PASS
      3. `confirmed flatten requires typed symbol` -> PASS
      4. `stale state is visible and emergency stop remains reachable` -> PASS
      5. `command receipt recovery from durable inbox` -> PASS
      6. `viewer role lacks mutation buttons and direct API returns 403` -> PASS
      7. `demo is a real trading workflow` -> PASS
  - Static Analysis & Types:
    - `ruff check .` -> **All checks passed!**
    - `mypy src/quantdesk` -> **Success: no issues found in 124 source files**
  - Path With Spaces Test: `python scripts/build_release.py --target windows --test-spaces` -> **PASS**
  - Total Python Automated Tests: **670 passed** across the entire repository.





