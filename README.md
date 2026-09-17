# QuantDesk

> **Deterministic, auditable trading research and execution framework with zero-mock local core, exact accounting, high-fidelity simulation, and single-instance desktop operations.**

QuantDesk is an institutional-grade algorithmic trading and quantitative research platform designed for Windows and Linux environments. It couples a strict single-writer deterministic event-driven core with an asynchronous control plane and a complete 10-page browser trading workspace.

---

## 1. Core Architectural Invariants

- **Single-Writer Event Processing**: A dedicated thread/process exclusively owns each trading account's state. External market feeds and user instructions enter an append-only journal before reaching the deterministic state machine.
- **Exact Decimal Accounting**: All cash, margin, fees, funding, and order reservations use exact `Decimal` financial arithmetic under strict double-entry ledger rules. Binary floating point is strictly quarantined to feature transformations and ML models.
- **Idempotent Fenced Dispatch**: Outbox dispatch enforces monotonic fencing tokens and durable client order IDs. A process crash after execution submission or database commit can never trigger duplicate orders or unrecorded fills.
- **Native Protection & Emergency Recovery**: Bracket stops and take-profits are tracked through explicit state transitions (`UNARMED`, `ARMING`, `ARMED`, `TRIGGERED`). One-click emergency actions (`KILL`, `FLATTEN`, `PAUSE`) execute deterministically and fail closed.
- **Process-Isolated ML Research**: LightGBM model training, purged walk-forward cross-validation, and PSI feature drift monitoring execute in worker subprocesses, preventing CPU starvation of the real-time core.
- **Single-Instance Desktop Operations**: Native Windows launcher enforces exclusive OS file locks (`msvcrt.locking`), heartbeat watchdog monitoring, automatic port allocation, and graphical startup error recovery without requiring a terminal.

---

## 2. System Architecture

```
                                  +---------------------------------------+
                                  |     Desktop Launcher / Supervisor     |
                                  | (Tkinter GUI / Headless CLI / Watchdog)|
                                  +-------------------+-------------------+
                                                      |
                         +----------------------------+---------------------------+
                         |                                                        |
                         v                                                        v
       +------------------------------------+                  +------------------------------------+
       |         FastAPI Control Plane      |                  |     Deterministic Engine Core      |
       |  - Versioned read-models (SQLite)  |                  |  - Single-writer event queue       |
       |  - Durable command inbox (CAS)     |                  |  - Append-only event journal       |
       |  - Subprocess research workers     |                  |  - Book, features, alpha rules     |
       |  - Bearer JWT / RBAC / CSRF fence  |                  |  - OMS, double-entry ledger, risk  |
       +-----------------+------------------+                  +------------------+-----------------+
                         |                                                        |
                         v                                                        v
       +------------------------------------+                  +------------------------------------+
       |       Browser UI (React / Vite)    |                  |        Async Venue Adapters        |
       |  - 10 operational pages            |                  |  - Bitget UTA V3 (Isolated Margin) |
       |  - SSE live streaming              |                  |  - Real-time reconnect & recovery  |
       |  - One-click emergency flatten/kill|                  |  - Deterministic venue emulator    |
       +-----------------+------------------+                  +------------------------------------+
```

---

## 3. Quickstart Guide

### 3.1 Prerequisites

- **Python**: 3.12 (Standard CPython)
- **Node.js**: 20+ and `npm`
- **Operating System**: Windows 10/11 x64 (Native target) or Linux (Ubuntu 22.04 LTS / Debian 12)

### 3.2 Installation & Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/riper/quantdesk.git
   cd quantdesk
   ```

2. **Initialize Python virtual environment**:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate      # On Windows
   # source .venv/bin/activate  # On Linux
   pip install -e ".[dev]"
   ```

3. **Install and build web frontend**:
   ```bash
   cd web
   npm ci
   npm run build
   cd ..
   ```

### 3.3 Running the Application

#### Option A: Native Desktop GUI Launcher (Recommended for Operators)
Launch QuantDesk with the visual launcher:
```bash
python launcher/main.py
```
- Automatically checks environment prerequisites (Python runtime, ports, directory locks).
- Prompts with a **One-Time Bootstrap Code Card** during first run for admin credential setup.
- Spawns backend and engine processes hidden (`CREATE_NO_WINDOW`) and opens default browser to `http://127.0.0.1:8000`.
- Second launch instances automatically redirect to the active dashboard (`OPEN_EXISTING_DASHBOARD`).

#### Option B: Headless / Server Mode (Recommended for CI / Linux)
```bash
python launcher/main.py --headless --no-browser --port 8000
```

#### Option C: Docker Container Deployment
```bash
docker compose -f deploy/compose.yaml up -d
```

---

## 4. Operational Workflows & The 10 Dashboard Pages

QuantDesk features 10 fully interactive operational pages:

1. **Dashboard (`/`)**: Real-time portfolio equity, PnL, venue connectivity, and immediate emergency controls (`KILL`, `FLATTEN`, `PAUSE`).
2. **Execution & Blotter (`/execution`)**: Real-time order grid, active fills, open positions, bracket stops, and manual order dispatch.
3. **Strategies (`/strategies`)**: Status of all 4 baseline rule strategies (`ImbalanceMomentum`, `CvdDivergence`, `BasisCarry`, `MicrostructureReversal`) and the optional ML hybrid gate.
4. **Risk & Controls (`/risk`)**: Dynamic exposure meters, margin headroom, drawdown gauges, rate limiters, and emergency circuit breakers.
5. **Ledger & Accounting (`/ledger`)**: Double-entry journal entries, real-time trial balance, fee/funding reconciliation, and CSV export.
6. **Market Data & Book (`/market`)**: L2 50-level real-time depth ladder, trades stream, order book imbalance (OBI), and cumulative volume delta (CVD).
7. **Backtesting & Simulation (`/backtest`)**: High-fidelity backtester with fee/slippage modeling, queue position tracking, and equity curve visualizations.
8. **Research & ML Governance (`/research`)**: LightGBM model training, purged walk-forward cross-validation, feature drift (PSI) monitoring, and model registry promotion.
9. **Diagnostics & Timeline (`/diagnostics`)**: Persistent incident timeline, stage latency percentiles (P50/P90/P99), and redacted diagnostic bundle export.
10. **Settings & Maintenance (`/settings`)**: System credentials, API tokens, storage retention policies, deletion previews, and encrypted backup/restore wizards.

---

## 5. Verification & Acceptance Testing

The repository contains zero mocked core components: all tests execute against real in-memory/disk SQLite engines, real single-writer state machines, and real mathematical models.

### 5.1 Running the Full Acceptance Profile
```bash
python scripts/verify.py --profile acceptance
```
Executes all unit, property, replay, integration, fault injection, and performance suites, generating a machine-readable report at `reports/acceptance_report.json`.

### 5.2 Performance & Latency Benchmarks
```bash
python scripts/verify.py --profile performance
```
Measures real observed latencies under synchronous SQLite WAL transactions on Windows NTFS:
- **Raw-to-Decision Latency**: Observed P99 **8.199 ms** (Budget: < 10.0 ms)
- **Reducer Cycle**: Observed P99 **0.395 ms** (Budget: < 2.0 ms)
- **Durable Commit (WAL fsync)**: Observed P99 **13.695 ms** (Budget: < 25.0 ms)

### 5.3 Building Release Bundles
```bash
python scripts/build_release.py --target windows --test-spaces
```
Packages the entire application into a standalone executable bundle in `dist/QuantDesk/` with a SHA-256 verified `release_manifest.json` and tests execution inside directories with spaces.

---

## 6. Deployment Gate Hierarchy (§21)

| Gate | Name | Requirements | Verification Status |
|---|---|---|---|
| **G0** | **Offline Build** | All unit, property, replay, fault, UI, and performance tests pass locally with packaging verification. | **PASS** |
| **G1** | **Public Paper** | 24 hours of continuous public feed recording on target symbols with zero missing sequences. | **NOT_RUN** (Requires 24h continuous data stream) |
| **G2** | **Venue Protocol** | Bitget UTA V3 book reconstruction, idempotency, order recovery, and native protection verified. | **PASS** (Verified via emulator and contract tests) |
| **G3** | **Research & Shadow** | 7 calendar days shadow/paper and 200+ decisions evaluated under drift monitoring. | **NOT_RUN** (Requires 7-day soak period) |
| **G4** | **Tiny Live** | Dedicated isolated Bitget UTA account credentials and manual operator multi-factor arming. | **BLOCKED** (Deliberately disarmed in DEMO) |
| **G5** | **Broader Live** | Multi-week audit of live fill quality, slippage reconciliation, and risk compliance. | **BLOCKED** |

---

## 7. Safety & Operational Disclaimers

> [!CAUTION]
> **FAIL-CLOSED POLICY & LIVE TRADING SAFEGUARDS**
>
> QuantDesk defaults to `DEMO` mode with `live_enabled = False`.
> - Real money execution (`LIVE`) cannot be activated without explicit multi-factor typed confirmation and an isolated Bitget UTA account profile (`accountLevel=isolated`, `holdMode=one_way_mode`).
> - The software is provided for research and systematic execution evaluation. Software test passage does not guarantee trading profitability or eliminate market risk.
> - Never commit exchange API secrets or private key material to source control.

---

## 8. Documentation Index

Detailed specifications and operator references are located in `docs/`:

- [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) — Authoritative product and engineering specification.
- [OPERATOR_GUIDE.md](docs/OPERATOR_GUIDE.md) — Daily operator manual, dashboard walkthrough, and troubleshooting.
- [ACCEPTANCE.md](docs/ACCEPTANCE.md) — Recorded benchmark metrics, fault matrix proofs, and release evidence.
- [SECURITY.md](docs/SECURITY.md) — RBAC model, credential encryption, and threat analysis.
- [RECOVERY.md](docs/RECOVERY.md) — Disaster recovery procedures, encrypted backup restoration, and state reconciliation.
- [DECISIONS.md](docs/DECISIONS.md) — Authoritative architecture decision record.
- [VENUE_CONTRACT.md](docs/VENUE_CONTRACT.md) — Bitget UTA V3 protocol specification, error mappings, and WebSocket schemas.
- [BUILD_STATUS.md](docs/BUILD_STATUS.md) — Historical milestone tracking and task-by-task evidence.
