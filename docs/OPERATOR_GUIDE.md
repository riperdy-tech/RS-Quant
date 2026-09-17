# QuantDesk Operator Guide

Authoritative plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

---

## 1. Introduction & Operating Philosophy

QuantDesk is a local-first, deterministic quantitative trading platform and algorithmic execution workstation. It enforces strict separation between:
- **Supervisor**: process management, directory locking, port allocation, health monitoring.
- **Account Engine**: single-writer deterministic state transitions, journal persistence, order lifecycle, double-entry accounting.
- **Control API**: authenticated web interface, durable command inbox, background research jobs.
- **Research Workers**: process-isolated workers running ML backtests and model training without live keys.

### 1.1 Core Invariants
- **Default to DEMO**: The platform always boots disarmed in `DEMO` mode.
- **Fail-Closed**: Any integrity violation, corrupted feed quote, missing verification, or network disconnect trips the risk latch to `Halted`.
- **Browser Closure Does Not Stop Trading**: Closing your web browser tab does **not** terminate the account engine or supervisor. Trading continues until explicitly stopped via the launcher or the Emergency Stop button.

---

## 2. Installation and First Launch

### 2.1 Windows Installation
- **Installer**: Run `QuantDesk-Setup-0.1.0.exe`.
- **Per-User Scope**: Installs to `%LOCALAPPDATA%\Programs\QuantDesk` without requiring Windows UAC administrator elevation.
- **Data Directory**: Runtime journals, SQLite databases, datasets, and models are stored under `%LOCALAPPDATA%\QuantDesk\data`.

### 2.2 Starting the Desktop Launcher
Launch QuantDesk from the Desktop shortcut or Start Menu:
1. The **QuantDesk Desktop Launcher** window appears.
2. The supervisor acquires an exclusive OS directory lock (`.account.lock`) and durable ownership epoch.
3. If an existing QuantDesk instance is already active, the launcher detects it (`OPEN_EXISTING_DASHBOARD`), opens the current dashboard in your browser, and prevents duplicate engine processes.
4. If port 8000 is occupied by another application, the launcher dynamically allocates an available loopback port and records it in `active_instance.json`.
5. Your default browser opens automatically to `http://127.0.0.1:<port>`.

### 2.3 One-Time Bootstrap Admin Enrollment
On a brand new installation:
1. The desktop launcher prominently displays a **One-Time Bootstrap Code** (e.g. `e4f1a8c9b2...`).
2. The browser setup screen prompts for this code.
3. Enter the bootstrap code and define your master administrator password (hashed using Argon2id).
4. Once enrolled, the bootstrap token is permanently invalidated and destroyed.

---

## 3. Dashboard Navigation & Workspace Pages

The desktop web workspace consists of 10 dedicated workstations accessible via the left navigation rail:

1. **Home (`/`)**: Pre-flight system readiness checklist, engine state, active mode badge, portfolio equity KPI cards, and quick landing actions.
2. **Trading (`/trading`)**: Active positions, open OMS orders, real-time fills table, and order audit lifecycle modal with trace timeline.
3. **Strategies (`/strategies`)**: Alpha strategy catalog (EMA, Trend, Mean Reversion, Breakout, ML Hybrid), signal strength, allocation dials, and runtime parameter editors.
4. **Backtests (`/backtests`)**: Launch walk-forward backtests with purged folds, inspect Sharpe ratios, max drawdown curves, and export trade logs.
5. **Models (`/models`)**: Model registry displaying candidate/champion models, PSI feature drift, log loss, Brier calibration, and promotion/rollback controls.
6. **Markets (`/markets`)**: Real-time candlestick charts, L2 order book depth ladder, order book imbalance (OBI), cumulative volume delta (CVD), and order flow imbalance (OFI).
7. **Risk (`/risk`)**: Real-time risk gauges (daily loss, peak drawdown latch, margin tiers), tripped circuit breakers, and emergency stop controls.
8. **Data (`/data`)**: Parquet dataset catalog, schema capabilities (`OHLCV`, `L2`, `TRADES`), and dataset import wizard.
9. **Diagnostics (`/diagnostics`)**: Latency percentiles (raw-to-decision P99, reducer P99), process working set memory, structured correlation timeline, and support bundle exporter.
10. **Settings (`/settings`)**: Bitget UTA V3 API credentials (stored write-only to OS Keyring), read-only connection test, consistent backup wizard, and live arming danger zone.

---

## 4. Operational Safety Controls

### 4.1 Emergency Stop
- Click the prominent red **Emergency Stop** button located at the top right of the navigation bar.
- **Immediate Execution**: Emergency Stop executes immediately without preview delays.
- **Action**: Latches risk to `Halted`, pauses all running strategies, cancels open orders, and disarms live mode.

### 4.2 Resetting Breakers
- Once the underlying issue is diagnosed and cleared, click **Reset Breaker**.
- Enter confirmation. The risk latch returns to `Normal`.
- **Reset-Without-Resume**: Resetting the breaker **never** automatically resumes strategy order submission. Strategies remain strictly `Paused` until an operator manually resumes each strategy.

### 4.3 Arming Live Mode (Production Guardrails)
- Default mode is always `DEMO`.
- Arming `LIVE` requires:
  1. Valid, verified Bitget UTA V3 trading-only API credentials (read-only keys without withdrawal permission).
  2. Completing all pre-flight readiness checklist items on the Home page.
  3. Typing the exact confirmation phrase `LIVE <account>` in the confirmation modal.

---

## 5. Support, Diagnostics, and Clean Shutdown

- **Export Support Bundle**: In the launcher or on the Diagnostics page, click **Export Support Bundle**. The system packages system logs, configuration, and incident history into a zip file with automated secret scrubbing (API keys and passwords redacted).
- **Stopping Safely**: Click **Stop Safely** in the desktop launcher. The supervisor signals the account engine, flushes SQLite WAL checkpoints, closes journals, and releases the directory lock.
