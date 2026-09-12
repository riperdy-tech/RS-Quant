"""Pure OMS facts; one composition reducer commits OMS and accounting together."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import localcontext
from hashlib import sha256

from quantdesk.core.checkpoint import EngineState
from quantdesk.core.events import (
    CancelTransportResult,
    Envelope,
    ExecutionReport,
    OrderInstruction,
    OrderReport,
    SubmitTransportResult,
    canonical_bytes,
)
from quantdesk.core.reducers import FactReducer, Reduction, Stage, decode_payload
from quantdesk.core.types import ExecutionType, Side
from quantdesk.execution.intents import (
    CancelAttemptObserved,
    CancelRequested,
    DispatchStarted,
    OrderApproved,
    UnsentAborted,
)
from quantdesk.execution.order_state import (
    TERMINAL,
    CommandState,
    ExecutionIdentity,
    Knowledge,
    Lifecycle,
    OMSState,
    OrderState,
    PendingAction,
)
from quantdesk.persistence.event_store import (
    OutboxInstruction,
    OutboxStatusUpdate,
    ProjectionUpdate,
)
from quantdesk.portfolio.arithmetic import ACCOUNTING_CONTEXT
from quantdesk.portfolio.events import (
    FinancialEventObserved,
    ReservationBatchChanged,
    ReservationChanged,
)
from quantdesk.portfolio.ledger import Ledger, LedgerState
from quantdesk.portfolio.reducer import accounting_reducer


@dataclass(frozen=True, slots=True)
class OMSChange:
    state: OMSState
    reservation_updates: tuple[ReservationChanged, ...] = ()
    outbox_instructions: tuple[OutboxInstruction, ...] = ()
    outbox_updates: tuple[OutboxStatusUpdate, ...] = ()
    emitted_facts: tuple[str, ...] = ()

    def projections(self) -> tuple[ProjectionUpdate, ...]:
        return (
            ProjectionUpdate("orders", "oms-state-v1", self.state.to_bytes()),
            *(
                ProjectionUpdate("reservations", r.reservation_id, canonical_bytes(r))
                for r in self.reservation_updates
            ),
        )


def _reservation(order: OrderState) -> ReservationChanged:
    instruction = order.instruction
    with localcontext(ACCOUNTING_CONTEXT):
        # Decimal numerator prevents any binary-float arithmetic in encumbrances.
        cash = order.cash_reserve * order.reserved_lots / instruction.quantity_lots
        fee = order.fee_reserve * order.reserved_lots / instruction.quantity_lots
    return ReservationChanged(
        instruction.instruction_id,
        instruction.instrument_id,
        instruction.side,
        order.reserved_lots,
        cash,
        fee,
        instruction.reduce_only,
        order.revision,
    )


def _find_order(
    state: OMSState, client: str | None, venue: str | None, instrument: str | None
) -> OrderState | None:
    by_client = next((o for o in state.orders if o.instruction.client_order_id == client), None)
    by_venue = next(
        (
            o
            for o in state.orders
            if venue is not None
            and o.venue_order_id == venue
            and o.instruction.instrument_id == instrument
        ),
        None,
    )
    if by_client is not None and by_venue is not None and by_client != by_venue:
        raise ValueError("order identity aliases conflict")
    order = by_client or by_venue
    if order and (
        order.instruction.instrument_id != instrument
        or (venue is not None and order.venue_order_id not in (None, venue))
    ):
        raise ValueError("order identity changes instrument or venue order")
    return order


def _put_order(state: OMSState, order: OrderState) -> OMSState:
    orders = {o.instruction.client_order_id: o for o in state.orders}
    orders[order.instruction.client_order_id] = order
    return replace(state, orders=tuple(orders[k] for k in sorted(orders)))


def _command_status(
    state: OMSState, instruction_id: str, status: str, attempt_id: str | None = None
) -> OMSState:
    if not any(c.instruction.instruction_id == instruction_id for c in state.commands):
        raise ValueError("unknown outbox instruction")
    return replace(
        state,
        commands=tuple(
            replace(c, status=status, attempt_id=attempt_id or c.attempt_id)
            if c.instruction.instruction_id == instruction_id
            else c
            for c in state.commands
        ),
    )


def _resolve_command(state: OMSState, client: str, *, cancel: bool = False) -> OMSState:
    return replace(
        state,
        commands=tuple(
            replace(c, status="RESOLVED")
            if c.instruction.client_order_id == client
            and c.status in {"PENDING", "UNKNOWN", "SENT"}
            and (cancel or isinstance(c.instruction, OrderInstruction))
            else c
            for c in state.commands
        ),
    )


class OMS:
    def approve(
        self, approval: OrderApproved, state: OMSState, portfolio: LedgerState, available_ns: int
    ) -> OMSChange:
        value = approval.instruction
        if (value.environment, value.account_id, value.instrument_id.split(":")[0]) != (
            state.environment,
            state.account,
            state.venue,
        ) or (portfolio.venue, portfolio.environment, portfolio.account) != (
            state.venue,
            state.environment,
            state.account,
        ):
            raise ValueError("instruction/portfolio changes account scope")
        if not all(
            (
                value.instruction_id,
                value.client_order_id,
                value.parent_intent_id,
                value.owner_strategy_id,
                value.risk_version,
                value.gateway_fence,
            )
        ):
            raise ValueError("instruction requires stable identity/risk/owner/fence")
        if type(value.quantity_lots) is not int or type(value.reduce_only) is not bool:
            raise ValueError("invalid lot or reduce-only type")
        if value.expires_at_ns <= available_ns:
            raise ValueError("expired intent cannot be approved")
        existing = next(
            (o for o in state.orders if o.instruction.client_order_id == value.client_order_id),
            None,
        )
        if existing:
            if (existing.instruction, existing.cash_reserve, existing.fee_reserve) != (
                value,
                approval.cash_reserve,
                approval.fee_reserve,
            ):
                raise ValueError("client order identity reused with changed instruction")
            return OMSChange(state)
        if any(c.instruction.instruction_id == value.instruction_id for c in state.commands) or any(
            o.instruction.parent_intent_id == value.parent_intent_id for o in state.orders
        ):
            raise ValueError("instruction or intent already consumed; no fresh-ID retry")
        if not value.reduce_only and any(
            o.instruction.instrument_id == value.instrument_id
            and o.knowledge != Knowledge.CONFIRMED
            for o in state.orders
        ):
            raise ValueError("unresolved order prohibits fresh entry identities")
        if not value.reduce_only:
            owner = dict(state.symbol_owners).get(value.instrument_id)
            if owner is not None and owner != value.owner_strategy_id:
                raise ValueError("instrument owner cannot change before flat reconciliation")
            position = portfolio.position(value.instrument_id).signed_lots
            if position and (position > 0) != (value.side == Side.BUY):
                raise ValueError("atomic reversal is prohibited; close and reconcile flat first")
            if any(
                o.instruction.instrument_id == value.instrument_id
                and o.instruction.reduce_only
                and o.reserved_lots
                for o in state.orders
            ):
                raise ValueError("active exit prohibits new entries")
        if value.reduce_only:
            position = portfolio.position(value.instrument_id).signed_lots
            reserved = sum(
                r.remaining_lots
                for r in portfolio.reservations
                if r.instrument_id == value.instrument_id and r.reduce_only
            )
            if (value.side == Side.SELL) != (position > 0) or value.quantity_lots > abs(
                position
            ) - reserved:
                raise ValueError("closing order exceeds available position")
        order = OrderState(value, approval.cash_reserve, approval.fee_reserve)
        candidate = _put_order(state, order)
        if not value.reduce_only:
            owners = dict(candidate.symbol_owners)
            owners[value.instrument_id] = value.owner_strategy_id
            candidate = replace(candidate, symbol_owners=tuple(sorted(owners.items())))
        candidate = replace(candidate, commands=(*state.commands, CommandState(value)))
        return self._change(state, candidate, None)

    def replace_order(
        self, client: str, replacement: OrderInstruction, state: OMSState
    ) -> OrderInstruction:
        order = state.order(client)
        if order.knowledge != Knowledge.CONFIRMED or (
            order.reserved_lots and order.lifecycle in TERMINAL
        ):
            raise ValueError("uncertain order or unresolved executions prohibit replacement")
        if order.lifecycle != Lifecycle.CANCELED or order.pending != PendingAction.NONE:
            raise ValueError("replacement requires cancel confirmation")
        if (
            replacement.client_order_id == client
            or replacement.instruction_id == order.instruction.instruction_id
            or replacement.parent_intent_id == order.instruction.parent_intent_id
        ):
            raise ValueError("replacement requires a new risk-approved intent and identity")
        if (
            replacement.account_id,
            replacement.environment,
            replacement.instrument_id,
            replacement.owner_strategy_id,
            replacement.side,
            replacement.reduce_only,
        ) != (
            order.instruction.account_id,
            order.instruction.environment,
            order.instruction.instrument_id,
            order.instruction.owner_strategy_id,
            order.instruction.side,
            order.instruction.reduce_only,
        ) or replacement.quantity_lots > order.canceled_remainder_lots:
            raise ValueError("replacement changes scope or exceeds canceled remainder")
        return replacement

    def apply(self, report: Envelope, state: OMSState) -> OMSChange:
        if (report.venue, report.environment, report.account_id) != (
            state.venue,
            state.environment,
            state.account,
        ):
            raise ValueError("OMS fact changes account scope")
        payload = decode_payload(report)
        aliases: tuple[str, ...] = ()
        cancel_instruction_id = None
        if isinstance(payload, CancelAttemptObserved):
            cancel_instruction_id = payload.instruction_id
            payload = payload.result
        if isinstance(payload, FinancialEventObserved):
            aliases = payload.aliases
            payload = payload.financial_payload
        candidate = state
        if isinstance(payload, ExecutionReport):
            if payload.execution_type != ExecutionType.TRADE or report.instrument_id is None:
                raise ValueError("OMS execution must be an instrument trade")
            candidate = self._execution(payload, aliases, report.instrument_id, state)
        elif isinstance(payload, OrderReport):
            order = _find_order(
                state, payload.client_order_id, payload.venue_order_id, report.instrument_id
            )
            if order is None:
                return OMSChange(
                    replace(
                        state, incidents=tuple(sorted(set((*state.incidents, "UNKNOWN_ORDER"))))
                    )
                )
            status = Lifecycle(payload.lifecycle)
            if status == Lifecycle.CREATED:
                raise ValueError("venue cannot report local CREATED state")
            cumulative = max(order.reported_fill_lots, payload.cumulative_fill_lots)
            if status == Lifecycle.FILLED:
                cumulative = max(cumulative, order.instruction.quantity_lots)
            if order.lifecycle in TERMINAL and status != Lifecycle.FILLED:
                status = order.lifecycle
            if order.accounted_fill_lots >= order.instruction.quantity_lots:
                status = Lifecycle.FILLED
            elif status == Lifecycle.OPEN and (
                order.accounted_fill_lots
                or cumulative
                or order.lifecycle == Lifecycle.PARTIALLY_FILLED
            ):
                status = Lifecycle.PARTIALLY_FILLED
            pending = (
                PendingAction.NONE
                if status in TERMINAL or order.pending == PendingAction.SUBMIT
                else order.pending
            )
            candidate = _put_order(
                state,
                replace(
                    order,
                    venue_order_id=payload.venue_order_id or order.venue_order_id,
                    lifecycle=status,
                    reported_fill_lots=cumulative,
                    pending=pending,
                    knowledge=Knowledge.CONFIRMED,
                    transport_started=True,
                ),
            )
            candidate = _resolve_command(
                candidate, payload.client_order_id, cancel=status in TERMINAL
            )
        elif isinstance(payload, SubmitTransportResult):
            command = next(
                c for c in state.commands if c.instruction.instruction_id == payload.instruction_id
            )
            order = state.order(command.instruction.client_order_id)
            _find_order(
                state,
                order.instruction.client_order_id,
                payload.venue_order_id,
                order.instruction.instrument_id,
            )
            # Facts already established via reports cannot regress on a delayed transport result.
            if command.status in {"RESOLVED", "REJECTED", "EXPIRED", "CANCELED", "BLOCKED"}:
                return OMSChange(state)
            rejected = not payload.accepted and payload.error_code == "VENUE_REJECTED"
            candidate = _command_status(
                state,
                payload.instruction_id,
                "SENT" if payload.accepted else "REJECTED" if rejected else "UNKNOWN",
            )
            candidate = _put_order(
                candidate,
                replace(
                    order,
                    venue_order_id=payload.venue_order_id or order.venue_order_id,
                    transport_started=True,
                    knowledge=Knowledge.CONFIRMED
                    if payload.accepted or rejected
                    else Knowledge.UNCERTAIN,
                    lifecycle=Lifecycle.REJECTED if rejected else order.lifecycle,
                    pending=PendingAction.NONE if rejected else order.pending,
                ),
            )
        elif isinstance(payload, CancelRequested):
            order = state.order(payload.client_order_id)
            existing = next(
                (
                    c
                    for c in state.commands
                    if c.instruction.instruction_id == payload.instruction_id
                ),
                None,
            )
            if existing:
                if existing.instruction != payload:
                    raise ValueError("cancel identity reused with changed body")
                return OMSChange(state)
            if order.lifecycle in TERMINAL or order.pending == PendingAction.CANCEL:
                return OMSChange(state)
            if not order.transport_started:
                candidate = _command_status(state, order.instruction.instruction_id, "CANCELED")
                candidate = _put_order(
                    candidate,
                    replace(order, lifecycle=Lifecycle.CANCELED, pending=PendingAction.NONE),
                )
            else:
                candidate = replace(state, commands=(*state.commands, CommandState(payload)))
                candidate = _put_order(candidate, replace(order, pending=PendingAction.CANCEL))
        elif isinstance(payload, CancelTransportResult):
            order = state.order(payload.client_order_id)
            if order.pending != PendingAction.CANCEL:
                return OMSChange(state)
            attempts = [
                c
                for c in state.commands
                if isinstance(c.instruction, CancelRequested)
                and c.instruction.client_order_id == payload.client_order_id
            ]
            if cancel_instruction_id is None and len(attempts) > 1:
                # The legacy uncorrelated transport fact cannot identify which
                # cancellation failed. Keep the current request pending.
                candidate = _put_order(state, replace(order, knowledge=Knowledge.UNCERTAIN))
                return self._change(state, candidate, report)
            if cancel_instruction_id is not None:
                matched = next(
                    (c for c in attempts if c.instruction.instruction_id == cancel_instruction_id),
                    None,
                )
                if matched is None:
                    raise ValueError("cancel response identity does not match order")
                if matched.status in {"REJECTED", "RESOLVED", "EXPIRED", "BLOCKED"}:
                    return OMSChange(state)
            cancel = next(
                c
                for c in reversed(state.commands)
                if isinstance(c.instruction, CancelRequested)
                and c.instruction.client_order_id == payload.client_order_id
            )
            rejected = not payload.accepted and payload.error_code == "VENUE_REJECTED"
            candidate = _command_status(
                state,
                cancel.instruction.instruction_id,
                "SENT" if payload.accepted else "REJECTED" if rejected else "UNKNOWN",
            )
            candidate = _put_order(
                candidate,
                replace(
                    order,
                    pending=PendingAction.NONE if rejected else order.pending,
                    knowledge=Knowledge.CONFIRMED
                    if payload.accepted or rejected
                    else Knowledge.UNCERTAIN,
                ),
            )
        elif isinstance(payload, (DispatchStarted, UnsentAborted)):
            command = next(
                c for c in state.commands if c.instruction.instruction_id == payload.instruction_id
            )
            order = state.order(command.instruction.client_order_id)
            is_cancel = isinstance(command.instruction, CancelRequested)
            if isinstance(payload, DispatchStarted):
                if command.status != "PENDING" or not payload.attempt_id:
                    raise ValueError("dispatch cannot retry an uncertain or consumed instruction")
                candidate = _command_status(
                    state, payload.instruction_id, "UNKNOWN", payload.attempt_id
                )
                candidate = _put_order(
                    candidate, replace(order, knowledge=Knowledge.UNCERTAIN, transport_started=True)
                )
            else:
                if command.status not in {"PENDING", "UNKNOWN"}:
                    raise ValueError("cannot expire or abort a sent instruction")
                candidate = _command_status(
                    state,
                    payload.instruction_id,
                    "EXPIRED" if payload.reason == "EXPIRED" else "BLOCKED",
                )
                candidate = _put_order(
                    candidate,
                    replace(
                        order,
                        knowledge=Knowledge.CONFIRMED,
                        lifecycle=order.lifecycle
                        if is_cancel
                        else Lifecycle.EXPIRED
                        if payload.reason == "EXPIRED"
                        else Lifecycle.REJECTED,
                        pending=PendingAction.NONE,
                    ),
                )
        else:
            return OMSChange(state)
        candidate = self._link_and_project(candidate)
        return self._change(state, candidate, report)

    def _execution(
        self, payload: ExecutionReport, aliases: tuple[str, ...], instrument: str, state: OMSState
    ) -> OMSState:
        order = _find_order(state, payload.client_order_id, payload.venue_order_id, instrument)
        native_ids = tuple(sorted(set((payload.native_execution_id, *aliases))))
        signature = sha256(
            canonical_bytes(
                (
                    payload.side,
                    payload.executed_lots,
                    payload.price.as_integer_ratio(),
                    payload.fee_amount.as_integer_ratio(),
                    payload.fee_currency,
                    payload.execution_type,
                )
            )
        ).hexdigest()
        existing = [
            e
            for e in state.executions
            if e.instrument_id == instrument and set(e.native_ids).intersection(native_ids)
        ]
        if len(existing) > 1:
            raise ValueError("execution aliases join already distinct economic effects")
        prior = existing[0] if existing else None
        client = order.instruction.client_order_id if order else payload.client_order_id
        if prior and (
            prior.signature != signature
            or (prior.client_order_id and client and prior.client_order_id != client)
            or (
                prior.venue_order_id
                and payload.venue_order_id
                and prior.venue_order_id != payload.venue_order_id
            )
        ):
            raise ValueError("execution identity has conflicting economic facts")
        execution = ExecutionIdentity(
            instrument,
            tuple(sorted(set((*native_ids, *(prior.native_ids if prior else ()))))),
            client or (prior.client_order_id if prior else None),
            payload.venue_order_id or (prior.venue_order_id if prior else None),
            payload.side,
            payload.executed_lots,
            signature,
        )
        candidate = replace(
            state,
            executions=tuple(
                sorted(
                    (*(e for e in state.executions if e is not prior), execution),
                    key=lambda e: (e.instrument_id, e.native_ids),
                )
            ),
        )
        if order:
            if order.instruction.side != payload.side:
                candidate = replace(
                    candidate,
                    incidents=tuple(sorted(set((*candidate.incidents, "EXECUTION_SIDE_MISMATCH")))),
                )
            candidate = _put_order(
                candidate,
                replace(
                    order,
                    venue_order_id=payload.venue_order_id or order.venue_order_id,
                    pending=PendingAction.NONE
                    if order.pending == PendingAction.SUBMIT
                    else order.pending,
                    knowledge=Knowledge.CONFIRMED,
                    transport_started=True,
                ),
            )
            candidate = _resolve_command(candidate, order.instruction.client_order_id)
        return candidate

    def _link_and_project(self, state: OMSState) -> OMSState:
        executions = []
        for execution in state.executions:
            order = _find_order(
                state, execution.client_order_id, execution.venue_order_id, execution.instrument_id
            )
            executions.append(
                replace(execution, client_order_id=order.instruction.client_order_id)
                if order
                else execution
            )
        candidate = replace(state, executions=tuple(executions))
        for order in candidate.orders:
            accounted = sum(
                e.lots
                for e in executions
                if e.client_order_id == order.instruction.client_order_id
                and e.instrument_id == order.instruction.instrument_id
            )
            status = order.lifecycle
            if accounted >= order.instruction.quantity_lots:
                status = Lifecycle.FILLED
                candidate = _resolve_command(
                    candidate, order.instruction.client_order_id, cancel=True
                )
            elif accounted and status not in TERMINAL:
                status = Lifecycle.PARTIALLY_FILLED
            knowledge = order.knowledge
            cancel_unknown = any(
                isinstance(c.instruction, CancelRequested)
                and c.instruction.client_order_id == order.instruction.client_order_id
                and c.status == "UNKNOWN"
                for c in candidate.commands
            )
            if cancel_unknown and status not in TERMINAL:
                knowledge = Knowledge.UNCERTAIN
            expected = max(
                order.reported_fill_lots,
                order.instruction.quantity_lots if status == Lifecycle.FILLED else 0,
            )
            if expected > accounted:
                knowledge = Knowledge.RECONCILING
            elif knowledge == Knowledge.RECONCILING:
                knowledge = Knowledge.CONFIRMED
            if max(accounted, order.reported_fill_lots) > order.instruction.quantity_lots:
                candidate = replace(
                    candidate,
                    incidents=tuple(sorted(set((*candidate.incidents, "ORDER_OVERFILLED")))),
                )
                knowledge = Knowledge.RECONCILING
            candidate = _put_order(
                candidate,
                replace(
                    order,
                    accounted_fill_lots=accounted,
                    lifecycle=status,
                    knowledge=knowledge,
                    pending=PendingAction.NONE if status in TERMINAL else order.pending,
                ),
            )
        return candidate

    def _change(self, before: OMSState, after: OMSState, event: Envelope | None) -> OMSChange:
        prior_orders = {o.instruction.client_order_id: o for o in before.orders}
        reservations = []
        for order in after.orders:
            old = prior_orders.get(order.instruction.client_order_id)
            if old == order:
                continue
            if old:
                order = replace(order, revision=old.revision + 1)
                after = _put_order(after, order)
            reservations.append(_reservation(order))
        old_commands = {c.instruction.instruction_id: c for c in before.commands}
        instructions, updates = [], []
        for command in after.commands:
            value = command.instruction
            prior = old_commands.get(value.instruction_id)
            if prior is None:
                instructions.append(
                    OutboxInstruction(
                        value.instruction_id,
                        value.client_order_id,
                        canonical_bytes(value),
                        value.risk_version,
                        value.gateway_fence,
                        event.engine_seq if event else 0,
                        value.expires_at_ns,
                    )
                )
            elif command.status != prior.status:
                updates.append(
                    OutboxStatusUpdate(
                        value.instruction_id,
                        prior.status,
                        command.status,
                        event.event_id if event else "",
                    )
                )
        return OMSChange(
            after,
            tuple(reservations),
            tuple(instructions),
            tuple(updates),
            tuple(i for i in after.incidents if i not in before.incidents),
        )


def execution_reducer(
    oms: OMS, ledger: Ledger, initial_oms: OMSState, initial_ledger: LedgerState
) -> FactReducer:
    """Use this composed reducer in place of the standalone accounting reducer.

    Reports apply economic facts once, followed by their reservation revisions,
    under one engine candidate. Recovery uses the identical fact path.
    """
    accounting = accounting_reducer(ledger, initial_ledger)

    def apply(event: Envelope, state: EngineState) -> Reduction:
        oms_data, ledger_data = (
            state.get("oms", "state-v1", b""),
            state.get("ledger", "state-v1", b""),
        )
        orders = OMSState.from_bytes(oms_data) if oms_data else initial_oms
        portfolio = LedgerState.from_bytes(ledger_data) if ledger_data else initial_ledger
        payload = decode_payload(event)
        if isinstance(payload, OrderApproved):
            change = oms.approve(payload, orders, portfolio, event.available_ns)
            change = replace(
                change,
                outbox_instructions=tuple(
                    replace(i, committed_seq=event.engine_seq) for i in change.outbox_instructions
                ),
            )
        elif event.account_id is not None:
            change = oms.apply(event, orders)
        else:
            change = OMSChange(orders)
        reduction = accounting.apply(event, state)
        portfolio = LedgerState.from_bytes(reduction.state.get("ledger", "state-v1"))
        # Reallocate the shared closing pool against the post-fill position in
        # one batch. A late execution is never rejected because an earlier exit
        # reservation became too large while its cancel report was in flight.
        proposed = {r.reservation_id: r for r in portfolio.reservations}
        for order in change.state.orders:
            proposed[order.instruction.instruction_id] = _reservation(order)
        prior_reserves = {r.reservation_id: r for r in portfolio.reservations}
        free_close = {p.instrument_id: abs(p.signed_lots) for p in portfolio.positions}
        reserve_updates = []
        close_conflict = False
        for key in sorted(proposed):
            reservation = proposed[key]
            if reservation.reduce_only:
                position = portfolio.position(reservation.instrument_id).signed_lots
                eligible = (reservation.side == Side.SELL) == (position > 0)
                allowed = (
                    min(reservation.remaining_lots, free_close.get(reservation.instrument_id, 0))
                    if eligible
                    else 0
                )
                free_close[reservation.instrument_id] = (
                    free_close.get(reservation.instrument_id, 0) - allowed
                )
                if allowed < reservation.remaining_lots:
                    close_conflict = True
                    with localcontext(ACCOUNTING_CONTEXT):
                        reservation = replace(
                            reservation,
                            remaining_lots=allowed,
                            cash_amount=reservation.cash_amount
                            * allowed
                            / reservation.remaining_lots,
                            fee_buffer=reservation.fee_buffer
                            * allowed
                            / reservation.remaining_lots,
                        )
            prior = prior_reserves.get(key)
            if prior is not None:
                if replace(reservation, revision=prior.revision) == prior:
                    continue
                reservation = replace(
                    reservation, revision=max(reservation.revision, prior.revision + 1)
                )
            reserve_updates.append(reservation)
        for instrument in sorted({r.instrument_id for r in reserve_updates}):
            batch = ReservationBatchChanged(
                instrument, tuple(r for r in reserve_updates if r.instrument_id == instrument)
            )
            reserve_event = replace(
                event,
                event_type="ReservationBatchChanged",
                payload=canonical_bytes(batch),
                instrument_id=instrument,
            )
            portfolio = ledger.apply(reserve_event, portfolio).state
        change = replace(change, reservation_updates=tuple(reserve_updates))
        # An externally observed reduce-only violation remains an economic fact.
        # It creates an incident; local instruction approval never authorizes it.
        incidents = set(change.state.incidents)
        if close_conflict:
            incidents.add("CLOSE_RESERVATION_CONFLICT")
        prior_accounted = {
            o.instruction.client_order_id: o.accounted_fill_lots for o in orders.orders
        }
        for order in change.state.orders:
            if order.instruction.reduce_only and order.accounted_fill_lots > prior_accounted.get(
                order.instruction.client_order_id, 0
            ):
                position = portfolio.position(order.instruction.instrument_id).signed_lots
                if position and (position > 0) == (order.instruction.side == Side.BUY):
                    incidents.add("REDUCE_ONLY_VENUE_VIOLATION")
        owners = dict(change.state.symbol_owners)
        for instrument in tuple(owners):
            unresolved = any(
                o.instruction.instrument_id == instrument
                and (
                    o.lifecycle not in TERMINAL
                    or o.reserved_lots
                    or o.knowledge != Knowledge.CONFIRMED
                )
                for o in change.state.orders
            )
            if portfolio.position(instrument).signed_lots == 0 and not unresolved:
                del owners[instrument]
        final_oms = replace(
            change.state,
            incidents=tuple(sorted(incidents)),
            symbol_owners=tuple(sorted(owners.items())),
        )
        changed = reduction.state.put("oms", "state-v1", final_oms.to_bytes())
        changed = changed.put("ledger", "state-v1", portfolio.to_bytes())
        return replace(
            reduction,
            state=changed,
            outbox_instructions=change.outbox_instructions,
            outbox_updates=change.outbox_updates,
            projection_updates=(
                ProjectionUpdate("balances", "ledger-state-v1", portfolio.to_bytes()),
                *replace(change, state=final_oms).projections(),
            ),
        )

    return FactReducer("execution-accounting-v1", Stage.BOOK_ACCOUNT_OMS, apply)
