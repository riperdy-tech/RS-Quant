# QuantDesk Disaster Recovery & Backup Guide

Authoritative plan: [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)

---

## 1. Crash Recovery and Journal Roll-Forward

### 1.1 Sudden Process Crash or Power Cut
If the system crashes abruptly (process termination, OS crash, or power loss):
1. **Raw Journal Truncation**: Upon restart, the `RawJournal` reader reads framed Zstandard frames and computes CRC32 checksums. If the process crashed mid-write, the torn tail frame fails checksum verification and is safely truncated to the last known complete frame.
2. **Deterministic Checkpoint Recovery**: The engine restores the most recent atomic snapshot checkpoint from SQLite `EventStore`.
3. **Journal Roll-Forward**: Events committed after the checkpoint sequence are replayed sequentially through the deterministic reducers, reconstructing exact order state, ledger positions, and balance entries.
4. **Fails Disarmed in RECOVERY Mode**: The system boots into `mode: RECOVERY` with `entries_paused: True` and `live_enabled: False`. No strategies will submit orders until manual operator intervention.
5. **Reconciliation Scan**: The OMS initiates startup reconciliation against the exchange gateway. It cross-checks open client order IDs, updates fill lots, and flags foreign or unrecorded executions to the incident audit log.

---

## 2. Directory Locks & Ownership Epochs

### 2.1 Single-Writer Account Protection
- QuantDesk enforces single-writer execution via an OS-level exclusive file lock (`.account.lock`) inside the account data directory using `msvcrt.locking` on Windows and `fcntl.flock` on POSIX.
- **Ownership Epoch**: Every newly granted lease increments the durable `epoch.json` counter. All outbound gateway dispatches and state transitions carry the current epoch.
- If a secondary process tries to write to the same account directory, it is immediately blocked with `AccountLockedError`.

### 2.2 Stale Lock Recovery
If an engine crashes while holding a lock, the operating system kernel automatically releases the OS file lock handle when the process dies.
If a supervisor detects that the lock file exists but the owning PID is dead:
1. It verifies that the owning PID has completely exited.
2. It acquires the OS lock.
3. It increments the ownership epoch, permanently fencing any delayed packets from the dead process.

---

## 3. Consistent Backup & Restore Procedures

### 3.1 Creating Backups
Backups are created via the **Settings** page or programmatically via `quantdesk.persistence.backup.create_backup`:
- Uses the SQLite Online Backup API (`sqlite3_backup`) to guarantee a non-blocking, transactionally consistent snapshot of `engine.sqlite`.
- Includes sealed raw journal segments and manifest checksums.
- Encrypts the database bundle with `AESGCMCipher` (AES-256-GCM) using your secret key stored in the OS Credential Manager.

### 3.2 Restoring from a Backup
To restore a damaged or migrated environment:
1. Choose an isolated, non-existent target directory (restoring over an existing live directory is strictly forbidden to prevent accidental overwrites).
2. Call `restore_backup(bundle_path, target_dir, cipher=cipher)` or use the launcher Restore Wizard.
3. The restore process:
   - Validates backup manifest signatures and file hashes.
   - Decrypts `engine.sqlite` into the target directory.
   - Performs a full `PRAGMA integrity_check` and foreign key constraint validation.
   - Writes `restore.json` mandating disarmed startup:
     ```json
     {
       "mode": "RECOVERY",
       "live_enabled": false,
       "entries_paused": true,
       "requires_reconciliation": true
     }
     ```
4. **Guaranteed Live Disarming**: A restored backup will **never** boot directly into `LIVE` trading. The operator must review restored positions before resuming trading.

---

## 4. Storage Retention & Pruning

Storage retention is managed via `RetentionPolicy` (§16.3):
- **Logs**: Default 14 days retention.
- **Unpinned Market Data**: Default 30 days retention.
- **Pinned Data**: Critical audit records, incident logs, and tagged checkpoints are pinned with `pinned_identifiers` and are **immune** to automated deletion.
- **Deletion Preview**: The system generates an explicit storage reclamation preview (`preview_retention_cleanup`) before deleting eligible files.
