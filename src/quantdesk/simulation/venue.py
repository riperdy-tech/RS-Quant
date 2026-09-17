from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from quantdesk.core.events import Envelope
from quantdesk.simulation.fees import FeeProfile, calculate_fee
from quantdesk.simulation.funding import calculate_funding_payment
from quantdesk.simulation.latency import LatencyProfile
from quantdesk.simulation.liquidity import LiquidityBudget
from quantdesk.simulation.queue import QueueEstimator
from quantdesk.simulation.scheduler import VirtualTimeline


class SimVenue:
    """High-fidelity simulated exchange matching engine per §14."""

    def __init__(
        self,
        timeline: VirtualTimeline,
        latency: LatencyProfile,
        fee_profile: FeeProfile,
        isolated_collateral_ticks: Decimal = Decimal("100000"),
        maintenance_margin_rate: Decimal = Decimal("0.005"),
    ) -> None:
        self.timeline = timeline
        self.latency = latency
        self.fee_profile = fee_profile
        self.liquidity = LiquidityBudget()
        self.queue = QueueEstimator()
        self.outbox: list[Envelope] = []

        # Current orderbook state
        self.bids: list[tuple[int, int]] = []
        self.asks: list[tuple[int, int]] = []
        self.mark_price_ticks: int = 0

        # Isolated position margin tracking per §14.4
        self.position_lots: int = 0  # + for Long, - for Short
        self.entry_price_ticks: int = 0
        self.isolated_collateral_ticks: Decimal = isolated_collateral_ticks
        self.cumulative_funding_ticks: Decimal = Decimal("0")
        self.maintenance_margin_rate: Decimal = maintenance_margin_rate
        self.is_liquidated: bool = False

    @property
    def best_bid_ticks(self) -> int:
        return self.bids[0][0] if self.bids else 0

    @property
    def best_ask_ticks(self) -> int:
        return self.asks[0][0] if self.asks else 0

    @property
    def best_bid_lots(self) -> int:
        return self.bids[0][1] if self.bids else 0

    @property
    def best_ask_lots(self) -> int:
        return self.asks[0][1] if self.asks else 0

    def on_market(self, event: Envelope) -> None:
        """Process incoming market truth events and maintain matching state."""
        payload_dict: dict[str, Any] = {}
        if isinstance(event.payload, bytes):
            try:
                decoded = json.loads(event.payload.decode("utf-8"))
                if isinstance(decoded, dict):
                    payload_dict = decoded
            except Exception:
                payload_dict = {}
        elif isinstance(event.payload, dict):
            payload_dict = event.payload
        elif hasattr(event.payload, "__dataclass_fields__"):
            payload_dict = {
                f: getattr(event.payload, f) for f in event.payload.__dataclass_fields__
            }

        if event.event_type in ("BookSnapshot", "BookUpdate"):
            raw_bids = payload_dict.get("bids", [])
            raw_asks = payload_dict.get("asks", [])
            self.bids = [(int(p), int(s)) for p, s in raw_bids]
            self.asks = [(int(p), int(s)) for p, s in raw_asks]
            is_snapshot = event.event_type == "BookSnapshot"

            self.liquidity.apply_book_update(self.bids, self.asks, snapshot=is_snapshot)

            if self.bids and self.asks:
                self.mark_price_ticks = (self.bids[0][0] + self.asks[0][0]) // 2
            elif self.bids:
                self.mark_price_ticks = self.bids[0][0]
            elif self.asks:
                self.mark_price_ticks = self.asks[0][0]

            self._check_margin_breach()

            self.timeline.schedule(
                self.latency.public_feed_delivery_ns,
                lambda: self._deliver_local(event),
            )

        elif event.event_type == "Quote":
            bid_p = int(payload_dict.get("bid_price_ticks", payload_dict.get("bid_price", 0)))
            bid_s = int(payload_dict.get("bid_size_lots", payload_dict.get("bid_size", 0)))
            ask_p = int(payload_dict.get("ask_price_ticks", payload_dict.get("ask_price", 0)))
            ask_s = int(payload_dict.get("ask_size_lots", payload_dict.get("ask_size", 0)))
            self.bids = [(bid_p, bid_s)] if bid_p > 0 else []
            self.asks = [(ask_p, ask_s)] if ask_p > 0 else []
            self.liquidity.apply_book_update(self.bids, self.asks, snapshot=True)
            if self.bids and self.asks:
                self.mark_price_ticks = (self.bids[0][0] + self.asks[0][0]) // 2
            elif self.bids:
                self.mark_price_ticks = self.bids[0][0]
            elif self.asks:
                self.mark_price_ticks = self.asks[0][0]
            self._check_margin_breach()
            self.timeline.schedule(
                self.latency.public_feed_delivery_ns,
                lambda: self._deliver_local(event),
            )

        elif event.event_type == "BarClosed":
            close_ticks = int(payload_dict.get("close_ticks", payload_dict.get("close", 0)))
            if close_ticks > 0:
                self.mark_price_ticks = close_ticks
                # Conservative synthetic top-of-book for bar-only execution
                half_spread = 1
                self.bids = [(close_ticks - half_spread, 1000)]
                self.asks = [(close_ticks + half_spread, 1000)]
                self.liquidity.apply_book_update(self.bids, self.asks, snapshot=True)
            self._check_margin_breach()
            self.timeline.schedule(
                self.latency.public_feed_delivery_ns,
                lambda: self._deliver_local(event),
            )

        elif event.event_type == "Trade":
            trade_price = int(payload_dict.get("price_ticks", payload_dict.get("price", 0)))
            trade_lots = int(payload_dict.get("lots", payload_dict.get("size_lots", 0)))
            aggressor = payload_dict.get("aggressor", payload_dict.get("aggressor_side", "BUY"))
            is_buy = aggressor == "BUY"

            if trade_price > 0:
                self.mark_price_ticks = trade_price

            fills = self.queue.apply_trade(is_buy, trade_price, trade_lots)
            for order_id, filled_lots in fills.items():
                self._generate_fill(order_id, filled_lots, trade_price, is_taker=False)
                # Passive order side is opposite to trade aggressor
                is_order_buy = not is_buy
                self._update_position(is_order_buy, filled_lots, trade_price)

            self._check_margin_breach()

            self.timeline.schedule(
                self.latency.public_feed_delivery_ns,
                lambda: self._deliver_local(event),
            )

        elif event.event_type == "FundingRate":
            rate_raw = payload_dict.get("funding_rate", 0)
            funding_rate = Decimal(str(rate_raw))
            if self.position_lots != 0 and self.mark_price_ticks > 0:
                payment = calculate_funding_payment(
                    Decimal(self.position_lots),
                    Decimal(self.mark_price_ticks),
                    funding_rate,
                )
                self.cumulative_funding_ticks += payment

                funding_event = self._make_envelope(
                    event_type="FundingPayment",
                    payload={
                        "funding_payment": str(payment),
                        "funding_rate": str(funding_rate),
                        "position_lots": self.position_lots,
                        "mark_price_ticks": self.mark_price_ticks,
                    },
                    correlation_id="funding",
                )
                self._deliver_local(funding_event)
                self._check_margin_breach()

    def submit(self, instruction: dict[str, Any]) -> None:
        """Process outbound order instructions with realistic latency and fences."""
        if self.is_liquidated:
            order_id = str(instruction.get("intent_id") or instruction.get("order_id", ""))
            self._generate_reject(order_id, "ACCOUNT_LIQUIDATED")
            return

        action = instruction.get("action")
        if action in ("ENTER", "EXIT"):
            self.timeline.schedule(
                self.latency.submit_outbound_ns + self.latency.venue_handling_ns,
                lambda: self._handle_new_order(instruction),
            )
        elif action == "CANCEL":
            self.timeline.schedule(
                self.latency.cancel_outbound_ns + self.latency.cancel_handling_ns,
                lambda: self._handle_cancel(instruction),
            )

    def _handle_new_order(self, instruction: dict[str, Any]) -> None:
        order_id = str(instruction.get("intent_id") or instruction.get("order_id", ""))
        side_val = instruction.get("side")
        is_buy = side_val in ("BUY", "Side.BUY") or getattr(side_val, "value", None) == "BUY"
        qty_lots = int(instruction.get("desired_quantity") or instruction.get("lots", 0))
        price_policy = instruction.get("price_policy", "MARKET")
        tif = instruction.get("time_in_force", "GTC")
        limit_price = instruction.get("price_ticks")
        if limit_price is not None:
            limit_price = int(limit_price)

        # Available virtual collateral check (§14.4)
        est_price = (
            limit_price or self.mark_price_ticks or self.best_ask_ticks or self.best_bid_ticks
        )
        if est_price > 0 and qty_lots > 0:
            notional = Decimal(qty_lots * est_price)
            required_margin = notional * self.maintenance_margin_rate
            equity = self._calculate_margin_equity()
            if required_margin > equity:
                self._generate_reject(order_id, "INSUFFICIENT_MARGIN")
                return

        # 1. Post-Only Cross Rejection (§14.2)
        if price_policy in ("POST_ONLY", "BEST_SAME_SIDE") or tif == "POST_ONLY":
            if (
                is_buy
                and self.best_ask_ticks > 0
                and limit_price
                and limit_price >= self.best_ask_ticks
            ):
                self._generate_reject(order_id, "POST_ONLY_RESTING_CROSS_REJECTED")
                return
            if (
                not is_buy
                and self.best_bid_ticks > 0
                and limit_price
                and limit_price <= self.best_bid_ticks
            ):
                self._generate_reject(order_id, "POST_ONLY_RESTING_CROSS_REJECTED")
                return

        # 2. Aggressive Execution with Depth Walking (§14.2)
        is_aggressive = (
            price_policy == "MARKET"
            or (
                is_buy
                and limit_price
                and self.best_ask_ticks > 0
                and limit_price >= self.best_ask_ticks
            )
            or (
                not is_buy
                and limit_price
                and self.best_bid_ticks > 0
                and limit_price <= self.best_bid_ticks
            )
        )

        if is_aggressive:
            opposite_book = self.asks if is_buy else self.bids
            # Calculate executable volume up to limit price
            available_depth: list[tuple[int, int]] = []
            for level_p, level_s in opposite_book:
                if limit_price is not None:
                    if is_buy and level_p > limit_price:
                        break
                    if not is_buy and level_p < limit_price:
                        break
                available_depth.append((level_p, level_s))

            total_executable = sum(s for _, s in available_depth)

            # FOK Check (§14.2)
            if tif == "FOK" and total_executable < qty_lots:
                self._generate_reject(order_id, "FOK_INSUFFICIENT_LIQUIDITY")
                return

            # Walk book levels and generate partial executions
            remaining = qty_lots
            for level_p, level_s in available_depth:
                if remaining <= 0:
                    break
                fillable = self.liquidity.try_consume(is_buy, level_p, remaining, level_s)
                if fillable > 0:
                    self._generate_fill(order_id, fillable, level_p, is_taker=True)
                    self._update_position(is_buy, fillable, level_p)
                    remaining -= fillable

            if remaining > 0:
                if tif == "IOC" or price_policy == "MARKET":
                    self._generate_cancel(order_id, remaining, "IOC_EXPIRED")
                else:
                    # Resting remainder joins passive queue
                    rest_price = (
                        limit_price or (self.best_bid_ticks if is_buy else self.best_ask_ticks)
                    )
                    displayed = self.best_bid_lots if is_buy else self.best_ask_lots
                    self.queue.add_order(order_id, is_buy, rest_price, remaining, displayed)
                    self._generate_ack(order_id)
            return

        # 3. Passive Limit Order Execution (§14.3)
        rest_price = limit_price or (self.best_bid_ticks if is_buy else self.best_ask_ticks)
        displayed = self.best_bid_lots if is_buy else self.best_ask_lots
        self.queue.add_order(order_id, is_buy, rest_price, qty_lots, displayed)
        self._generate_ack(order_id)

    def _handle_cancel(self, instruction: dict[str, Any]) -> None:
        order_id = str(instruction.get("order_id") or instruction.get("intent_id", ""))
        canceled_lots = self.queue.cancel_order(order_id)
        self._generate_cancel(order_id, canceled_lots, "USER_REQUEST")

    def _update_position(self, is_buy: bool, filled_lots: int, price_ticks: int) -> None:
        delta = filled_lots if is_buy else -filled_lots
        new_pos = self.position_lots + delta

        if (self.position_lots >= 0 and delta > 0) or (self.position_lots <= 0 and delta < 0):
            # Increasing position size: update average entry price
            old_cost = abs(self.position_lots) * self.entry_price_ticks
            new_cost = filled_lots * price_ticks
            self.entry_price_ticks = (old_cost + new_cost) // abs(new_pos) if new_pos != 0 else 0
        self.position_lots = new_pos
        self._check_margin_breach()

    def _calculate_margin_equity(self) -> Decimal:
        pos_lots_abs = abs(self.position_lots)
        pnl = 0
        if self.position_lots != 0 and self.mark_price_ticks > 0 and self.entry_price_ticks > 0:
            pnl = (
                (self.mark_price_ticks - self.entry_price_ticks) * self.position_lots
                if self.position_lots > 0
                else (self.entry_price_ticks - self.mark_price_ticks) * pos_lots_abs
            )
        return self.isolated_collateral_ticks + Decimal(pnl) - self.cumulative_funding_ticks

    def _check_margin_breach(self) -> None:
        """Check margin equity against maintenance requirement per §14.4."""
        if self.position_lots == 0 or self.mark_price_ticks == 0 or self.is_liquidated:
            return

        pos_lots_abs = abs(self.position_lots)
        margin_equity = self._calculate_margin_equity()

        notional = Decimal(pos_lots_abs * self.mark_price_ticks)
        maint_margin = notional * self.maintenance_margin_rate
        close_fee = notional * self.fee_profile.taker_rate

        if margin_equity <= (maint_margin + close_fee):
            self.is_liquidated = True
            # Cancel all pending resting orders
            self.queue.orders.clear()

            liq_event = self._make_envelope(
                event_type="SimulatedLiquidationTriggered",
                payload={
                    "position_lots": self.position_lots,
                    "mark_price_ticks": self.mark_price_ticks,
                    "margin_equity": str(margin_equity),
                    "maintenance_margin": str(maint_margin),
                },
                correlation_id="liquidation",
            )
            self._deliver_local(liq_event)

            # Forced depth reduction (§14.4)
            is_liq_buy = self.position_lots < 0
            opposite_book = self.asks if is_liq_buy else self.bids
            lots_to_liquidate = pos_lots_abs

            for level_p, level_s in opposite_book:
                if lots_to_liquidate <= 0:
                    break
                fillable = self.liquidity.try_consume(
                    is_liq_buy, level_p, lots_to_liquidate, level_s
                )
                if fillable > 0:
                    self._generate_fill("LIQUIDATION", fillable, level_p, is_taker=True)
                    lots_to_liquidate -= fillable

            self.position_lots = 0

    def _generate_ack(self, order_id: str) -> None:
        ack_event = self._make_envelope(
            event_type="OrderAck",
            payload={"order_id": order_id},
            correlation_id=f"ack-{order_id}",
        )
        self.timeline.schedule(
            self.latency.private_ack_ns,
            lambda: self._deliver_local(ack_event),
        )

    def _generate_fill(
        self, order_id: str, filled_lots: int, price_ticks: int, is_taker: bool
    ) -> None:
        notional = Decimal(price_ticks * filled_lots)
        fee = calculate_fee(notional, not is_taker, self.fee_profile)

        fill_event = self._make_envelope(
            event_type="OrderFill",
            payload={
                "order_id": order_id,
                "filled_lots": filled_lots,
                "price_ticks": price_ticks,
                "is_taker": is_taker,
                "fee": str(fee),
                "fee_currency": "USDT",
            },
            correlation_id=f"fill-{order_id}",
        )
        self.timeline.schedule(
            self.latency.fill_report_delivery_ns,
            lambda: self._deliver_local(fill_event),
        )

    def _generate_cancel(self, order_id: str, canceled_lots: int, reason: str) -> None:
        cancel_event = self._make_envelope(
            event_type="OrderCanceled",
            payload={
                "order_id": order_id,
                "canceled_lots": canceled_lots,
                "reason": reason,
            },
            correlation_id=f"cancel-{order_id}",
        )
        self.timeline.schedule(
            self.latency.private_ack_ns,
            lambda: self._deliver_local(cancel_event),
        )

    def _generate_reject(self, order_id: str, reason: str) -> None:
        reject_event = self._make_envelope(
            event_type="OrderRejected",
            payload={"order_id": order_id, "reason": reason},
            correlation_id=f"reject-{order_id}",
        )
        self.timeline.schedule(
            self.latency.private_ack_ns,
            lambda: self._deliver_local(reject_event),
        )

    def _make_envelope(
        self, event_type: str, payload: dict[str, Any], correlation_id: str
    ) -> Envelope:
        return Envelope(
            event_type=event_type,
            schema_version=1,
            run_id="sim",
            account_id=None,
            venue="SIM",
            environment="SIM",
            instrument_id="SIM",
            source_channel="SIM",
            connection_epoch="sim-1",
            source_message_id=None,
            source_sequence=None,
            exchange_event_ns=self.timeline.current_time_ns,
            exchange_transaction_ns=None,
            receive_wall_ns=self.timeline.current_time_ns,
            receive_monotonic_ns=self.timeline.current_time_ns,
            available_ns=self.timeline.current_time_ns,
            causation_id=None,
            correlation_id=correlation_id,
            raw_ref=None,
            producer_version="v1",
            payload=json.dumps(payload).encode("utf-8"),
            event_id=f"{event_type.lower()}-{self.timeline._seq}",
            engine_seq=0,
        )

    def _deliver_local(self, event: Envelope) -> None:
        self.outbox.append(event)

    def advance(self, until_ns: int) -> tuple[Envelope, ...]:
        self.timeline.advance_to(until_ns)
        out = tuple(self.outbox)
        self.outbox.clear()
        return out

    def on_end_of_data(self, forced_close: bool = False) -> list[Envelope]:
        """Handle end of dataset per §14 and Task 10 requirements:

        - Cancel hypothetical pending entries.
        - Mark remaining positions to the last valid mark price.
        - If forced_close is requested (separately labeled scenario), execute
          against available opposite book depth; do not fabricate an invisible favorable
          fill if depth is insufficient.
        """
        events: list[Envelope] = []
        # 1. Cancel pending resting entries
        for order_id, order in list(self.queue.orders.items()):
            canceled_lots = order.own_remaining_lots
            cancel_event = self._make_envelope(
                event_type="OrderCanceled",
                payload={
                    "order_id": order_id,
                    "canceled_lots": canceled_lots,
                    "reason": "END_OF_DATA",
                },
                correlation_id=f"end_cancel-{order_id}",
            )
            events.append(cancel_event)
        self.queue.orders.clear()

        # 2. Position handling
        if self.position_lots != 0:
            if forced_close:
                is_close_buy = self.position_lots < 0
                opposite_book = self.asks if is_close_buy else self.bids
                lots_to_close = abs(self.position_lots)

                for level_p, level_s in opposite_book:
                    if lots_to_close <= 0:
                        break
                    fillable = self.liquidity.try_consume(
                        is_close_buy, level_p, lots_to_close, level_s
                    )
                    if fillable > 0:
                        notional = Decimal(level_p * fillable)
                        fee = calculate_fee(notional, False, self.fee_profile)
                        fill_event = self._make_envelope(
                            event_type="OrderFill",
                            payload={
                                "order_id": "FORCED_CLOSE_END_OF_DATA",
                                "filled_lots": fillable,
                                "price_ticks": level_p,
                                "is_taker": True,
                                "fee": str(fee),
                                "fee_currency": "USDT",
                            },
                            correlation_id="forced_close_end_of_data",
                        )
                        events.append(fill_event)
                        self._update_position(is_close_buy, fillable, level_p)
                        lots_to_close -= fillable

                if lots_to_close > 0:
                    # Surfaced insufficient depth / unclosed residual
                    unclosed_event = self._make_envelope(
                        event_type="SimulatedResidualExposure",
                        payload={
                            "remaining_unclosed_lots": lots_to_close,
                            "reason": "INSUFFICIENT_DEPTH_AT_END_OF_DATA",
                        },
                        correlation_id="residual_end_of_data",
                    )
                    events.append(unclosed_event)
            else:
                # Mark to last valid mark price without forced fill
                pnl = (
                    (self.mark_price_ticks - self.entry_price_ticks) * self.position_lots
                    if self.position_lots > 0
                    else (self.entry_price_ticks - self.mark_price_ticks) * abs(self.position_lots)
                )
                mark_event = self._make_envelope(
                    event_type="PositionMarkedToEnd",
                    payload={
                        "position_lots": self.position_lots,
                        "entry_price_ticks": self.entry_price_ticks,
                        "mark_price_ticks": self.mark_price_ticks,
                        "unrealized_pnl_ticks": str(pnl),
                    },
                    correlation_id="mark_to_end",
                )
                events.append(mark_event)

        return events
