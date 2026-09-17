# Task 17 Walkthrough: Graphical Launcher, Packaging, Backup, and Deployment

We have implemented and verified **Task 17: Graphical launcher, packaging, backup, and deployment** in strict compliance with §5, §16.1, §16.2, §16.3, and Task 17 of `docs/IMPLEMENTATION_PLAN.md`.

---

## 1. Key Accomplishments

### Primary Acceptance Test Verification (§17)
- Replaced the stub in `tests/support/supervisor_case.py` with a **real local implementation** (`launcher_second_instance_and_restore_case`):
  ```python
  def test_second_launch_and_restore_are_safe(case):
      r = case("launcher_second_instance_and_restore")
      assert r["account_writer_process_count"] == 1
      assert r["second_launch_action"] == "OPEN_EXISTING_DASHBOARD"
      assert r["restored_economic_state_hash"] == r["backup_economic_state_hash"]
      assert r["restored_live_armed"] is False
  ```
- **Verified Behavior**:
  1. Primary launcher instance starts, acquires exclusive OS directory lock (`AccountLock`), and records active PID/port in `active_instance.json`.
  2. Secondary launch attempt detects the running owner, avoids spawning a second writer (`account_writer_process_count == 1`), and triggers `OPEN_EXISTING_DASHBOARD`.
  3. Real trading orders processed through `AccountCase` with double-entry ledger postings.
  4. Consistent snapshot backup created with `AESGCMCipher` and restored into an isolated directory.
  5. State restored and verified: `restored_economic_state_hash == backup_economic_state_hash` and `restored_live_armed is False`.

### Process Supervisor Subsystem (`src/quantdesk/supervisor/`)
1. **OS Exclusive Directory Lock (`lock.py`)**:
   - Uses `msvcrt.locking` on Windows and `fcntl.flock` on POSIX to guarantee single-writer ownership of the account directory.
   - Durable `epoch.json` maintains lease counter incremented on each lease grant.
2. **Heartbeat & Watchdog (`heartbeat.py`)**:
   - Heartbeat monitor expects heartbeats every 1.0s, flagging unhealthy after 3.0s.
   - `SupervisedRecoveryWatchdog` coordinates exponential backoff recovery and emergency cancellation of open entry orders on crashed writers while preserving protective stops.
3. **Supervisor Manager (`manager.py`)**:
   - Launches child processes hidden without flashing console windows on Windows (`CREATE_NO_WINDOW`).
   - Redirects stdout/stderr to rotating log files in `logs/`.
   - Resolves port collisions: dynamically detects port conflicts and allocates an available loopback port (`find_free_loopback_port`).
   - Clean shutdown: signals processes, flushes SQLite WAL, and releases locks.

### Desktop Launcher Subsystem (`src/quantdesk/launcher/` & `launcher/`)
1. **Native GUI Launcher (`app.py`)**:
   - Built with Python standard library `tkinter`.
   - Detects running instances and opens the existing dashboard in browser.
   - Displays system status, active mode (`DEMO`), and one-time bootstrap enrollment code.
   - Action buttons: **Open dashboard**, **Run diagnostics**, **Export support bundle**, **Stop safely**.
   - Supports headless execution for automated testing (`LauncherApp(headless=True)`).
2. **Startup Error Recovery Without PowerShell (`errors.py`)**:
   - Pre-flight diagnostic runner detecting missing DLLs, permissions, locked directories, and corrupted SQLite databases with actionable GUI advice.
3. **Executable Entrypoint (`launcher/main.py`)**:
   - Main entrypoint accepting CLI options (`--account-dir`, `--mode`, `--headless`, `--no-browser`, `--port`).
4. **PyInstaller Specification (`launcher/QuantDesk.spec`)**:
   - Packages `src/quantdesk`, production web distribution `web/dist`, demo configs, migrations, fixtures, and hidden imports.
5. **Inno Setup Script (`launcher/installer.iss`)**:
   - Per-user installation to `{localappdata}\Programs\QuantDesk` without requiring Windows UAC administrator elevation.

### Backup, Retention, and Schema Validation (`src/quantdesk/persistence/backup.py`)
1. **Retention Policy**:
   - Default 14 days for logs, 30 days for unpinned market data.
   - Pinned audit records and checkpoints are immune to cleanup.
2. **Storage Reclamation Preview**:
   - `preview_retention_cleanup` calculates files and bytes to be reclaimed before deletion.
   - `apply_retention_cleanup` securely unlinks only unpinned candidates.
3. **Schema Integrity**:
   - Checks schema version compatibility on restore; forces disarmed `live_enabled: False` in `restore.json`.

### Deployment & CI Resources (`deploy/`, `.github/workflows/`)
- `deploy/Dockerfile`: Multi-stage container with non-root user `quantdesk` and health check.
- `deploy/compose.yaml`: Docker Compose deployment with private bindings and volume mounts.
- `deploy/systemd/quantdesk.service`: Systemd unit file for Linux hosts.
- `.github/workflows/release-windows.yml`: GitHub Actions Windows CI build and clean-machine smoke test workflow.
- `scripts/build_release.py`: Packaging script with release manifest generation (SHA-256) and verified execution in paths containing spaces.

---

## 2. Verification Evidence

### Integration Suites
```powershell
.venv\Scripts\pytest.exe tests/integration/test_supervisor.py tests/integration/test_backup_restore.py -q
```
Output:
```
........                                                                 [100%]
8 passed in 0.85s
```
All 8 integration tests passed:
1. `test_second_launch_and_restore_are_safe` (Primary acceptance test: single-writer lock + exact economic hash restore)
2. `test_account_lock_exclusive_and_epoch_increment`
3. `test_heartbeat_monitor_and_watchdog_threshold`
4. `test_port_collision_resolution`
5. `test_diagnostics_environment_checks`
6. `test_consistent_sqlite_backup_and_restore`
7. `test_backup_retention_pinning_and_deletion_preview`
8. `test_restore_safeguards_and_isolation`

### Path With Spaces Verification
```powershell
.venv\Scripts\python.exe scripts/build_release.py --target windows --test-spaces --skip-bundle
```
Output:
```
Testing application execution in a path containing spaces...
Path with spaces verification: PASS
```

### Static Analysis & Type Checking
```powershell
.venv\Scripts\ruff.exe check src/quantdesk
.venv\Scripts\mypy.exe src/quantdesk
```
Output:
```
All checks passed!
Success: no issues found in 124 source files
```
