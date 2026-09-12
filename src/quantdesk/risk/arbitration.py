"""Stable intent ordering, symbol ownership, and shared closing-quantity allocation."""

from dataclasses import dataclass, replace
from decimal import localcontext

from quantdesk.core.events import IntentRejected, StrategyIntent
from quantdesk.core.types import IntentAction, Side
from quantdesk.execution.order_state import TERMINAL, Knowledge, OMSState
from quantdesk.portfolio.arithmetic import ACCOUNTING_CONTEXT
from quantdesk.portfolio.ledger import LedgerState, PortfolioView
from quantdesk.venues.instruments import InstrumentRegistry, InstrumentSpec


@dataclass(frozen=True, slots=True)
class IntentBatch:
    intents: tuple[StrategyIntent, ...]
    rejections: tuple[IntentRejected, ...]
    cancel_client_ids: tuple[str, ...]
    netted_intent_ids: tuple[str, ...] = ()


class Arbitrator:
    def __init__(self, instruments: tuple[InstrumentSpec, ...]) -> None:
        self.instruments = instruments

    def resolve(
        self,
        intents: tuple[StrategyIntent, ...],
        portfolio: LedgerState | PortfolioView,
        oms: OMSState,
    ) -> IntentBatch:
        unique: dict[str, StrategyIntent] = {}
        for intent in intents:
            if intent.intent_id in unique and unique[intent.intent_id] != intent:
                raise ValueError("intent identity reused with changed body")
            unique[intent.intent_id] = intent
        registry = InstrumentRegistry()
        for spec in self.instruments:
            registry.add(spec)
        positions = (
            {p.instrument_id: p.signed_lots for p in portfolio.positions}
            if isinstance(portfolio, LedgerState)
            else {p.position.instrument_id: p.position.signed_lots for p in portfolio.positions}
        )
        accepted: list[StrategyIntent] = []
        rejected: list[IntentRejected] = []
        cancels: set[str] = set()
        netted: list[str] = []
        consumed = {o.instruction.parent_intent_id for o in oms.orders}

        def reject(intent: StrategyIntent, reason: str) -> None:
            rejected.append(
                IntentRejected(intent.intent_id, reason, reason.replace("_", " ").lower())
            )

        for instrument in sorted({i.instrument_id for i in intents}):
            group = [i for i in unique.values() if i.instrument_id == instrument]
            group.sort(key=lambda i: (i.action == IntentAction.ENTER, i.strategy_id, i.intent_id))
            try:
                spec = registry.at(instrument, portfolio.available_ns, portfolio.available_ns)
            except LookupError:
                for item in group:
                    reject(item, "INSTRUMENT_UNAVAILABLE")
                continue
            orders = [
                o
                for o in oms.orders
                if o.instruction.instrument_id == instrument
                and (
                    o.lifecycle not in TERMINAL
                    or o.reserved_lots
                    or o.knowledge != Knowledge.CONFIRMED
                )
            ]
            position = positions.get(instrument, 0)
            closing_reserved = sum(o.reserved_lots for o in orders if o.instruction.reduce_only)
            available_close = max(0, abs(position) - closing_reserved)
            exit_active = any(o.instruction.reduce_only and o.reserved_lots for o in orders)
            uncertain = any(o.knowledge != Knowledge.CONFIRMED for o in orders) or bool(
                oms.unmatched_executions
            )
            owners = {
                o.instruction.owner_strategy_id for o in orders if not o.instruction.reduce_only
            }
            preferred_owner = dict(oms.symbol_owners).get(instrument) or (
                min(owners) if owners else None
            )
            entries: list[StrategyIntent] = []
            for item in group:
                if item.intent_id in consumed:
                    reject(item, "DUPLICATE_INTENT")
                    continue
                if item.expires_at_ns <= portfolio.available_ns:
                    reject(item, "INTENT_EXPIRED")
                    continue
                if item.action == IntentAction.ENTER:
                    entries.append(item)
                    continue
                exit_active = True
                cancels.update(
                    o.instruction.client_order_id for o in orders if not o.instruction.reduce_only
                )
                if item.action == IntentAction.CANCEL_ENTRY:
                    accepted.append(item)
                    continue
                if (
                    position == 0
                    or (item.side == Side.SELL) != (position > 0)
                    or available_close == 0
                ):
                    reject(item, "NO_AVAILABLE_POSITION")
                    continue
                try:
                    desired = (
                        spec.quantity_to_lots(item.desired_quantity)
                        if item.desired_quantity is not None
                        else available_close
                    )
                except (TypeError, ValueError):
                    reject(item, "INVALID_QUANTITY")
                    continue
                lots = min(desired, available_close)
                available_close -= lots
                with localcontext(ACCOUNTING_CONTEXT):
                    accepted.append(replace(item, desired_quantity=spec.quantity_step * lots))
            eligible: list[StrategyIntent] = []
            for item in entries:
                if exit_active:
                    reject(item, "EXIT_ACTIVE")
                elif uncertain or oms.incidents:
                    reject(item, "ORDER_UNCERTAINTY")
                elif position and (position > 0) != (item.side == Side.BUY):
                    reject(item, "CLOSE_BEFORE_REVERSE")
                elif preferred_owner is not None and item.strategy_id != preferred_owner:
                    reject(item, "SYMBOL_OWNED_BY_OTHER_STRATEGY")
                else:
                    preferred_owner = item.strategy_id
                    eligible.append(item)
            if not eligible:
                continue
            first = eligible[0]
            compatible: list[StrategyIntent] = []
            total = 0
            for item in eligible:
                if (
                    item.price_policy,
                    item.stop_policy,
                    item.config_hash,
                    item.model_hash_or_none,
                ) != (
                    first.price_policy,
                    first.stop_policy,
                    first.config_hash,
                    first.model_hash_or_none,
                ) or item.desired_quantity is None:
                    # Risk-budget sizing needs current risk context; do not invent quantity.
                    if len(eligible) == 1:
                        accepted.append(item)
                    else:
                        reject(item, "INCOMPATIBLE_NETTING_POLICY")
                    continue
                try:
                    lots = spec.quantity_to_lots(item.desired_quantity)
                except (ValueError, TypeError):
                    reject(item, "INVALID_QUANTITY")
                    continue
                total += lots if item.side == Side.BUY else -lots
                compatible.append(item)
            if compatible:
                netted.extend(i.intent_id for i in compatible if len(compatible) > 1)
                if total:
                    side = Side.BUY if total > 0 else Side.SELL
                    source = next(i for i in compatible if i.side == side)
                    with localcontext(ACCOUNTING_CONTEXT):
                        accepted.append(
                            replace(
                                source,
                                desired_quantity=spec.quantity_step * abs(total),
                                expires_at_ns=min(i.expires_at_ns for i in compatible),
                            )
                        )
                else:
                    for item in compatible:
                        reject(item, "NETTED_TO_ZERO")
        return IntentBatch(
            tuple(accepted),
            tuple(sorted(rejected, key=lambda r: r.intent_id)),
            tuple(sorted(cancels)),
            tuple(sorted(netted)),
        )
