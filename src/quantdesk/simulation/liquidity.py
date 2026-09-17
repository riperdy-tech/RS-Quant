class LiquidityBudget:
    def __init__(self) -> None:
        self.consumed_bids: dict[int, int] = {}  # price_ticks -> consumed lots
        self.consumed_asks: dict[int, int] = {}  # price_ticks -> consumed lots

    def apply_book_update(
        self,
        bids: list[tuple[int, int]],
        asks: list[tuple[int, int]],
        snapshot: bool = False,
    ) -> None:
        if snapshot:
            self.consumed_bids.clear()
            self.consumed_asks.clear()
        else:
            # If a price level is updated explicitly, we release the conservative hold
            for price, _size in bids:
                if price in self.consumed_bids:
                    del self.consumed_bids[price]
            for price, _size in asks:
                if price in self.consumed_asks:
                    del self.consumed_asks[price]

    def try_consume(
        self,
        is_buy: bool,
        price_ticks: int,
        requested_lots: int,
        available_lots: int,
    ) -> int:
        consumed_dict = self.consumed_asks if is_buy else self.consumed_bids
        already_consumed = consumed_dict.get(price_ticks, 0)

        remaining_budget = max(0, available_lots - already_consumed)
        fillable = min(requested_lots, remaining_budget)

        if fillable > 0:
            consumed_dict[price_ticks] = already_consumed + fillable

        return fillable
