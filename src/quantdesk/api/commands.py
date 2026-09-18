from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CommandRecord:
    command_id: str
    body_hash: str
    status: str
    body: dict[str, Any] = field(default_factory=dict)
    created_at_ns: int = field(default_factory=lambda: int(time.time_ns()))
    updated_at_ns: int = field(default_factory=lambda: int(time.time_ns()))
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DurableInbox:
    """Durable SQLite-backed command inbox enforcing idempotency and CAS versions (§15.3)."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else None
        self.records: dict[str, CommandRecord] = {}
        self.resource_versions: dict[str, str] = {}
        self.active_previews: dict[str, dict[str, Any]] = {}

        self.emergency_halted: bool = False
        self.strategy_states: dict[str, str] = {
            "unified-btc": "RUNNING",
            "unified-eth": "RUNNING",
            "curated-btc": "RUNNING",
            "curated-eth": "RUNNING",
            "imbalance-btc": "PAUSED",
            "momentum-btc": "PAUSED",
            "imbalance-eth": "PAUSED",
            "momentum-eth": "PAUSED",
        }

        if self.db_path:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()
            self._load_from_db()

    def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
        if not self.db_path:
            return []
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def reset(self) -> None:
        self.records.clear()
        self.resource_versions.clear()
        self.active_previews.clear()
        self.emergency_halted = False
        self.strategy_states = {
            "imbalance-btc": "RUNNING",
            "momentum-btc": "RUNNING",
        }
        if self.db_path and self.db_path.exists():
            self._execute("DELETE FROM command_inbox")

    def _init_db(self) -> None:
        self._execute("""
            CREATE TABLE IF NOT EXISTS command_inbox (
                command_id TEXT PRIMARY KEY,
                body_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                body TEXT NOT NULL,
                created_at_ns INTEGER NOT NULL,
                updated_at_ns INTEGER NOT NULL,
                error TEXT
            )
        """)

    def _load_from_db(self) -> None:
        if not self.db_path or not self.db_path.exists():
            return
        rows = self._execute("""
            SELECT command_id, body_hash, status, body, created_at_ns, updated_at_ns, error
            FROM command_inbox
        """)
        for row in rows:
            cmd_id, b_hash, st, b_str, c_at, u_at, err = row
            self.records[cmd_id] = CommandRecord(
                command_id=cmd_id,
                body_hash=b_hash,
                status=st,
                body=json.loads(b_str),
                created_at_ns=c_at,
                updated_at_ns=u_at,
                error=err,
            )

    def set_resource_version(self, resource_key: str, version: str) -> None:
        self.resource_versions[resource_key] = str(version)

    def get_resource_version(self, resource_key: str) -> str:
        return self.resource_versions.get(resource_key, "1")

    def create_preview(
        self,
        command_type: str,
        target: dict[str, Any],
        payload: dict[str, Any],
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Creates a human-readable action preview and single-use confirmation token (§15.3)."""
        target_str = (
            target.get("strategy_id")
            or target.get("account_id")
            or target.get("symbol")
            or "global"
        )
        current_version = self.get_resource_version(target_str)
        preview_token = secrets.token_urlsafe(16)

        required_text = None
        cmd_upper = command_type.upper()
        if cmd_upper in ("ENABLE_LIVE", "ARM_LIVE"):
            required_text = f"LIVE {target.get('account_id', 'paper-demo')}"
        elif cmd_upper in ("FLATTEN", "EMERGENCY_FLATTEN", "FLATTEN_POSITION"):
            sym = target.get("symbol") or target.get("instrument_id") or "all"
            required_text = f"FLATTEN {sym}"

        preview = {
            "preview_id": preview_token,
            "type": command_type,
            "target": target,
            "actor": actor,
            "current_version": current_version,
            "required_confirmation_text": required_text,
            "expires_at_ns": time.time_ns() + 60_000_000_000,  # 60s validity
            "action_summary": f"Execute {command_type} on target {target}",
        }
        self.active_previews[preview_token] = preview
        return preview

    def submit(
        self, command_id: str, body: dict[str, Any], actor: str | None = None
    ) -> tuple[int, str]:
        """Submits a command with idempotency and optimistic concurrency checks."""
        # Ensure actor is stored in record from session, not untrusted body (§15.3)
        body = dict(body)
        if actor:
            body["actor_id"] = actor

        body_str = json.dumps(body, sort_keys=True)
        body_hash = hashlib.sha256(body_str.encode("utf-8")).hexdigest()

        # 1. Check idempotency on command_id
        if command_id in self.records:
            existing = self.records[command_id]
            if existing.body_hash != body_hash:
                # Same ID with different body -> 409 Conflict (§15.3)
                return 409, existing.status
            # Duplicate submission with identical body -> return 202 with existing status
            return 202, existing.status

        # 2. Check optimistic concurrency version (CAS)
        expected_ver = body.get("expected_state_version")
        target_dict = body.get("target") or {}
        target_key = (
            target_dict.get("strategy_id")
            or target_dict.get("account_id")
            or target_dict.get("symbol")
            or "global"
        )

        if expected_ver is not None:
            current_ver = self.get_resource_version(target_key)
            if str(expected_ver) != str(current_ver):
                return 409, f"VERSION_CONFLICT: expected {expected_ver}, current is {current_ver}"

        # 3. Check confirmation token if provided or required
        token = body.get("confirmation_token")
        cmd_type = str(body.get("type", "")).upper()
        confirmation_text = body.get("confirmation_text")

        if token:
            if token not in self.active_previews:
                return 403, "Invalid or already consumed confirmation token"
            prev = self.active_previews.pop(token)

            # Check expiration
            if time.time_ns() > prev.get("expires_at_ns", 0):
                return 403, "Confirmation token has expired"

            # Check actor binding
            if prev.get("actor") and actor and prev["actor"] != actor:
                return 403, f"Confirmation token bound to actor '{prev['actor']}', not '{actor}'"

            # Check target binding (wrong-account token check §15.3)
            prev_target = prev.get("target") or {}
            if prev_target != target_dict:
                return 403, f"Confirmation token bound to target {prev_target}, not {target_dict}"

            # Check command type binding
            if prev.get("type", "").upper() != cmd_type:
                bound_type = prev.get("type")
                return 403, f"Confirmation token bound to type '{bound_type}', not '{cmd_type}'"

            # Check required confirmation text
            req_text = prev.get("required_confirmation_text")
            if req_text and confirmation_text != req_text:
                return 403, f"Confirmation text mismatch: must type '{req_text}'"

        elif cmd_type in (
            "ENABLE_LIVE",
            "ARM_LIVE",
            "FLATTEN",
            "EMERGENCY_FLATTEN",
            "FLATTEN_POSITION",
        ):
            target_acc = target_dict.get("account_id", "paper-demo")
            sym = target_dict.get("symbol") or target_dict.get("instrument_id") or "all"
            expected_text = f"LIVE {target_acc}" if "LIVE" in cmd_type else f"FLATTEN {sym}"
            if confirmation_text != expected_text:
                return 403, f"Confirmation text mismatch: must type '{expected_text}'"

        # 4. Handle state transitions for risk latch and strategies
        if cmd_type == "EMERGENCY_KILL":
            self.emergency_halted = True
            for k in list(self.strategy_states.keys()):
                self.strategy_states[k] = "PAUSED"
            try:
                from quantdesk.strategies.live_runner import autonomous_live_engine
                autonomous_live_engine.emergency_stop_all()
            except Exception:
                pass
        elif cmd_type in ("STOP_DEMO", "PAUSE_ALL"):
            for k in list(self.strategy_states.keys()):
                self.strategy_states[k] = "PAUSED"
            try:
                from quantdesk.strategies.live_runner import autonomous_live_engine
                autonomous_live_engine.pause_trading()
            except Exception:
                pass
        elif cmd_type in ("START_DEMO", "RESUME_ALL"):
            self.emergency_halted = False
            for k in list(self.strategy_states.keys()):
                self.strategy_states[k] = "RUNNING"
            try:
                from quantdesk.strategies.live_runner import autonomous_live_engine
                autonomous_live_engine.resume_trading()
            except Exception:
                pass
        elif cmd_type == "RESET_RISK_LATCH":
            self.emergency_halted = False
            # Note: reset does NOT resume paused strategies (§15)
        elif cmd_type in ("ENABLE_LIVE", "ARM_LIVE"):
            cap_val = body.get("payload", {}).get("capital_usdt") or target_dict.get("capital_usdt")
            lev_val = body.get("payload", {}).get("leverage") or target_dict.get("leverage")
            if cap_val:
                try:
                    from quantdesk.strategies.live_runner import autonomous_live_engine

                    autonomous_live_engine.update_capital_config(
                        capital_usdt=cap_val,
                        leverage=lev_val or 3.0,
                    )
                except Exception:
                    pass
        elif cmd_type == "PAUSE_STRATEGY":
            strat = target_dict.get("strategy_id", "imbalance-btc")
            self.strategy_states[strat] = "PAUSED"
        elif cmd_type == "RESUME_STRATEGY":
            if not self.emergency_halted:
                strat = target_dict.get("strategy_id", "imbalance-btc")
                self.strategy_states[strat] = "RUNNING"

        # 5. Durable commit
        now = time.time_ns()
        record = CommandRecord(
            command_id=command_id,
            body_hash=body_hash,
            status="QUEUED",
            body=body,
            created_at_ns=now,
            updated_at_ns=now,
        )
        self.records[command_id] = record

        if self.db_path:
            self._execute(
                """
                INSERT INTO command_inbox (
                    command_id, body_hash, status, body, created_at_ns, updated_at_ns
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (command_id, body_hash, "QUEUED", body_str, now, now),
            )

        return 202, "QUEUED"

    def update_status(self, command_id: str, status: str, error: str | None = None) -> None:
        """Updates durable status of a command record."""
        if command_id in self.records:
            now = time.time_ns()
            rec = self.records[command_id]
            rec.status = status
            rec.updated_at_ns = now
            rec.error = error

            if self.db_path:
                self._execute(
                    """
                    UPDATE command_inbox
                    SET status = ?, updated_at_ns = ?, error = ?
                    WHERE command_id = ?
                    """,
                    (status, now, error, command_id),
                )

    def get_status(self, command_id: str) -> str:
        if command_id in self.records:
            return self.records[command_id].status
        return "UNKNOWN"

    def get_record(self, command_id: str) -> CommandRecord | None:
        return self.records.get(command_id)


# Global singleton inbox
durable_inbox = DurableInbox()


def escape_csv_formula(value: str) -> str:
    """Escapes spreadsheet formula injection prefixes (=, +, -, @, tab, cr) per §15.4."""
    if not value:
        return value
    if value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value
