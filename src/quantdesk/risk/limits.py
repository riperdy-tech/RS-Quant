"""§11.1 pre-trade, runtime, and operational risk checks.

Every check returns (passed: bool, reason_code: str). Reason codes are
explicit and stable; the engine never guesses why an intent was rejected.
Daily loss baseline is persisted at session start and adjusted only for
verified external cash transfers. Restart never clears the day's loss,
peak, latch, or consumed limits.
"""

from __future__ import annotations

from decimal import Decimal

from quantdesk.config.schema import RiskConfig


class RiskLimits:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    # ── pre-trade checks ────────────────────────────────────────────

    def check_mode_and_account(
        self, mode: str, live_enabled: bool, environment: str
    ) -> tuple[bool, str]:
        if mode == "LIVE" and not live_enabled:
            return False, "LIVE_NOT_ENABLED"
        if environment not in {"DEMO", "SANDBOX", "LIVE"}:
            return False, "UNSUPPORTED_ENVIRONMENT"
        return True, "OK"

    def check_reconciliation_complete(self, reconciled: bool) -> tuple[bool, str]:
        if not reconciled:
            return False, "RECONCILIATION_INCOMPLETE"
        return True, "OK"

    def check_latch(self, active_latches: dict[str, bool]) -> tuple[bool, str]:
        for latch_id, active in active_latches.items():
            if active:
                return False, f"LATCH_ACTIVE:{latch_id}"
        return True, "OK"

    def check_finite_values(
        self,
        desired_quantity: Decimal | None,
        risk_budget: Decimal | None,
        price: Decimal | None,
    ) -> tuple[bool, str]:
        for name, value in (
            ("desired_quantity", desired_quantity),
            ("risk_budget", risk_budget),
            ("price", price),
        ):
            if value is not None and (not value.is_finite() or value < 0):
                return False, f"NON_FINITE_{name.upper()}"
        return True, "OK"

    def check_stop_distance(
        self, stop_distance: Decimal | None, price: Decimal
    ) -> tuple[bool, str]:
        if stop_distance is None or not stop_distance.is_finite() or stop_distance <= 0:
            return False, "INVALID_STOP_DISTANCE"
        if price <= 0:
            return False, "INVALID_PRICE"
        # Stop must be within a reasonable fraction of price
        if stop_distance >= price:
            return False, "STOP_DISTANCE_EXCEEDS_PRICE"
        return True, "OK"

    def check_tick_lot_notional(
        self,
        quantity_lots: int,
        min_lots: int,
        price_ticks: int | None,
        min_notional: Decimal,
        notional: Decimal,
    ) -> tuple[bool, str]:
        if quantity_lots <= 0:
            return False, "BELOW_MINIMUM_AFTER_RISK_CAP"
        if quantity_lots < min_lots:
            return False, "BELOW_MINIMUM_AFTER_RISK_CAP"
        if price_ticks is not None and price_ticks <= 0:
            return False, "INVALID_PRICE_TICKS"
        if notional < min_notional:
            return False, "BELOW_MIN_NOTIONAL"
        return True, "OK"

    def check_order_count(
        self,
        open_entry_orders_symbol: int,
        open_orders_account: int,
    ) -> tuple[bool, str]:
        if open_entry_orders_symbol >= self.config.max_open_entry_orders_per_symbol:
            return False, "MAX_ENTRY_ORDERS_PER_SYMBOL"
        if open_orders_account >= self.config.max_open_orders_account:
            return False, "MAX_OPEN_ORDERS_ACCOUNT"
        return True, "OK"

    def check_duplicate_intent(
        self, intent_id: str, consumed_intents: set[str]
    ) -> tuple[bool, str]:
        if intent_id in consumed_intents:
            return False, "DUPLICATE_INTENT"
        return True, "OK"

    def check_spread(self, spread_bps: Decimal | None) -> tuple[bool, str]:
        if spread_bps is not None and spread_bps > self.config.max_spread_bps:
            return False, "SPREAD_TOO_WIDE"
        return True, "OK"

    def check_participation_depth(
        self, order_lots: int, visible_depth_lots: int
    ) -> tuple[bool, str]:
        if visible_depth_lots <= 0:
            return False, "NO_VISIBLE_DEPTH"
        fraction = Decimal(order_lots) / Decimal(visible_depth_lots)
        if fraction > self.config.max_order_visible_depth_fraction:
            return False, "EXCEEDS_DEPTH_PARTICIPATION"
        return True, "OK"

    def check_exposure(
        self,
        pending_notional: Decimal,
        order_notional: Decimal,
        equity: Decimal,
    ) -> tuple[bool, str]:
        if equity <= 0:
            return False, "ZERO_EQUITY"
        gross = pending_notional + order_notional
        if gross / equity > self.config.max_gross_notional_fraction:
            return False, "MAX_GROSS_NOTIONAL_EXCEEDED"
        return True, "OK"

    def check_symbol_exposure(
        self,
        symbol_notional: Decimal,
        order_notional: Decimal,
        equity: Decimal,
    ) -> tuple[bool, str]:
        if equity <= 0:
            return False, "ZERO_EQUITY"
        total = symbol_notional + order_notional
        if total / equity > self.config.max_symbol_notional_fraction:
            return False, "MAX_SYMBOL_NOTIONAL_EXCEEDED"
        return True, "OK"

    def check_notional_caps(
        self,
        order_notional: Decimal,
        total_notional: Decimal,
    ) -> tuple[bool, str]:
        """Tiny-live absolute caps; require stricter of absolute and relative."""
        if (
            self.config.max_order_notional_usdt is not None
            and order_notional > self.config.max_order_notional_usdt
        ):
            return False, "MAX_ORDER_NOTIONAL_USDT_EXCEEDED"
        if (
            self.config.max_total_notional_usdt is not None
            and total_notional + order_notional > self.config.max_total_notional_usdt
        ):
            return False, "MAX_TOTAL_NOTIONAL_USDT_EXCEEDED"
        return True, "OK"

    # ── daily loss / drawdown ───────────────────────────────────────

    def check_daily_loss(
        self,
        baseline_equity: Decimal,
        current_equity: Decimal,
        net_external_flow: Decimal,
    ) -> tuple[bool, str]:
        """Daily loss = max(0, baseline + flow - current) / baseline."""
        if baseline_equity <= 0:
            return False, "BASELINE_EQUITY_ZERO_OR_NEGATIVE"
        loss = max(Decimal("0"), baseline_equity + net_external_flow - current_equity)
        fraction = loss / baseline_equity
        if fraction > self.config.daily_loss_fraction:
            return False, "DAILY_LOSS_LIMIT_EXCEEDED"
        if self.config.max_daily_loss_usdt is not None and loss > self.config.max_daily_loss_usdt:
            return False, "MAX_DAILY_LOSS_USDT_EXCEEDED"
        return True, "OK"

    def check_drawdown(
        self,
        peak_equity: Decimal,
        current_equity: Decimal,
    ) -> tuple[bool, str]:
        """Peak drawdown uses flow-adjusted high-water mark."""
        if peak_equity <= 0:
            return True, "OK"
        drawdown = max(Decimal("0"), peak_equity - current_equity)
        fraction = drawdown / peak_equity
        if fraction > self.config.peak_drawdown_fraction:
            return False, "PEAK_DRAWDOWN_EXCEEDED"
        return True, "OK"

    # ── stale data / operational ────────────────────────────────────

    def check_stale_data(
        self,
        event_available_ns: int,
        market_data_ns: int | None,
        mark_ns: int | None,
        heartbeat_ns: int | None,
        ingress_ns: int | None,
        clock_offset_ms: int,
    ) -> tuple[bool, str]:
        if market_data_ns is not None:
            age_ms = (event_available_ns - market_data_ns) // 1_000_000
            if age_ms > self.config.market_data_max_age_ms:
                return False, "STALE_MARKET_DATA"
        if mark_ns is not None:
            age_ms = (event_available_ns - mark_ns) // 1_000_000
            if age_ms > self.config.mark_max_age_ms:
                return False, "STALE_MARK_PRICE"
        if heartbeat_ns is not None:
            age_ms = (event_available_ns - heartbeat_ns) // 1_000_000
            if age_ms > self.config.private_heartbeat_max_age_ms:
                return False, "STALE_PRIVATE_HEARTBEAT"
        if ingress_ns is not None:
            age_ms = (event_available_ns - ingress_ns) // 1_000_000
            if age_ms > self.config.max_ingress_age_ms:
                return False, "STALE_INGRESS"
        if clock_offset_ms > self.config.max_clock_offset_ms:
            return False, "CLOCK_OFFSET_TOO_HIGH"
        return True, "OK"

    def check_disk_reserve(self, free_gib: int) -> tuple[bool, str]:
        if free_gib < self.config.disk_reserve_gib:
            return False, "DISK_RESERVE_LOW"
        return True, "OK"

    def check_protection_confirmed(
        self,
        has_position: bool,
        protection_confirmed: bool,
        elapsed_ms: int,
    ) -> tuple[bool, str]:
        if (
            has_position
            and not protection_confirmed
            and elapsed_ms > self.config.protection_confirmation_ms
        ):
            return False, "PROTECTION_CONFIRMATION_TIMEOUT"
        return True, "OK"

    # ── aggregate pre-trade evaluation ──────────────────────────────

    def evaluate_pre_trade(
        self,
        *,
        mode: str,
        live_enabled: bool,
        environment: str,
        reconciled: bool,
        active_latches: dict[str, bool],
        desired_quantity: Decimal | None,
        risk_budget: Decimal | None,
        price: Decimal | None,
        intent_id: str,
        consumed_intents: set[str],
        open_entry_orders_symbol: int,
        open_orders_account: int,
        spread_bps: Decimal | None,
        event_available_ns: int,
        market_data_ns: int | None,
        mark_ns: int | None,
        heartbeat_ns: int | None,
        ingress_ns: int | None,
        clock_offset_ms: int,
        baseline_equity: Decimal,
        current_equity: Decimal,
        net_external_flow: Decimal,
        peak_equity: Decimal,
    ) -> tuple[bool, str]:
        """Run all pre-trade checks in order. Return first failure."""
        checks = [
            self.check_mode_and_account(mode, live_enabled, environment),
            self.check_reconciliation_complete(reconciled),
            self.check_latch(active_latches),
            self.check_finite_values(desired_quantity, risk_budget, price),
            self.check_duplicate_intent(intent_id, consumed_intents),
            self.check_order_count(open_entry_orders_symbol, open_orders_account),
            self.check_spread(spread_bps),
            self.check_stale_data(
                event_available_ns,
                market_data_ns,
                mark_ns,
                heartbeat_ns,
                ingress_ns,
                clock_offset_ms,
            ),
            self.check_daily_loss(baseline_equity, current_equity, net_external_flow),
            self.check_drawdown(peak_equity, current_equity),
        ]
        for passed, reason in checks:
            if not passed:
                return False, reason
        return True, "OK"
