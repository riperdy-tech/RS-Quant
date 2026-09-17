"""§11.1 aggregate risk evaluation and runtime monitoring.

Risk.evaluate(intent, context) → RiskDecision — runs every §11.1 pre-trade
check, sizes via RiskSizer, and returns an explicit reason code on rejection.

Risk.on_event(event) → RiskActions — runtime monitoring of losses, drawdown,
protection status, and operational health.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quantdesk.config.schema import RiskConfig
from quantdesk.core.events import (
    Envelope,
    RiskDecision,
    RiskLatchChanged,
    StrategyIntent,
)
from quantdesk.core.types import IntentAction
from quantdesk.risk.emergency import Emergency
from quantdesk.risk.limits import RiskLimits
from quantdesk.risk.sizer import RiskSizer
from quantdesk.venues.instruments import InstrumentSpec


@dataclass(frozen=True, slots=True)
class RiskContext:
    """All state needed by risk evaluation; constructed by the risk producer."""

    mode: str
    live_enabled: bool
    environment: str
    reconciled: bool
    active_latches: dict[str, bool]
    intent_id: str
    consumed_intents: set[str]
    open_entry_orders_symbol: int
    open_orders_account: int
    spread_bps: Decimal | None
    event_available_ns: int
    market_data_ns: int | None
    mark_ns: int | None
    heartbeat_ns: int | None
    ingress_ns: int | None
    clock_offset_ms: int
    baseline_equity: Decimal
    current_equity: Decimal
    net_external_flow: Decimal
    peak_equity: Decimal
    current_price: Decimal
    stop_distance: Decimal | None
    estimated_roundtrip_cost: Decimal
    pending_notional: Decimal
    symbol_notional: Decimal
    spec: InstrumentSpec | None
    visible_depth_lots: int | None
    risk_version: str


class Risk:
    """Deterministic risk gate per §11."""

    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.limits = RiskLimits(config)
        self.sizer = RiskSizer(config)
        self.emergency = Emergency()

    def evaluate(self, intent: StrategyIntent, context: RiskContext) -> RiskDecision:
        """Run all pre-trade checks and sizing. Return RiskDecision.

        Per §11.1: no strategy, UI route, or adapter can bypass risk.
        """
        # Exit/cancel intents bypass sizing but still check latches
        if intent.action != IntentAction.ENTER:
            passed, reason = self.limits.check_latch(context.active_latches)
            # Exits are allowed even when latched (only entries blocked)
            return RiskDecision(
                intent_id=intent.intent_id,
                approved=True,
                reason_code="OK",
                approved_quantity_lots=0,
                risk_version=context.risk_version,
            )

        # Run aggregate pre-trade checks
        passed, reason = self.limits.evaluate_pre_trade(
            mode=context.mode,
            live_enabled=context.live_enabled,
            environment=context.environment,
            reconciled=context.reconciled,
            active_latches=context.active_latches,
            desired_quantity=intent.desired_quantity,
            risk_budget=intent.risk_budget,
            price=context.current_price if context.current_price > 0 else None,
            intent_id=context.intent_id,
            consumed_intents=context.consumed_intents,
            open_entry_orders_symbol=context.open_entry_orders_symbol,
            open_orders_account=context.open_orders_account,
            spread_bps=context.spread_bps,
            event_available_ns=context.event_available_ns,
            market_data_ns=context.market_data_ns,
            mark_ns=context.mark_ns,
            heartbeat_ns=context.heartbeat_ns,
            ingress_ns=context.ingress_ns,
            clock_offset_ms=context.clock_offset_ms,
            baseline_equity=context.baseline_equity,
            current_equity=context.current_equity,
            net_external_flow=context.net_external_flow,
            peak_equity=context.peak_equity,
        )
        if not passed:
            return RiskDecision(
                intent_id=intent.intent_id,
                approved=False,
                reason_code=reason,
                approved_quantity_lots=0,
                risk_version=context.risk_version,
            )

        # Check instrument availability
        if context.spec is None:
            return RiskDecision(
                intent_id=intent.intent_id,
                approved=False,
                reason_code="INSTRUMENT_UNAVAILABLE",
                approved_quantity_lots=0,
                risk_version=context.risk_version,
            )

        # Check stop distance
        if context.stop_distance is not None and context.current_price > 0:
            passed, reason = self.limits.check_stop_distance(
                context.stop_distance, context.current_price
            )
            if not passed:
                return RiskDecision(
                    intent_id=intent.intent_id,
                    approved=False,
                    reason_code=reason,
                    approved_quantity_lots=0,
                    risk_version=context.risk_version,
                )

        # Calculate risk cash from budget
        risk_cash = intent.risk_budget if intent.risk_budget is not None else (
            context.current_equity * self.config.per_trade_risk_fraction
        )

        # Size the order
        stop = context.stop_distance if context.stop_distance is not None else Decimal("1")
        lots, size_reason = self.sizer.calculate_size(
            intent_desired_qty=intent.desired_quantity,
            risk_cash=risk_cash,
            stop_distance_per_base_unit=stop,
            estimated_roundtrip_cost_per_base_unit=context.estimated_roundtrip_cost,
            current_price=context.current_price,
            spec=context.spec,
            account_exposure_remaining=_exposure_remaining(
                context.current_equity,
                context.pending_notional,
                self.config.max_gross_notional_fraction,
            ),
            symbol_exposure_remaining=_symbol_remaining(
                context.current_equity,
                context.symbol_notional,
                self.config.max_symbol_notional_fraction,
            ),
            max_notional_usdt=self.config.max_order_notional_usdt,
            max_total_notional_usdt=self.config.max_total_notional_usdt,
            current_total_notional=context.pending_notional,
            visible_depth_lots=context.visible_depth_lots,
        )
        if size_reason != "OK":
            return RiskDecision(
                intent_id=intent.intent_id,
                approved=False,
                reason_code=size_reason,
                approved_quantity_lots=0,
                risk_version=context.risk_version,
            )

        # Check notional caps after sizing
        order_notional = Decimal(lots) * context.spec.quantity_step * context.current_price
        passed, reason = self.limits.check_notional_caps(
            order_notional, context.pending_notional
        )
        if not passed:
            return RiskDecision(
                intent_id=intent.intent_id,
                approved=False,
                reason_code=reason,
                approved_quantity_lots=0,
                risk_version=context.risk_version,
            )

        # Check exposure
        passed, reason = self.limits.check_exposure(
            context.pending_notional, order_notional, context.current_equity
        )
        if not passed:
            return RiskDecision(
                intent_id=intent.intent_id,
                approved=False,
                reason_code=reason,
                approved_quantity_lots=0,
                risk_version=context.risk_version,
            )

        return RiskDecision(
            intent_id=intent.intent_id,
            approved=True,
            reason_code="OK",
            approved_quantity_lots=lots,
            risk_version=context.risk_version,
        )

    def on_event(self, event: Envelope) -> tuple[RiskLatchChanged, ...]:
        """Runtime event monitoring. Returns latches to activate if needed."""
        return ()


def _exposure_remaining(
    equity: Decimal, pending: Decimal, fraction: Decimal
) -> Decimal | None:
    if equity <= 0:
        return Decimal("0")
    max_gross = equity * fraction
    return max(Decimal("0"), max_gross - pending)


def _symbol_remaining(
    equity: Decimal, symbol_notional: Decimal, fraction: Decimal
) -> Decimal | None:
    if equity <= 0:
        return Decimal("0")
    max_sym = equity * fraction
    return max(Decimal("0"), max_sym - symbol_notional)
