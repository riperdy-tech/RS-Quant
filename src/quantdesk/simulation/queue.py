from __future__ import annotations

from dataclasses import dataclass


@dataclass
class QueueState:
    order_id: str
    price_ticks: int
    is_buy: bool
    queue_ahead_lots: int
    own_remaining_lots: int
    own_filled_lots: int = 0


class QueueEstimator:
    def __init__(self) -> None:
        self.orders: dict[str, QueueState] = {}

    def add_order(
        self,
        order_id: str,
        is_buy: bool,
        price_ticks: int,
        lots: int,
        current_displayed_lots: int,
    ) -> None:
        simulated_ahead = sum(
            o.own_remaining_lots
            for o in self.orders.values()
            if o.is_buy == is_buy and o.price_ticks == price_ticks
        )
        self.orders[order_id] = QueueState(
            order_id=order_id,
            price_ticks=price_ticks,
            is_buy=is_buy,
            queue_ahead_lots=current_displayed_lots + simulated_ahead,
            own_remaining_lots=lots,
        )

    def cancel_order(self, order_id: str) -> int:
        if order_id in self.orders:
            order = self.orders.pop(order_id)
            return order.own_remaining_lots
        return 0

    def apply_trade(
        self, is_buy: bool, trade_price_ticks: int, trade_lots: int
    ) -> dict[str, int]:
        """Applies a public trade.

        If the trade matches our side/price, it consumes queue.
        Returns a dict of order_id -> newly filled lots for this trade event.
        """
        passive_buy = not is_buy
        fills: dict[str, int] = {}

        for order_id, order in list(self.orders.items()):
            if order.is_buy == passive_buy and order.price_ticks == trade_price_ticks:
                if trade_lots <= order.queue_ahead_lots:
                    order.queue_ahead_lots -= trade_lots
                else:
                    trade_reaching_order = trade_lots - order.queue_ahead_lots
                    order.queue_ahead_lots = 0
                    fill = min(order.own_remaining_lots, trade_reaching_order)
                    if fill > 0:
                        order.own_remaining_lots -= fill
                        order.own_filled_lots += fill
                        fills[order_id] = fill
                    if order.own_remaining_lots == 0:
                        del self.orders[order_id]

        return fills

    def apply_book_depth_reduction(
        self,
        is_buy: bool,
        price_ticks: int,
        reduction_lots: int,
        total_depth_before: int,
        proportional: bool = False,
    ) -> None:
        """Apply depth reduction (e.g. cancellations in book).

        Under conservative model (proportional=False), cancellations do not improve queue-ahead.
        Under proportional model (proportional=True), reduction is allocated proportionally
        to queue ahead without ever generating fills.
        """
        if not proportional or reduction_lots <= 0 or total_depth_before <= 0:
            return

        for order in self.orders.values():
            if (
                order.is_buy == is_buy
                and order.price_ticks == price_ticks
                and order.queue_ahead_lots > 0
            ):
                fraction = min(1.0, order.queue_ahead_lots / total_depth_before)
                reduce_ahead = round(reduction_lots * fraction)
                order.queue_ahead_lots = max(0, order.queue_ahead_lots - reduce_ahead)
