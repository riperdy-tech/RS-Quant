"""Additive versioned migrations; orchestrator must pause/checkpoint/backup first."""

import sqlite3

SCHEMA_VERSION = 2
PROJECTION_TABLES = frozenset(
    {
        "orders",
        "executions",
        "positions",
        "balances",
        "risk_latches",
        "reservations",
        "protection_groups",
        "config_versions",
        "model_versions",
        "reconciliation_runs",
    }
)

_V1 = (
    """CREATE TABLE events (
        engine_seq INTEGER PRIMARY KEY CHECK(engine_seq>0), event_id TEXT NOT NULL UNIQUE,
        type TEXT NOT NULL, envelope_json BLOB NOT NULL, payload_blob BLOB NOT NULL,
        origin TEXT NOT NULL CHECK(origin IN ('INPUT','DERIVED')),
        parent_id TEXT REFERENCES events(event_id), raw_ref TEXT)""",
    """CREATE TABLE ledger_transactions (
        transaction_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(event_id),
        approved_by TEXT, reversal_of TEXT REFERENCES ledger_transactions(transaction_id))""",
    """CREATE TABLE ledger_postings (
        transaction_id TEXT NOT NULL REFERENCES ledger_transactions(transaction_id),
        ordinal INTEGER NOT NULL, account TEXT NOT NULL, asset TEXT NOT NULL,
        amount TEXT NOT NULL, PRIMARY KEY(transaction_id, ordinal))""",
    """CREATE TABLE economic_identities (
        venue TEXT NOT NULL, environment TEXT NOT NULL, account TEXT NOT NULL,
        instrument TEXT NOT NULL, native_id TEXT NOT NULL, component_type TEXT NOT NULL,
        transaction_id TEXT NOT NULL REFERENCES ledger_transactions(transaction_id),
        PRIMARY KEY(venue, environment, account, instrument, native_id, component_type))""",
    """CREATE TABLE outbox (
        instruction_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL, payload BLOB NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('PENDING','SENT','RESOLVED','EXPIRED')),
        risk_version TEXT NOT NULL, fence_epoch TEXT NOT NULL,
        committed_seq INTEGER NOT NULL REFERENCES events(engine_seq),
        expires_at INTEGER NOT NULL)""",
    """CREATE TABLE command_results (
        command_id TEXT PRIMARY KEY, body_hash TEXT NOT NULL, state TEXT NOT NULL,
        reason TEXT, applied_seq INTEGER REFERENCES events(engine_seq))""",
    """CREATE TABLE projection_watermarks (
        name TEXT PRIMARY KEY, state_version INTEGER NOT NULL, engine_seq INTEGER NOT NULL)""",
    "CREATE TABLE store_metadata (name TEXT PRIMARY KEY, value BLOB NOT NULL)",
)
_V2 = (
    """CREATE TABLE checkpoint_manifest (
        checkpoint_id TEXT PRIMARY KEY, engine_seq INTEGER NOT NULL,
        state_version INTEGER NOT NULL, snapshot BLOB NOT NULL, sha256 TEXT NOT NULL,
        raw_watermark BLOB NOT NULL, schema_version INTEGER NOT NULL)""",
    "CREATE INDEX outbox_pending ON outbox(status, committed_seq, instruction_id)",
)


def migrate(connection: sqlite3.Connection, *, target_version: int = SCHEMA_VERSION) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if target_version > SCHEMA_VERSION or target_version < version:
        raise ValueError("unsupported schema version or downgrade")
    if connection.in_transaction:
        raise ValueError("migration requires a transaction-free connection")
    for next_version in range(version + 1, target_version + 1):
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in _V1 if next_version == 1 else _V2:
                connection.execute(statement)
            if next_version == 1:
                for table in sorted(PROJECTION_TABLES):
                    connection.execute(
                        f"CREATE TABLE {table} (key TEXT PRIMARY KEY, payload BLOB NOT NULL, "
                        "state_version INTEGER NOT NULL)"
                    )
                for table in (
                    "events",
                    "ledger_transactions",
                    "ledger_postings",
                    "economic_identities",
                ):
                    for operation in ("UPDATE", "DELETE"):
                        connection.execute(
                            f"CREATE TRIGGER {table}_no_{operation.lower()} BEFORE {operation} "
                            f"ON {table} BEGIN SELECT RAISE(ABORT, "
                            "'append-only audit history'); END"
                        )
            if next_version == 2:
                for operation in ("UPDATE", "DELETE"):
                    connection.execute(
                        f"CREATE TRIGGER checkpoint_no_{operation.lower()} BEFORE {operation} "
                        "ON checkpoint_manifest BEGIN SELECT "
                        "RAISE(ABORT, 'append-only checkpoint history'); END"
                    )
            connection.execute(f"PRAGMA user_version={next_version}")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
