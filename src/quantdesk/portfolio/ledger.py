"""Pure candidate transitions. The account engine owns commit and publication.

Positive postings are debits. Each economic transaction contains independent
cash/counteraccount pairs, preserving exact balance even for repeating PnL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from hashlib import sha256
from typing import cast

from quantdesk.core.events import (
    CashTransfer,
    Envelope,
    ExecutionReport,
    FeeAdjustment,
    FundingRateAnnounced,
    FundingSettlement,
    LedgerAdjustmentApproved,
    MarkPrice,
    TimerFired,
    canonical_bytes,
)
from quantdesk.core.reducers import _decode_value, decode_payload
from quantdesk.core.types import ExecutionType, Side
from quantdesk.persistence.event_store import (
    EconomicAliasUpdate,
    EconomicIdentity,
    LedgerPosting,
    LedgerTransaction,
    ProjectionUpdate,
)
from quantdesk.portfolio.arithmetic import ACCOUNTING_CONTEXT, ZERO, exact_sum, money
from quantdesk.portfolio.events import (
    ConversionRateObserved,
    FinancialEventObserved,
    ReservationChanged,
)
from quantdesk.portfolio.positions import Position, PositionLeg, apply_fill
from quantdesk.venues.instruments import InstrumentRegistry, InstrumentSpec


@dataclass(frozen=True, slots=True)
class Balance:
    account: str
    asset: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class AppliedIdentity:
    identity: EconomicIdentity
    fingerprint: str
    transaction_id: str


@dataclass(frozen=True, slots=True)
class Mark:
    instrument_id: str
    price: Decimal
    event_ns: int
    available_ns: int
    source: str


@dataclass(frozen=True, slots=True)
class FinancialEvent:
    envelope: Envelope
    aliases: tuple[str, ...] = ()
    source_observation_ids: tuple[str, ...] = ()
    trading_adjustment: bool = False

    def recorded_envelope(self) -> Envelope:
        """Commit this envelope whenever supplemental economic evidence is supplied."""
        if not (self.aliases or self.source_observation_ids or self.trading_adjustment):
            return self.envelope
        payload = decode_payload(self.envelope)
        if not isinstance(
            payload,
            (
                ExecutionReport,
                CashTransfer,
                FundingSettlement,
                FeeAdjustment,
                LedgerAdjustmentApproved,
            ),
        ):
            raise ValueError("supplemental financial evidence requires a financial payload")
        observed = FinancialEventObserved(
            payload, self.aliases, self.source_observation_ids, self.trading_adjustment
        )
        return replace(
            self.envelope, event_type="FinancialEventObserved", payload=canonical_bytes(observed)
        )


@dataclass(frozen=True, slots=True)
class LedgerState:
    venue: str
    environment: str
    account: str
    available_ns: int = 0
    balances: tuple[Balance, ...] = ()
    positions: tuple[Position, ...] = ()
    identities: tuple[AppliedIdentity, ...] = ()
    marks: tuple[Mark, ...] = ()
    transactions: tuple[LedgerTransaction, ...] = ()
    reservations: tuple[ReservationChanged, ...] = ()
    conversions: tuple[ConversionRateObserved, ...] = ()
    trading_adjustments: tuple[tuple[str, Decimal], ...] = ()
    adjustment_sources: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def balance(self, account: str, asset: str = "USDT") -> Decimal:
        return next(
            (b.amount for b in self.balances if (b.account, b.asset) == (account, asset)), ZERO
        )

    def position(self, instrument: str) -> Position:
        return next(
            (p for p in self.positions if p.instrument_id == instrument), Position(instrument)
        )

    def to_bytes(self) -> bytes:
        return canonical_bytes(self)

    @classmethod
    def from_bytes(cls, data: bytes) -> LedgerState:
        result = cast(LedgerState, _decode_value(json.loads(data), cls))
        if canonical_bytes(result) != data:
            raise ValueError("noncanonical ledger checkpoint")
        return result


@dataclass(frozen=True, slots=True)
class PositionView:
    position: Position
    mark_price: Decimal | None
    mark_ns: int | None
    unrealized_pnl: Decimal | None
    notional: Decimal | None


@dataclass(frozen=True, slots=True)
class PortfolioView:
    available_ns: int
    positions: tuple[PositionView, ...]
    wallet_cash: Decimal | None
    realized_pnl: Decimal | None
    total_fees: Decimal | None
    funding_received: Decimal | None
    external_flow: Decimal | None
    unrealized_pnl: Decimal | None
    equity: Decimal | None
    net_pnl: Decimal | None
    unavailable_reasons: tuple[str, ...]
    reserved_cash: Decimal = ZERO
    available_cash: Decimal | None = None
    approved_trading_adjustments: Decimal | None = ZERO
    profile_blockers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LedgerChange:
    state: LedgerState
    transactions: tuple[LedgerTransaction, ...] = ()
    position_updates: tuple[Position, ...] = ()
    position_legs: tuple[PositionLeg, ...] = ()
    duplicate: bool = False
    alias_updates: tuple[EconomicAliasUpdate, ...] = ()

    def projections(self) -> tuple[ProjectionUpdate, ...]:
        return (ProjectionUpdate("balances", "ledger-state-v1", self.state.to_bytes()),)


def economic_key(identity: EconomicIdentity) -> tuple[str, ...]:
    return (
        identity.venue,
        identity.environment,
        identity.account,
        identity.instrument if identity.component_type == "execution" else "",
        identity.native_id,
        identity.component_type,
    )


def _economic_value(value: object) -> object:
    if isinstance(value, Decimal):
        return value.as_integer_ratio()
    if isinstance(value, tuple):
        return tuple(_economic_value(item) for item in value)
    return value


def _pair(asset: str, cash_change: Decimal, account: str) -> tuple[LedgerPosting, ...]:
    money(cash_change)
    return (
        LedgerPosting(f"cash:{asset}", asset, cash_change),
        LedgerPosting(account, asset, cash_change.copy_negate()),
    )


class Ledger:
    def __init__(
        self, instruments: tuple[InstrumentSpec, ...], *, mark_max_age_ns: int = 2_000_000_000
    ) -> None:
        if type(mark_max_age_ns) is not int or mark_max_age_ns < 0:
            raise ValueError("invalid mark age limit")
        self.instruments = instruments
        self.mark_max_age_ns = mark_max_age_ns

    def _spec(self, instrument: str, event_ns: int, available_ns: int) -> InstrumentSpec:
        registry = InstrumentRegistry()
        for spec in self.instruments:
            registry.add(spec)
        result = registry.at(instrument, event_ns, available_ns)
        if (
            result.quote_unit != "USDT"
            or result.settlement_unit != "USDT"
            or result.instrument_id.split(":")[1] not in {"USDT-FUTURES", "linear"}
        ):
            raise ValueError("unsupported linear settlement profile")
        return result

    def apply(self, financial_event: Envelope | FinancialEvent, state: LedgerState) -> LedgerChange:
        fact = (
            financial_event
            if isinstance(financial_event, FinancialEvent)
            else FinancialEvent(financial_event)
        )
        event = fact.recorded_envelope()
        if (event.venue, event.environment, event.account_id) != (
            state.venue,
            state.environment,
            state.account,
        ):
            raise ValueError("financial event belongs to a different account scope")
        if event.available_ns < state.available_ns or event.engine_seq <= 0 or not event.event_id:
            raise ValueError("ledger requires an ordered event and nonregressing timeline")
        payload = decode_payload(event)
        if isinstance(payload, FinancialEventObserved):
            fact = FinancialEvent(
                event, payload.aliases, payload.source_observation_ids, payload.trading_adjustment
            )
            payload = payload.financial_payload
        candidate = replace(state, available_ns=event.available_ns)
        if isinstance(payload, (FundingRateAnnounced, TimerFired)):
            return LedgerChange(candidate)
        if isinstance(payload, ReservationChanged):
            if event.instrument_id != payload.instrument_id:
                raise ValueError("reservation instrument mismatch")
            reservations = {r.reservation_id: r for r in state.reservations}
            prior_reserve = reservations.get(payload.reservation_id)
            if prior_reserve is not None:
                if prior_reserve == payload:
                    return LedgerChange(candidate, duplicate=True)
                if payload.revision <= prior_reserve.revision:
                    raise ValueError("reservation revision regressed or conflicts")
                if (payload.instrument_id, payload.side, payload.reduce_only) != (
                    prior_reserve.instrument_id,
                    prior_reserve.side,
                    prior_reserve.reduce_only,
                ):
                    raise ValueError("reservation scope changed")
            reservations[payload.reservation_id] = payload
            position = state.position(payload.instrument_id)
            closing = [
                r
                for r in reservations.values()
                if r.instrument_id == payload.instrument_id and r.reduce_only and r.remaining_lots
            ]
            if any((r.side == Side.SELL) != (position.signed_lots > 0) for r in closing) or sum(
                r.remaining_lots for r in closing
            ) > abs(position.signed_lots):
                raise ValueError("closing reservations exceed available position")
            return LedgerChange(
                replace(
                    candidate, reservations=tuple(reservations[k] for k in sorted(reservations))
                )
            )
        if isinstance(payload, ConversionRateObserved):
            if payload.event_ns > event.available_ns:
                raise ValueError("future conversion observation")
            conversions = {c.base_asset: c for c in state.conversions}
            if (
                payload.base_asset not in conversions
                or payload.event_ns >= conversions[payload.base_asset].event_ns
            ):
                conversions[payload.base_asset] = payload
            return LedgerChange(
                replace(candidate, conversions=tuple(conversions[k] for k in sorted(conversions)))
            )
        if isinstance(payload, MarkPrice):
            if event.instrument_id is None or payload.event_ns > event.available_ns:
                raise ValueError("invalid mark scope/time")
            mark = Mark(
                event.instrument_id,
                payload.price,
                payload.event_ns,
                event.available_ns,
                payload.source,
            )
            marks = {m.instrument_id: m for m in state.marks}
            if (
                event.instrument_id not in marks
                or payload.event_ns >= marks[event.instrument_id].event_ns
            ):
                marks[event.instrument_id] = mark
            return LedgerChange(replace(candidate, marks=tuple(marks[k] for k in sorted(marks))))
        position_updates: tuple[Position, ...] = ()
        legs: tuple[PositionLeg, ...] = ()
        approved_by: str | None = None
        reversal_of: str | None = None
        if isinstance(payload, ExecutionReport):
            if payload.execution_type != ExecutionType.TRADE or event.instrument_id is None:
                raise ValueError("execution must be a trade with an instrument")
            spec = self._spec(event.instrument_id, payload.event_ns, event.available_ns)
            prior = state.position(event.instrument_id)
            if prior.signed_lots and prior.base_quantity != spec.base_quantity(prior.signed_lots):
                raise ValueError("instrument unit revision conflicts with open position")
            signed = payload.executed_lots * (1 if payload.side == Side.BUY else -1)
            position, realized, legs = apply_fill(prior, signed, payload.price, spec)
            position_updates = (position,)
            native, component, instrument = (
                payload.native_execution_id,
                "execution",
                event.instrument_id,
            )
            postings = _pair("USDT", realized, "income:realized_pnl") + _pair(
                payload.fee_currency, payload.fee_amount.copy_negate(), "expense:trading_fees"
            )
            signature: object = (
                payload.side,
                payload.executed_lots,
                payload.price,
                payload.fee_amount,
                payload.fee_currency,
                payload.execution_type,
            )
        elif isinstance(payload, CashTransfer):
            if money(payload.amount) < 0 or payload.direction not in {"IN", "OUT"}:
                raise ValueError("cash transfer requires nonnegative amount and IN/OUT direction")
            native, component, instrument = payload.native_transaction_id, "transfer", "cash"
            amount = payload.amount if payload.direction == "IN" else payload.amount.copy_negate()
            postings = _pair(payload.asset, amount, "equity:external")
            signature = (amount, payload.asset)
        elif isinstance(payload, FundingSettlement):
            if event.instrument_id != payload.instrument_id:
                raise ValueError("funding instrument mismatch")
            native, component, instrument = (
                payload.native_transaction_id,
                "funding",
                payload.instrument_id,
            )
            postings = _pair(payload.asset, payload.amount, "income:funding")
            signature = (
                payload.amount,
                payload.asset,
                payload.instrument_id,
                payload.settlement_ns,
            )
        elif isinstance(payload, FeeAdjustment):
            if not payload.reason or not event.instrument_id:
                raise ValueError("fee correction requires reason and execution instrument")
            execution_identity = EconomicIdentity(
                state.venue,
                state.environment,
                state.account,
                event.instrument_id,
                payload.native_execution_id,
                "execution",
            )
            original = next(
                (
                    r
                    for r in state.identities
                    if economic_key(r.identity) == economic_key(execution_identity)
                ),
                None,
            )
            if original is None:
                raise ValueError("fee correction references unknown execution")
            original_transaction = next(
                t for t in state.transactions if t.transaction_id == original.transaction_id
            )
            if not any(
                p.account == "expense:trading_fees" and p.asset == payload.asset
                for p in original_transaction.postings
            ):
                raise ValueError("fee correction changes execution fee asset")
            native, component, instrument = (
                payload.native_transaction_id,
                "fee_adjustment",
                event.instrument_id,
            )
            postings = _pair(payload.asset, payload.amount.copy_negate(), "expense:trading_fees")
            reversal_of = original.transaction_id
            signature = (payload.amount, payload.asset, original.transaction_id)
        elif isinstance(payload, LedgerAdjustmentApproved):
            if (
                not payload.author.strip()
                or not payload.reason.strip()
                or not fact.source_observation_ids
                or not all(fact.source_observation_ids)
            ):
                raise ValueError("adjustment requires author, reason, and source observations")
            native, component, instrument = (
                payload.adjustment_id,
                "adjustment",
                event.instrument_id or "cash",
            )
            postings = _pair(payload.asset, payload.amount, "equity:adjustments")
            approved_by = payload.author
            signature = (
                payload.amount,
                payload.asset,
                payload.reason,
                payload.author,
                fact.source_observation_ids,
                fact.trading_adjustment,
            )
        else:
            raise ValueError("unsupported ledger event")
        if not native or not all(p.asset for p in postings):
            raise ValueError("native financial identity and asset required")
        identity = EconomicIdentity(
            state.venue, state.environment, state.account, instrument, native, component
        )
        if not all(fact.aliases) or len(set((native, *fact.aliases))) != 1 + len(fact.aliases):
            raise ValueError("economic aliases must be nonempty and unique")
        aliases = tuple(replace(identity, native_id=alias) for alias in fact.aliases)
        fingerprint = sha256(canonical_bytes(_economic_value(signature))).hexdigest()
        keys = {economic_key(i) for i in (identity, *aliases)}
        matches = [r for r in state.identities if economic_key(r.identity) in keys]
        if matches:
            if (
                any(r.fingerprint != fingerprint for r in matches)
                or len({r.transaction_id for r in matches}) != 1
            ):
                raise ValueError("conflicting economic duplicate")
            existing = {economic_key(r.identity) for r in matches}
            additions = tuple(i for i in (identity, *aliases) if economic_key(i) not in existing)
            candidate = replace(
                candidate,
                identities=state.identities
                + tuple(
                    AppliedIdentity(i, fingerprint, matches[0].transaction_id) for i in additions
                ),
            )
            return LedgerChange(
                candidate,
                duplicate=True,
                alias_updates=tuple(
                    EconomicAliasUpdate(event.event_id, matches[0].transaction_id, i)
                    for i in additions
                ),
            )
        transaction_id = sha256(canonical_bytes(economic_key(identity))).hexdigest()
        transaction = LedgerTransaction(
            transaction_id, event.event_id, identity, postings, aliases, approved_by, reversal_of
        )
        balances = {(b.account, b.asset): b.amount for b in state.balances}
        for posting in postings:
            key = (posting.account, posting.asset)
            balances[key] = exact_sum(balances.get(key, ZERO), posting.amount)
        positions = {p.instrument_id: p for p in state.positions}
        positions.update((p.instrument_id, p) for p in position_updates)
        candidate = replace(
            candidate,
            balances=tuple(Balance(*key, balances[key]) for key in sorted(balances)),
            positions=tuple(positions[k] for k in sorted(positions)),
            identities=state.identities
            + tuple(AppliedIdentity(i, fingerprint, transaction_id) for i in (identity, *aliases)),
            transactions=(*state.transactions, transaction),
        )
        if isinstance(payload, LedgerAdjustmentApproved):
            candidate = replace(
                candidate,
                adjustment_sources=(
                    *state.adjustment_sources,
                    (transaction_id, fact.source_observation_ids),
                ),
            )
            if fact.trading_adjustment:
                adjustments = dict(state.trading_adjustments)
                adjustments[payload.asset] = exact_sum(
                    adjustments.get(payload.asset, ZERO), payload.amount
                )
                candidate = replace(
                    candidate, trading_adjustments=tuple(sorted(adjustments.items()))
                )
        return LedgerChange(candidate, (transaction,), position_updates, legs)

    def snapshot(self, state: LedgerState) -> PortfolioView:
        reasons: list[str] = []
        views: list[PositionView] = []
        marks = {m.instrument_id: m for m in state.marks}
        unrealized = ZERO
        with localcontext(ACCOUNTING_CONTEXT):
            all_positions = {p.instrument_id: p for p in state.positions}
            for instrument in marks:
                all_positions.setdefault(instrument, Position(instrument))
            for position in (all_positions[key] for key in sorted(all_positions)):
                mark = marks.get(position.instrument_id)
                valid = (
                    mark is not None
                    and 0 <= state.available_ns - mark.event_ns <= self.mark_max_age_ns
                )
                if position.signed_lots == 0:
                    views.append(
                        PositionView(
                            position,
                            mark.price if valid and mark else None,
                            mark.event_ns if valid and mark else None,
                            ZERO,
                            ZERO,
                        )
                    )
                elif valid and mark is not None and position.average_entry is not None:
                    pnl = position.base_quantity * (mark.price - position.average_entry)
                    unrealized += pnl
                    views.append(
                        PositionView(
                            position,
                            mark.price,
                            mark.event_ns,
                            pnl,
                            abs(position.base_quantity * mark.price),
                        )
                    )
                else:
                    reasons.append(f"MARK_UNAVAILABLE:{position.instrument_id}")
                    views.append(PositionView(position, None, None, None, None))
            conversions = {c.base_asset: c for c in state.conversions}

            def convert(amounts: tuple[tuple[str, Decimal], ...]) -> Decimal | None:
                result = ZERO
                missing = False
                for asset, amount in amounts:
                    if not amount:
                        continue
                    if asset == "USDT":
                        result = exact_sum(result, amount)
                    elif (
                        asset in conversions
                        and 0
                        <= state.available_ns - conversions[asset].event_ns
                        <= self.mark_max_age_ns
                    ):
                        result = exact_sum(result, amount * conversions[asset].rate)
                    else:
                        missing = True
                        reasons.append(f"CONVERSION_UNAVAILABLE:{asset}")
                return None if missing else result

            def total(account: str, sign: int = 1) -> Decimal | None:
                return convert(
                    tuple(
                        (b.asset, b.amount if sign == 1 else b.amount.copy_negate())
                        for b in state.balances
                        if b.account == account
                        or (account == "cash" and b.account == f"cash:{b.asset}")
                    )
                )

            wallet = total("cash")
            realized = total("income:realized_pnl", -1)
            fees = total("expense:trading_fees")
            funding = total("income:funding", -1)
            external = total("equity:external", -1)
            adjustments = convert(state.trading_adjustments)
            reserved = exact_sum(
                *(exact_sum(r.cash_amount, r.fee_buffer) for r in state.reservations)
            )
            equity = None if reasons or wallet is None else exact_sum(wallet, unrealized)
            net_pnl = (
                None
                if reasons or any(v is None for v in (realized, fees, funding, adjustments))
                else exact_sum(
                    cast(Decimal, realized),
                    unrealized,
                    cast(Decimal, fees).copy_negate(),
                    cast(Decimal, funding),
                    cast(Decimal, adjustments),
                )
            )
            blockers = tuple(
                sorted(
                    {
                        f"UNSUPPORTED_FEE_CURRENCY:{b.asset}"
                        for b in state.balances
                        if b.account == "expense:trading_fees" and b.asset != "USDT"
                    }
                )
            )
            return PortfolioView(
                state.available_ns,
                tuple(views),
                wallet,
                realized,
                fees,
                funding,
                external,
                None if any(r.startswith("MARK_") for r in reasons) else unrealized,
                equity,
                net_pnl,
                tuple(sorted(set(reasons))),
                reserved,
                None if wallet is None else exact_sum(wallet, reserved.copy_negate()),
                adjustments,
                blockers,
            )
