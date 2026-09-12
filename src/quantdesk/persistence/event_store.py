from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, fields
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from quantdesk.core.events import Envelope, canonical_bytes
from quantdesk.persistence.db import Database
from quantdesk.persistence.manifests import DurableWatermark, RawRef
from quantdesk.persistence.migrations import PROJECTION_TABLES, SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class EventRecord:
    envelope: Envelope
    origin: Literal["INPUT", "DERIVED"]
    parent_id: str | None = None


@dataclass(frozen=True, slots=True)
class EconomicIdentity:
    venue: str
    environment: str
    account: str
    instrument: str
    native_id: str
    component_type: str


@dataclass(frozen=True, slots=True)
class LedgerPosting:
    account: str
    asset: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class LedgerTransaction:
    transaction_id: str
    event_id: str
    identity: EconomicIdentity
    postings: tuple[LedgerPosting, ...]
    aliases: tuple[EconomicIdentity, ...] = ()
    approved_by: str | None = None
    reversal_of: str | None = None


@dataclass(frozen=True, slots=True)
class OutboxInstruction:
    instruction_id: str
    client_order_id: str
    payload: bytes
    risk_version: str
    fence_epoch: str
    committed_seq: int
    expires_at: int


@dataclass(frozen=True, slots=True)
class ProjectionUpdate:
    table: str
    key: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class PersistenceTransition:
    """Persistence-facing immutable effects adapted by the core's Transition.

    The engine chooses/validates reducer output; this boundary enforces durable
    references, sequence/CAS, accounting integrity and an atomic write set. No
    candidate in-memory state may be published until CommitReceipt is returned.
    """

    input_event_id: str
    base_state_version: int
    events: tuple[EventRecord, ...]
    ledger_transactions: tuple[LedgerTransaction, ...] = ()
    outbox_instructions: tuple[OutboxInstruction, ...] = ()
    projection_updates: tuple[ProjectionUpdate, ...] = ()


@dataclass(frozen=True, slots=True)
class CommitReceipt:
    first_seq: int
    last_seq: int
    state_version: int
    raw_watermark: DurableWatermark
    outbox_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    checkpoint_id: str
    engine_seq: int
    state_version: int
    snapshot: bytes
    sha256: str
    raw_watermark: DurableWatermark
    schema_version: int


def _decode_envelope(data: bytes) -> Envelope:
    values = json.loads(data)
    for field in fields(Envelope):
        if (
            field.name.endswith("_ns") or field.name in {"engine_seq", "schema_version"}
        ) and values[field.name] is not None:
            values[field.name] = int(values[field.name])
    values["payload"] = base64.b64decode(values["payload"]["$bytes"], validate=True)
    return Envelope(**values)


def _identity_values(identity: EconomicIdentity) -> tuple[str, ...]:
    return (
        identity.venue,
        identity.environment,
        identity.account,
        identity.instrument,
        identity.native_id,
        identity.component_type,
    )


def _economic_key(identity: EconomicIdentity) -> tuple[str, ...]:
    # Execution IDs are instrument-scoped; funding/cash movement IDs are account-wide.
    instrument = identity.instrument if identity.component_type == "execution" else ""
    return (
        identity.venue,
        identity.environment,
        identity.account,
        instrument,
        identity.native_id,
        identity.component_type,
    )


class EventStore:
    def __init__(
        self, database: Database, *, allowed_assets: frozenset[str] = frozenset({"USDT"})
    ) -> None:
        self.database = database
        self.allowed_assets = allowed_assets

    def state_watermark(self) -> tuple[int, int]:
        row = self.database.connection.execute(
            "SELECT state_version, engine_seq FROM projection_watermarks WHERE name='engine'"
        ).fetchone()
        return (0, 0) if row is None else (int(row[0]), int(row[1]))

    def raw_watermark(self) -> DurableWatermark | None:
        row = self.database.connection.execute(
            "SELECT value FROM store_metadata WHERE name='raw_watermark'"
        ).fetchone()
        return None if row is None else DurableWatermark.from_bytes(row[0])

    def _validate_raw_watermark(self, watermark: DurableWatermark) -> None:
        previous = self.raw_watermark()
        if previous is not None and (
            watermark.journal_id != previous.journal_id
            or (watermark.frame_ordinal, watermark.chunk_index, watermark.end_offset)
            < (previous.frame_ordinal, previous.chunk_index, previous.end_offset)
        ):
            raise ValueError("raw watermark regressed or changed journal")

    def _validate_ledger(
        self, transaction: LedgerTransaction, events: dict[str, EventRecord]
    ) -> None:
        if not transaction.transaction_id or transaction.event_id not in events:
            raise ValueError("ledger transaction requires a current event")
        if not transaction.postings:
            raise ValueError("ledger transaction requires balanced postings")
        identity = transaction.identity
        envelope = events[transaction.event_id].envelope
        if (identity.account, identity.environment, identity.venue) != (
            envelope.account_id,
            envelope.environment,
            envelope.venue,
        ):
            raise ValueError("ledger identity does not belong to the account writer")
        for alias in (identity, *transaction.aliases):
            if any(not value for value in _identity_values(alias)):
                raise ValueError("economic identity fields are required")
            if (
                alias.venue,
                alias.environment,
                alias.account,
                alias.instrument,
                alias.component_type,
            ) != (
                identity.venue,
                identity.environment,
                identity.account,
                identity.instrument,
                identity.component_type,
            ):
                raise ValueError("economic alias changes financial scope")
        totals: dict[str, Fraction] = {}
        valid_accounts = {
            "equity:external",
            "income:realized_pnl",
            "expense:trading_fees",
            "income:funding",
            "equity:adjustments",
        }
        adjustment = identity.component_type == "adjustment"
        for posting in transaction.postings:
            if not isinstance(posting.amount, Decimal):
                raise TypeError("ledger posting amount must be Decimal")
            if not posting.amount.is_finite():
                raise ValueError("ledger posting amount must be finite")
            if posting.asset not in self.allowed_assets:
                raise ValueError("unknown or unauthorized ledger asset")
            if posting.account not in valid_accounts and posting.account != f"cash:{posting.asset}":
                raise ValueError("unknown ledger account")
            adjustment |= posting.account == "equity:adjustments"
            # Integer ratios preserve exact balance even when caller Decimal
            # context is too small; never round away a nonzero imbalance.
            totals[posting.asset] = totals.get(posting.asset, Fraction()) + Fraction(posting.amount)
        if any(total != 0 for total in totals.values()):
            raise ValueError("ledger postings do not balance exactly per asset")
        if adjustment and not transaction.approved_by:
            raise ValueError("ledger adjustment requires explicit authorization")

    def commit(
        self, transition: PersistenceTransition, raw_watermark: DurableWatermark
    ) -> CommitReceipt:
        self.database.assert_owner()
        connection = self.database.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            state_version, sequence = self.state_watermark()
            if transition.base_state_version != state_version:
                raise ValueError("stale base state version")
            self._validate_raw_watermark(raw_watermark)
            if (
                not transition.events
                or transition.events[0].envelope.event_id != transition.input_event_id
                or transition.events[0].origin != "INPUT"
            ):
                raise ValueError("transition must start with its selected input event")
            events = {record.envelope.event_id: record for record in transition.events}
            account_row = connection.execute(
                "SELECT value FROM store_metadata WHERE name='account_scope'"
            ).fetchone()
            account_scope = account_row[0] if account_row is not None else None
            for offset, record in enumerate(transition.events, 1):
                envelope = record.envelope
                if envelope.account_id is not None:
                    scope = canonical_bytes(
                        (envelope.venue, envelope.environment, envelope.account_id)
                    )
                    if account_scope is not None and scope != account_scope:
                        raise ValueError("event belongs to a different account writer")
                    account_scope = scope
                if envelope.engine_seq != sequence + offset:
                    raise ValueError("event sequences must be contiguous")
                if record.origin not in {"INPUT", "DERIVED"} or (
                    record.origin == "DERIVED" and not record.parent_id
                ):
                    raise ValueError("invalid event origin/parent")
                if offset > 1 and record.origin != "DERIVED":
                    raise ValueError("a transition has exactly one selected input")
                if envelope.raw_ref is not None and not raw_watermark.covers(
                    RawRef.parse(envelope.raw_ref)
                ):
                    raise ValueError("canonical raw reference exceeds durable watermark")
            for transaction in transition.ledger_transactions:
                self._validate_ledger(transaction, events)
            new_version = state_version + 1
            for record in transition.events:
                envelope = record.envelope
                connection.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        envelope.engine_seq,
                        envelope.event_id,
                        envelope.event_type,
                        canonical_bytes(envelope),
                        envelope.payload,
                        record.origin,
                        record.parent_id,
                        envelope.raw_ref,
                    ),
                )
            for transaction in transition.ledger_transactions:
                connection.execute(
                    "INSERT INTO ledger_transactions VALUES (?, ?, ?, ?)",
                    (
                        transaction.transaction_id,
                        transaction.event_id,
                        transaction.approved_by,
                        transaction.reversal_of,
                    ),
                )
                for identity in (transaction.identity, *transaction.aliases):
                    connection.execute(
                        "INSERT INTO economic_identities VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (*_economic_key(identity), transaction.transaction_id),
                    )
                connection.executemany(
                    "INSERT INTO ledger_postings VALUES (?, ?, ?, ?, ?)",
                    [
                        (
                            transaction.transaction_id,
                            ordinal,
                            posting.account,
                            posting.asset,
                            str(posting.amount),
                        )
                        for ordinal, posting in enumerate(transaction.postings)
                    ],
                )
            for instruction in transition.outbox_instructions:
                if not all(
                    (
                        instruction.instruction_id,
                        instruction.client_order_id,
                        instruction.risk_version,
                        instruction.fence_epoch,
                    )
                ):
                    raise ValueError("outbox requires stable identity/risk/fence")
                if instruction.committed_seq not in {
                    record.envelope.engine_seq for record in transition.events
                }:
                    raise ValueError("outbox must reference a current committed event")
                connection.execute(
                    "INSERT INTO outbox VALUES (?, ?, ?, 'PENDING', ?, ?, ?, ?)",
                    (
                        instruction.instruction_id,
                        instruction.client_order_id,
                        instruction.payload,
                        instruction.risk_version,
                        instruction.fence_epoch,
                        instruction.committed_seq,
                        instruction.expires_at,
                    ),
                )
            for projection in transition.projection_updates:
                if projection.table not in PROJECTION_TABLES or not projection.key:
                    raise ValueError("unknown projection table/key")
                connection.execute(
                    f"INSERT INTO {projection.table} VALUES (?, ?, ?) ON CONFLICT(key) "
                    "DO UPDATE SET payload=excluded.payload, state_version=excluded.state_version",
                    (projection.key, projection.payload, new_version),
                )
            last_seq = transition.events[-1].envelope.engine_seq
            connection.execute(
                "INSERT INTO projection_watermarks VALUES ('engine', ?, ?) ON CONFLICT(name) "
                "DO UPDATE SET state_version=excluded.state_version, "
                "engine_seq=excluded.engine_seq",
                (new_version, last_seq),
            )
            connection.execute(
                "INSERT INTO store_metadata VALUES ('raw_watermark', ?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (raw_watermark.to_bytes(),),
            )
            if account_scope is not None:
                connection.execute(
                    "INSERT OR IGNORE INTO store_metadata VALUES ('account_scope', ?)",
                    (account_scope,),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return CommitReceipt(
            sequence + 1,
            last_seq,
            new_version,
            raw_watermark,
            tuple(row.instruction_id for row in transition.outbox_instructions),
        )

    def read_after(self, seq: int) -> tuple[EventRecord, ...]:
        rows = self.database.connection.execute(
            "SELECT envelope_json, origin, parent_id FROM events "
            "WHERE engine_seq>? ORDER BY engine_seq",
            (seq,),
        ).fetchall()
        return tuple(EventRecord(_decode_envelope(row[0]), row[1], row[2]) for row in rows)

    def postings(self, transaction_id: str) -> tuple[LedgerPosting, ...]:
        rows = self.database.connection.execute(
            "SELECT account, asset, amount FROM ledger_postings "
            "WHERE transaction_id=? ORDER BY ordinal",
            (transaction_id,),
        ).fetchall()
        return tuple(LedgerPosting(row[0], row[1], Decimal(row[2])) for row in rows)

    def projection(self, table: str, key: str) -> tuple[bytes, int] | None:
        if table not in PROJECTION_TABLES:
            raise ValueError("unknown projection table")
        row = self.database.connection.execute(
            f"SELECT payload, state_version FROM {table} WHERE key=?", (key,)
        ).fetchone()
        return None if row is None else (bytes(row[0]), int(row[1]))

    def save_checkpoint(
        self, checkpoint_id: str, snapshot: bytes, raw_watermark: DurableWatermark
    ) -> CheckpointRecord:
        self.database.assert_owner()
        self._validate_raw_watermark(raw_watermark)
        version, seq = self.state_watermark()
        digest = hashlib.sha256(snapshot).hexdigest()
        connection = self.database.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "INSERT INTO checkpoint_manifest VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    checkpoint_id,
                    seq,
                    version,
                    snapshot,
                    digest,
                    raw_watermark.to_bytes(),
                    SCHEMA_VERSION,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return CheckpointRecord(
            checkpoint_id, seq, version, snapshot, digest, raw_watermark, SCHEMA_VERSION
        )

    def latest_checkpoint(self) -> CheckpointRecord | None:
        row = self.database.connection.execute(
            "SELECT * FROM checkpoint_manifest ORDER BY engine_seq DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        if hashlib.sha256(row[3]).hexdigest() != row[4]:
            raise ValueError("checkpoint snapshot hash mismatch")
        return CheckpointRecord(
            row[0], row[1], row[2], row[3], row[4], DurableWatermark.from_bytes(row[5]), row[6]
        )
