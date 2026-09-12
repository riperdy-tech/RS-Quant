"""Account-writer dispatch capabilities and a fenced asynchronous I/O boundary.

Transport preparation may await for rate limits or connections. `handoff` MUST
be synchronous: it hands bytes to the transport before returning an awaitable
response. It must not defer the send behind another await. This is the final
boundary that future venue adapters and the simulator implement.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, cast

from quantdesk.core.events import CancelTransportResult, OrderInstruction, SubmitTransportResult
from quantdesk.core.reducers import _decode_value
from quantdesk.core.types import EventPayload
from quantdesk.execution.intents import (
    CancelAttemptObserved,
    CancelRequested,
    DispatchStarted,
    GatewayInstruction,
    UnsentAborted,
)
from quantdesk.execution.order_state import TERMINAL, OMSState, PendingAction
from quantdesk.persistence.db import Database, FileOwnership
from quantdesk.persistence.event_store import EventStore, OutboxInstruction
from quantdesk.persistence.outbox import Outbox


class AccountOwnership:
    """A stale heartbeat denies sends; it never grants a competing writer ownership."""

    def __init__(
        self,
        database: Database,
        *,
        now_ns: Callable[[], int],
        heartbeat_timeout_ns: int = 3_000_000_000,
    ) -> None:
        database.assert_owner()
        if heartbeat_timeout_ns <= 0:
            raise ValueError("heartbeat timeout must be positive")
        self.database, self.now_ns = database, now_ns
        self.timeout = heartbeat_timeout_ns
        self._lock = FileOwnership(database.path.with_suffix(".gateway.lock"))
        self._active = False
        try:
            connection = database.connection
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT value FROM store_metadata WHERE name='gateway_epoch'"
            ).fetchone()
            self.epoch = str(1 if row is None else int(row[0]) + 1)
            connection.execute(
                "INSERT INTO store_metadata VALUES ('gateway_epoch', ?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (self.epoch,),
            )
            connection.commit()
            self._active = True
            self._heartbeat_ns = now_ns()
        except BaseException:
            database.connection.rollback()
            self._lock.close()
            raise

    def heartbeat(self) -> None:
        self.database.assert_owner()
        if not self._active:
            raise PermissionError("account ownership released")
        self._heartbeat_ns = self.now_ns()

    def validate(self, epoch: str) -> None:
        self.database.assert_owner()
        age = self.now_ns() - self._heartbeat_ns
        if not self._active or epoch != self.epoch or not 0 <= age <= self.timeout:
            raise PermissionError("stale account fence or heartbeat")
        row = self.database.connection.execute(
            "SELECT value FROM store_metadata WHERE name='gateway_epoch'"
        ).fetchone()
        if row is None or str(row[0]) != epoch:
            raise PermissionError("durable ownership epoch changed")

    def close(self) -> None:
        self.database.assert_owner()
        if self._active:
            self._active = False
            self._lock.close()


@dataclass(frozen=True, slots=True)
class GatewayPolicy:
    risk_version: str
    latch_version: str
    entries_allowed: bool = False
    reconciled: bool = False
    recovery_reductions_allowed: bool = False


@dataclass(frozen=True, slots=True)
class DispatchPermit:
    instruction_id: str
    instruction_hash: str
    expires_at_ns: int
    risk_version: str
    latch_version: str
    fence_epoch: str
    attempt_id: str


class VenueBoundary(Protocol):
    async def prepare(self, instruction: GatewayInstruction) -> None: ...

    def handoff(
        self, instruction: GatewayInstruction
    ) -> Awaitable[SubmitTransportResult | CancelTransportResult]: ...


class KnownUnsentError(Exception):
    """Raised only by a synchronous boundary that proves no request bytes were handed off."""


class UnsentDeadlineExceeded(KnownUnsentError, TimeoutError):
    """A local pre-byte deadline, not an ambiguous transport response timeout."""


class DispatchAuthority:
    """Only the account writer can mint, consume, or revoke a dispatch permit.

    The synchronous commit callback must record the supplied fact through its
    Engine. A grant additionally verifies the durable outbox claim after commit.
    Permits are process-local capabilities; they never survive ownership restart.
    """

    def __init__(
        self,
        store: EventStore,
        ownership: AccountOwnership,
        current_policy: Callable[[], GatewayPolicy],
        now_ns: Callable[[], int],
        commit_fact: Callable[[EventPayload, GatewayInstruction], None],
        validate_filters: Callable[[GatewayInstruction], bool],
    ) -> None:
        if store.database is not ownership.database:
            raise ValueError("permit authority and ownership must share the account writer")
        self.store, self.ownership, self.current_policy = store, ownership, current_policy
        self.now_ns, self.commit_fact, self.validate_filters = now_ns, commit_fact, validate_filters
        self._issued: dict[str, DispatchPermit] = {}
        self._unsent: dict[str, DispatchPermit] = {}
        self._ordinal = 0

    def decode(self, row: OutboxInstruction) -> GatewayInstruction:
        try:
            payload = json.loads(row.payload)
            cls = OrderInstruction if "quantity_lots" in payload else CancelRequested
            instruction = cast(GatewayInstruction, _decode_value(payload, cls))
        except (ValueError, TypeError, KeyError) as exc:
            raise PermissionError("invalid committed instruction") from exc
        if (
            instruction.instruction_id,
            instruction.client_order_id,
            instruction.risk_version,
            instruction.gateway_fence,
            instruction.expires_at_ns,
        ) != (
            row.instruction_id,
            row.client_order_id,
            row.risk_version,
            row.fence_epoch,
            row.expires_at,
        ):
            raise PermissionError("committed instruction metadata mismatch")
        return instruction

    def _check(self, row: OutboxInstruction, instruction: GatewayInstruction) -> GatewayPolicy:
        self.store.database.assert_owner()
        if self.now_ns() >= row.expires_at:
            raise PermissionError("EXPIRED")
        self.ownership.validate(row.fence_epoch)
        policy = self.current_policy()
        if row.risk_version != policy.risk_version or not policy.latch_version:
            raise PermissionError("risk version changed")
        account_scope = self.store.database.connection.execute(
            "SELECT value FROM store_metadata WHERE name='account_scope'"
        ).fetchone()
        if account_scope is None or json.loads(account_scope[0])[1] not in {
            "DEMO",
            "REPLAY",
            "BACKTEST",
            "PAPER",
            "SANDBOX",
        }:
            raise PermissionError("mainnet dispatch is disarmed")
        if isinstance(instruction, OrderInstruction):
            scope = self.store.database.connection.execute(
                "SELECT value FROM store_metadata WHERE name='account_scope'"
            ).fetchone()
            expected = [
                instruction.instrument_id.split(":")[0],
                instruction.environment,
                instruction.account_id,
            ]
            if scope is None or json.loads(scope[0]) != expected:
                raise PermissionError("gateway account scope mismatch")
            if instruction.environment not in {"DEMO", "REPLAY", "BACKTEST", "PAPER", "SANDBOX"}:
                raise PermissionError("mainnet dispatch is disarmed")
            recovery = instruction.reduce_only and policy.recovery_reductions_allowed
            if not policy.reconciled and not recovery:
                raise PermissionError("account is not reconciled")
            if not instruction.reduce_only and not policy.entries_allowed:
                raise PermissionError("risk latch prohibits entries")
        if not self.validate_filters(instruction):
            raise PermissionError("current authorization/instrument filters failed")
        # A committed dispatch claim is not a transport send. Read the current
        # account-writer projection again after preparation, so a cancel fact
        # can revoke a still-unsent submission even though its claim is UNKNOWN.
        projection = self.store.projection("orders", "oms-state-v1")
        if projection is None:
            raise PermissionError("authoritative OMS state unavailable")
        state = OMSState.from_bytes(projection[0])
        order = state.order(instruction.client_order_id)
        if not any(c.instruction == instruction for c in state.commands):
            raise PermissionError("instruction does not match authoritative OMS state")
        if isinstance(instruction, OrderInstruction) and order.pending == PendingAction.CANCEL:
            raise PermissionError("CANCEL_REQUESTED")
        pending = (
            PendingAction.SUBMIT
            if isinstance(instruction, OrderInstruction)
            else PendingAction.CANCEL
        )
        if order.lifecycle in TERMINAL or order.pending != pending:
            raise PermissionError("order is no longer dispatchable")
        return policy

    def _committed_status(self, row: OutboxInstruction) -> str:
        stored = self.store.database.connection.execute(
            "SELECT instruction_id, client_order_id, payload, risk_version, fence_epoch, "
            "committed_seq, expires_at, status "
            "FROM outbox WHERE instruction_id=?",
            (row.instruction_id,),
        ).fetchone()
        if stored is None or OutboxInstruction(*stored[:7]) != row:
            raise PermissionError("instruction is not committed or its body changed")
        return str(stored[7])

    def _durable(self, row: OutboxInstruction, expected: str) -> None:
        if self._committed_status(row) != expected:
            raise PermissionError("instruction is not committed or already consumed")

    def grant(self, row: OutboxInstruction) -> DispatchPermit:
        self.store.database.assert_owner()
        instruction = self.decode(row)
        self._durable(row, "PENDING")
        policy = self._check(row, instruction)
        self._ordinal += 1
        attempt = f"{self.ownership.epoch}:{self._ordinal}:{row.instruction_id}"
        self.commit_fact(DispatchStarted(row.instruction_id, attempt), instruction)
        self._durable(row, "UNKNOWN")
        permit = DispatchPermit(
            row.instruction_id,
            sha256(row.payload).hexdigest(),
            row.expires_at,
            policy.risk_version,
            policy.latch_version,
            row.fence_epoch,
            attempt,
        )
        self._issued[attempt] = permit
        self._unsent[attempt] = permit
        return permit

    def consume(self, permit: DispatchPermit, row: OutboxInstruction) -> GatewayInstruction:
        self.store.database.assert_owner()
        issued = self._issued.pop(permit.attempt_id, None)
        if issued is not permit:
            raise PermissionError("unissued or used dispatch permit")
        if (
            permit.instruction_id,
            permit.instruction_hash,
            permit.expires_at_ns,
            permit.risk_version,
            permit.fence_epoch,
        ) != (
            row.instruction_id,
            sha256(row.payload).hexdigest(),
            row.expires_at,
            row.risk_version,
            row.fence_epoch,
        ):
            raise PermissionError("dispatch permit instruction mismatch")
        instruction = self.decode(row)
        policy = self._check(row, instruction)
        if policy.latch_version != permit.latch_version:
            raise PermissionError("dispatch permit latch version changed")
        self._durable(row, "UNKNOWN")
        return instruction

    def handoff_started(self, permit: DispatchPermit) -> None:
        self.store.database.assert_owner()
        if self._unsent.pop(permit.attempt_id, None) is not permit:
            raise PermissionError("unowned dispatch handoff")

    def abort_unsent(
        self, row: OutboxInstruction, reason: str, permit: DispatchPermit | None = None
    ) -> None:
        if permit is not None:
            if self._unsent.pop(permit.attempt_id, None) is not permit or (
                permit.instruction_id != row.instruction_id
                or permit.instruction_hash != sha256(row.payload).hexdigest()
            ):
                raise PermissionError("cannot abort a sent or unowned dispatch attempt")
            self._issued.pop(permit.attempt_id, None)
            if self._committed_status(row) != "UNKNOWN":
                # A report observed while preparation awaited already resolved
                # the command. Its factual order/reservation projection wins.
                return
            self._durable(row, "UNKNOWN")
        else:
            self._durable(row, "PENDING")
        self.commit_fact(UnsentAborted(row.instruction_id, reason), self.decode(row))

    def observe(self, payload: EventPayload, instruction: GatewayInstruction) -> None:
        self.store.database.assert_owner()
        if isinstance(instruction, CancelRequested):
            if (
                not isinstance(payload, CancelTransportResult)
                or payload.client_order_id != instruction.client_order_id
            ):
                raise PermissionError("cancel transport response identity mismatch")
            payload = CancelAttemptObserved(instruction.instruction_id, payload)
        elif (
            not isinstance(payload, SubmitTransportResult)
            or payload.instruction_id != instruction.instruction_id
        ):
            raise PermissionError("submit transport response identity mismatch")
        self.commit_fact(payload, instruction)


class Router:
    def __init__(self, outbox: Outbox, authority: DispatchAuthority, venue: VenueBoundary) -> None:
        if outbox.database_path.resolve() != authority.store.database.path:
            raise ValueError("router outbox belongs to another account")
        self.outbox, self.authority, self.venue = outbox, authority, venue
        self._running = False

    async def dispatch_ready(self) -> tuple[str, ...]:
        if self._running:
            return ()
        self._running = True
        sent: list[str] = []
        try:
            for row in self.outbox.pending():
                try:
                    permit = self.authority.grant(row)
                except PermissionError as exc:
                    if str(exc) == "EXPIRED":
                        self.authority.abort_unsent(row, "EXPIRED")
                    continue
                instruction = self.authority.decode(row)
                try:
                    await self.venue.prepare(instruction)
                    instruction = self.authority.consume(permit, row)
                except asyncio.CancelledError:
                    self.authority.abort_unsent(row, "DISPATCH_INVALIDATED", permit)
                    discard = getattr(self.venue, "discard_prepared", None)
                    if discard is not None:
                        discard()
                    raise
                except (PermissionError, TimeoutError, ConnectionError) as exc:
                    # No transport handoff happened. Only this known-unsent path
                    # may release a claim; an ambiguous sent request never does.
                    self.authority.abort_unsent(
                        row,
                        "EXPIRED"
                        if str(exc) == "EXPIRED"
                        else "CANCELED"
                        if str(exc) == "CANCEL_REQUESTED"
                        else "DISPATCH_INVALIDATED",
                        permit,
                    )
                    discard = getattr(self.venue, "discard_prepared", None)
                    if discard is not None:
                        discard()
                    continue
                try:
                    # There is no await between the final validation and handoff.
                    response = self.venue.handoff(instruction)
                except KnownUnsentError:
                    self.authority.abort_unsent(row, "DISPATCH_INVALIDATED", permit)
                    discard = getattr(self.venue, "discard_prepared", None)
                    if discard is not None:
                        discard()
                    continue
                except BaseException as exc:
                    # A boundary error without an explicit no-byte guarantee
                    # may have sent partially, including cancellation or a
                    # process-style exception. Irrevocably revoke unsent proof
                    # before propagating; DispatchStarted already made the
                    # durable command UNKNOWN with its reserves retained.
                    self.authority.handoff_started(permit)
                    if not isinstance(exc, (TimeoutError, ConnectionError, OSError)):
                        raise
                    result = (
                        CancelTransportResult(instruction.client_order_id, False, "TIMEOUT")
                        if isinstance(instruction, CancelRequested)
                        else SubmitTransportResult(
                            instruction.instruction_id, False, None, "TIMEOUT"
                        )
                    )
                    self.authority.observe(result, instruction)
                    continue
                self.authority.handoff_started(permit)
                try:
                    sent.append(row.instruction_id)
                    result = await response
                except (TimeoutError, ConnectionError, OSError):
                    result = (
                        CancelTransportResult(instruction.client_order_id, False, "TIMEOUT")
                        if isinstance(instruction, CancelRequested)
                        else SubmitTransportResult(
                            instruction.instruction_id, False, None, "TIMEOUT"
                        )
                    )
                self.authority.observe(result, instruction)
            return tuple(sent)
        finally:
            self._running = False
