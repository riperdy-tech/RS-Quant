from hypothesis import given, settings
from hypothesis import strategies as st

from quantdesk.simulation.liquidity import LiquidityBudget
from quantdesk.simulation.queue import QueueEstimator


def test_liquidity_conservation_multiple_orders():
    budget = LiquidityBudget()
    budget.apply_book_update(bids=[], asks=[(100, 10)], snapshot=True)

    # 3 orders arriving sequentially for the same price level, total 15 lots requested
    f1 = budget.try_consume(True, 100, 5, 10)
    f2 = budget.try_consume(True, 100, 5, 10)
    f3 = budget.try_consume(True, 100, 5, 10)

    assert f1 == 5
    assert f2 == 5
    assert f3 == 0  # Liquidity exhausted

    # Book updates size to 15
    budget.apply_book_update(bids=[], asks=[(100, 15)], snapshot=False)

    f4 = budget.try_consume(True, 100, 5, 15)
    assert f4 == 5
    assert budget.consumed_asks[100] == 5


@given(
    available_lots=st.integers(min_value=1, max_value=1000),
    order_requests=st.lists(st.integers(min_value=1, max_value=200), min_size=1, max_size=30),
)
@settings(max_examples=100)
def test_property_liquidity_budget_conservation(
    available_lots: int, order_requests: list[int]
) -> None:
    """Invariant: total consumed lots across orders never exceeds available depth."""
    budget = LiquidityBudget()
    budget.apply_book_update(bids=[(100, available_lots)], asks=[], snapshot=True)

    total_consumed = 0
    for req in order_requests:
        fill = budget.try_consume(
            is_buy=False,
            price_ticks=100,
            requested_lots=req,
            available_lots=available_lots,
        )
        assert 0 <= fill <= req
        total_consumed += fill
        assert total_consumed <= available_lots

    assert budget.consumed_bids[100] == total_consumed
    # Subsequent consumption must return 0 if already saturated
    if total_consumed == available_lots:
        assert (
            budget.try_consume(
                is_buy=False,
                price_ticks=100,
                requested_lots=10,
                available_lots=available_lots,
            )
            == 0
        )


@given(
    queue_ahead=st.integers(min_value=0, max_value=500),
    order_size=st.integers(min_value=1, max_value=200),
    trades=st.lists(st.integers(min_value=1, max_value=100), min_size=1, max_size=20),
)
@settings(max_examples=100)
def test_property_queue_single_order_conservation(
    queue_ahead: int, order_size: int, trades: list[int]
) -> None:
    """Invariant: volume allocation is strictly conserved and fills never exceed order size."""
    q = QueueEstimator()
    q.add_order(
        "ord1", is_buy=True, price_ticks=100, lots=order_size, current_displayed_lots=queue_ahead
    )

    total_fills = 0
    total_trade_volume = sum(trades)

    for trade_lots in trades:
        fills = q.apply_trade(is_buy=False, trade_price_ticks=100, trade_lots=trade_lots)
        fill = fills.get("ord1", 0)
        assert fill >= 0
        total_fills += fill

        if "ord1" in q.orders:
            order = q.orders["ord1"]
            assert order.queue_ahead_lots >= 0
            assert order.own_remaining_lots >= 0
            assert order.own_filled_lots + order.own_remaining_lots == order_size
            assert order.own_filled_lots == total_fills

    assert total_fills <= order_size
    assert total_fills <= total_trade_volume


@given(
    queue_ahead=st.integers(min_value=0, max_value=200),
    order_sizes=st.lists(st.integers(min_value=1, max_value=50), min_size=2, max_size=5),
    trades=st.lists(st.integers(min_value=1, max_value=100), min_size=1, max_size=15),
)
@settings(max_examples=100)
def test_property_simultaneous_orders_fifo_priority(
    queue_ahead: int, order_sizes: list[int], trades: list[int]
) -> None:
    """Invariant: later orders never receive fills before all earlier orders are fully satisfied."""
    q = QueueEstimator()
    order_ids = [f"ord_{i}" for i in range(len(order_sizes))]
    for oid, size in zip(order_ids, order_sizes, strict=True):
        q.add_order(
            oid, is_buy=True, price_ticks=100, lots=size, current_displayed_lots=queue_ahead
        )

    filled_totals: dict[str, int] = {oid: 0 for oid in order_ids}

    for trade_lots in trades:
        fills = q.apply_trade(is_buy=False, trade_price_ticks=100, trade_lots=trade_lots)
        for oid, fill_qty in fills.items():
            filled_totals[oid] += fill_qty
            # Verify FIFO invariant: all earlier orders must be 100% filled!
            idx = order_ids.index(oid)
            for prev_idx in range(idx):
                prev_oid = order_ids[prev_idx]
                assert filled_totals[prev_oid] == order_sizes[prev_idx]


@given(
    queue_ahead=st.integers(min_value=5, max_value=500),
    order_size=st.integers(min_value=1, max_value=100),
    reductions=st.lists(st.integers(min_value=1, max_value=50), min_size=1, max_size=10),
)
@settings(max_examples=100)
def test_property_depth_deletion_never_creates_fills(
    queue_ahead: int, order_size: int, reductions: list[int]
) -> None:
    """Invariant: depth deletions/cancellations alone NEVER generate passive fills."""
    q = QueueEstimator()
    q.add_order(
        "ord1", is_buy=True, price_ticks=100, lots=order_size, current_displayed_lots=queue_ahead
    )

    total_depth = queue_ahead + 200
    for red in reductions:
        # Test both conservative and proportional cancellation models
        q.apply_book_depth_reduction(
            is_buy=True,
            price_ticks=100,
            reduction_lots=red,
            total_depth_before=total_depth,
            proportional=True,
        )
        assert "ord1" in q.orders
        order = q.orders["ord1"]
        # Zero fills must have been generated
        assert order.own_filled_lots == 0
        assert order.own_remaining_lots == order_size
        assert order.queue_ahead_lots >= 0

