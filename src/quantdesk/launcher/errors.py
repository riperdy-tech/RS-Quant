"""Visible startup error diagnostics and recovery guidance without PowerShell (§16.1)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DiagnosticIssue:
    """Represents a detectable startup failure with non-technical recovery actions."""

    code: str
    title: str
    description: str
    recommended_action: str
    recoverable: bool = True


def diagnose_environment(
    account_dir: Path | str,
    web_dist_dir: Path | str | None = None,
) -> list[DiagnosticIssue]:
    """Runs pre-flight diagnostics on the local environment, returning actionable issues (§16.1)."""
    issues: list[DiagnosticIssue] = []
    acc_path = Path(account_dir).resolve()

    # 1. Check Native ML / Math DLLs
    try:
        import lightgbm  # noqa: F401
    except ImportError as exc:
        issues.append(
            DiagnosticIssue(
                code="DLL_LIGHTGBM_FAILURE",
                title="Model Library Runtime Missing",
                description=f"LightGBM native library failed to load: {exc}",
                recommended_action=(
                    "Install the Microsoft Visual C++ 2015-2022 Redistributable (x64) "
                    "or reinstall QuantDesk with bundled runtime binaries."
                ),
                recoverable=False,
            )
        )

    # 2. Check Directory Permissions & Lock
    if not acc_path.exists():
        try:
            acc_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            issues.append(
                DiagnosticIssue(
                    code="DIR_PERMISSION_DENIED",
                    title="Account Directory Inaccessible",
                    description=f"Cannot create or access account directory {acc_path}: {exc}",
                    recommended_action=(
                        "Select a writable application directory or check folder permissions."
                    ),
                    recoverable=False,
                )
            )

    lock_file = acc_path / ".account.lock"
    if lock_file.exists():
        epoch_file = acc_path / "epoch.json"
        owner_pid = "unknown"
        if epoch_file.exists():
            try:
                import json
                epoch_data = json.loads(epoch_file.read_text(encoding="utf-8"))
                owner_pid = epoch_data.get("owner_pid", "unknown")
            except Exception:
                pass
        issues.append(
            DiagnosticIssue(
                code="LOCKED_DIRECTORY",
                title="Account Directory In Use",
                description=f"Another instance appears to own this directory (PID: {owner_pid}).",
                recommended_action=(
                    "If QuantDesk is already running, click 'Open dashboard'. "
                    "If the previous process terminated unexpectedly, click 'Clear Stale Lock'."
                ),
                recoverable=True,
            )
        )

    # 3. Check Database Integrity if database exists
    db_path = acc_path / "control.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check(1)")
            res = cursor.fetchone()
            conn.close()
            if not res or res[0] != "ok":
                issues.append(
                    DiagnosticIssue(
                        code="DATABASE_CORRUPT",
                        title="Database Integrity Verification Failed",
                        description=f"SQLite integrity check failed on {db_path.name}: {res}",
                        recommended_action=(
                            "Restore from a recent verified backup using the 'Restore Backup' "
                            "button or isolate the damaged directory."
                        ),
                        recoverable=True,
                    )
                )
        except Exception as exc:
            issues.append(
                DiagnosticIssue(
                    code="DATABASE_UNREADABLE",
                    title="Database File Unreadable",
                    description=f"Could not open {db_path.name}: {exc}",
                    recommended_action=(
                        "Check if the file is locked by an antivirus scan or external tool."
                    ),
                    recoverable=False,
                )
            )

    # 4. Check Frontend Web Assets
    if web_dist_dir:
        dist_path = Path(web_dist_dir).resolve()
        index_html = dist_path / "index.html"
        if not index_html.exists():
            issues.append(
                DiagnosticIssue(
                    code="MISSING_WEB_ASSETS",
                    title="Dashboard User Interface Assets Missing",
                    description=f"Static web bundle was not found at {dist_path}.",
                    recommended_action=(
                        "Run the release packager or 'npm run build' inside the web directory."
                    ),
                    recoverable=False,
                )
            )

    return issues
