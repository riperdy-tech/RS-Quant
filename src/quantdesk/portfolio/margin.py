"""Explicit local isolated-margin estimates; unavailable inputs never become zero."""

from dataclasses import dataclass
from decimal import Decimal, localcontext

from quantdesk.core.types import Side
from quantdesk.portfolio.arithmetic import ACCOUNTING_CONTEXT, ZERO, exact_sum, money
from quantdesk.portfolio.events import ReservationChanged
from quantdesk.portfolio.ledger import PortfolioView
from quantdesk.venues.instruments import InstrumentRegistry, InstrumentSpec


@dataclass(frozen=True, slots=True)
class MarginTier:
    notional_cap: Decimal
    maintenance_rate: Decimal
    maintenance_deduction: Decimal

    def __post_init__(self) -> None:
        if money(self.notional_cap) <= 0 or not ZERO <= money(self.maintenance_rate) <= 1:
            raise ValueError("invalid margin tier")
        if money(self.maintenance_deduction) < 0:
            raise ValueError("invalid maintenance deduction")


@dataclass(frozen=True, slots=True)
class MarginSpec:
    instrument: InstrumentSpec
    leverage: Decimal | None
    fee_buffer: Decimal
    tiers: tuple[MarginTier, ...]
    tier_revision: str
    observed_ns: int
    max_age_ns: int
    venue_available_collateral: Decimal | None = None
    venue_observed_ns: int | None = None
    require_venue_bounds: bool = False


@dataclass(frozen=True, slots=True)
class MarginView:
    status: str
    initial_margin: Decimal | None
    maintenance_margin: Decimal | None
    incremental_pending_reserve: Decimal | None
    available_collateral: Decimal | None
    worst_long_lots: int
    worst_short_lots: int
    reasons: tuple[str, ...]
    source: str = "LOCAL_ESTIMATE"
    tier_revision: str | None = None
    liquidation_price: Decimal | None = None


class Margin:
    @staticmethod
    def estimate(
        portfolio: PortfolioView, pending: tuple[ReservationChanged, ...], spec: MarginSpec
    ) -> MarginView:
        relevant = tuple(r for r in pending if r.instrument_id == spec.instrument.instrument_id)
        if len({r.reservation_id for r in pending}) != len(pending):
            raise ValueError("duplicate pending reservation")
        position = next(
            (
                p
                for p in portfolio.positions
                if p.position.instrument_id == spec.instrument.instrument_id
            ),
            None,
        )
        lots = 0 if position is None else position.position.signed_lots
        # For each extreme, reducing the initial opposite position first makes
        # more room for ordinary orders to open new exposure. Other-side orders
        # can be left unfilled; a reduce-only fill itself never crosses zero.
        close_short = min(
            max(-lots, 0),
            sum(r.remaining_lots for r in relevant if r.side == Side.BUY and r.reduce_only),
        )
        close_long = min(
            max(lots, 0),
            sum(r.remaining_lots for r in relevant if r.side == Side.SELL and r.reduce_only),
        )
        long = (
            lots
            + close_short
            + sum(r.remaining_lots for r in relevant if r.side == Side.BUY and not r.reduce_only)
        )
        short = (
            lots
            - close_long
            - sum(r.remaining_lots for r in relevant if r.side == Side.SELL and not r.reduce_only)
        )
        reasons: list[str] = []
        registry = InstrumentRegistry()
        try:
            registry.add(spec.instrument)
            registry.at(
                spec.instrument.instrument_id, portfolio.available_ns, portfolio.available_ns
            )
        except (ValueError, LookupError):
            reasons.append("INSTRUMENT_SPEC_UNAVAILABLE")
        if position is not None:
            if lots and not position.position.spec_revision:
                reasons.append("POSITION_SPEC_UNAVAILABLE")
            if position.position.base_quantity != spec.instrument.base_quantity(lots):
                reasons.append("POSITION_UNIT_MISMATCH")
        instrument_parts = spec.instrument.instrument_id.split(":")
        if (
            spec.instrument.quote_unit != "USDT"
            or spec.instrument.settlement_unit != "USDT"
            or len(instrument_parts) != 6
            or instrument_parts[1] not in {"USDT-FUTURES", "linear"}
        ):
            reasons.append("UNSUPPORTED_LINEAR_MARGIN_PROFILE")
        if not spec.tiers or not spec.tier_revision:
            reasons.append("MISSING_RISK_TIERS")
        if (
            spec.max_age_ns < 0
            or not 0 <= portfolio.available_ns - spec.observed_ns <= spec.max_age_ns
        ):
            reasons.append("STALE_MARGIN_INPUT")
        if spec.leverage is None:
            reasons.append("MISSING_LEVERAGE")
        elif money(spec.leverage) <= 0:
            raise ValueError("leverage must be positive")
        elif not spec.instrument.min_leverage <= spec.leverage <= spec.instrument.max_leverage:
            raise ValueError("leverage outside instrument bounds")
        if money(spec.fee_buffer) < 0:
            raise ValueError("fee buffer must be nonnegative")
        if portfolio.equity is None or position is None or position.mark_price is None:
            reasons.append("MARK_OR_EQUITY_UNAVAILABLE")
        if (spec.require_venue_bounds or spec.venue_available_collateral is not None) and (
            spec.venue_available_collateral is None
            or spec.venue_observed_ns is None
            or not 0 <= portfolio.available_ns - spec.venue_observed_ns <= spec.max_age_ns
        ):
            reasons.append("VENUE_COLLATERAL_UNAVAILABLE")
        caps = [tier.notional_cap for tier in spec.tiers]
        if caps != sorted(set(caps)):
            raise ValueError("risk tiers must have strictly increasing caps")
        if reasons:
            return MarginView(
                "INCOMPLETE_MARGIN_MODEL",
                None,
                None,
                None,
                None,
                long,
                short,
                tuple(reasons),
                tier_revision=spec.tier_revision or None,
            )
        assert (
            position is not None and position.mark_price is not None and spec.leverage is not None
        )
        assert portfolio.equity is not None
        with localcontext(ACCOUNTING_CONTEXT):
            # Stored base quantity is the accounting fact. Verified current
            # instrument units apply only to the possible additional fills.
            current_base = position.position.base_quantity
            long_base = exact_sum(current_base, spec.instrument.base_quantity(long - lots))
            short_base = exact_sum(current_base, spec.instrument.base_quantity(short - lots))
            worst = max(long_base.copy_abs(), short_base.copy_abs()) * position.mark_price
            current = current_base.copy_abs() * position.mark_price
            tier = next((tier for tier in spec.tiers if worst <= tier.notional_cap), None)
            if tier is None:
                return MarginView(
                    "INCOMPLETE_MARGIN_MODEL",
                    None,
                    None,
                    None,
                    None,
                    long,
                    short,
                    ("RISK_TIER_COVERAGE_UNAVAILABLE",),
                    tier_revision=spec.tier_revision,
                )
            pending_cash = exact_sum(*(r.cash_amount for r in relevant))
            pending_fees = exact_sum(*(r.fee_buffer for r in relevant))
            incremental = max(ZERO, (worst - current) / spec.leverage, pending_cash)
            initial = exact_sum(current / spec.leverage, spec.fee_buffer, incremental, pending_fees)
            maintenance = worst * tier.maintenance_rate - tier.maintenance_deduction
            if maintenance < 0:
                raise ValueError("tier gives negative maintenance")
            available = portfolio.equity
            if spec.venue_available_collateral is not None:
                available = min(available, money(spec.venue_available_collateral))
            return MarginView(
                "ESTIMATED",
                initial,
                maintenance,
                incremental,
                available,
                long,
                short,
                (),
                tier_revision=spec.tier_revision,
            )
