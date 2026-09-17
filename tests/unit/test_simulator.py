import json
from decimal import Decimal

from quantdesk.core.events import Envelope
from quantdesk.simulation.fees import FeeProfile, calculate_fee
from quantdesk.simulation.funding import calculate_funding_payment
from quantdesk.simulation.latency import LatencyProfile
from quantdesk.simulation.liquidity import LiquidityBudget
from quantdesk.simulation.queue import QueueEstimator
from quantdesk.simulation.scheduler import VirtualTimeline
from quantdesk.simulation.venue import SimVenue


def test_scheduler_ordering_and_delay():
    tl = VirtualTimeline()
    executed = []
    tl.schedule(100, lambda: executed.append(1))
    tl.schedule(50, lambda: executed.append(2))
    tl.schedule_absolute(75, lambda: executed.append(3))

    tl.advance_to(49)
    assert not executed
    tl.advance_to(50)
    assert executed == [2]
    tl.advance_to(75)
    assert executed == [2, 3]
    tl.advance_to(100)
    assert executed == [2, 3, 1]


def test_liquidity_budget_exhaustion_and_reset():
    budget = LiquidityBudget()
    budget.apply_book_update(bids=[(100, 10)], asks=[(102, 10)], snapshot=True)

    # First consume 5 of 10 available asks
    f1 = budget.try_consume(True, 102, 5, 10)
    assert f1 == 5

    # Second consume attempts 6 lots; only 5 remaining budget
    f2 = budget.try_consume(True, 102, 6, 10)
    assert f2 == 5

    # Third consume gets 0 (exhausted)
    f3 = budget.try_consume(True, 102, 1, 10)
    assert f3 == 0

    # Book update with new size 20 releases conservative hold
    budget.apply_book_update(bids=[], asks=[(102, 20)], snapshot=False)
    f4 = budget.try_consume(True, 102, 12, 20)
    assert f4 == 12


def test_queue_arithmetic_by_hand():
    """Exact hand-calculated test matching §14.3:
    queue ahead 5 lots, own order 3 lots, eligible sell trade 6 lots -> own fill 1 lot, remaining 2.
    """
    q = QueueEstimator()
    q.add_order("ord1", is_buy=True, price_ticks=100, lots=3, current_displayed_lots=5)
    assert q.orders["ord1"].queue_ahead_lots == 5
    assert q.orders["ord1"].own_remaining_lots == 3

    # Sell trade of 6 lots at bid price 100
    fills = q.apply_trade(is_buy=False, trade_price_ticks=100, trade_lots=6)
    assert fills == {"ord1": 1}
    assert q.orders["ord1"].own_remaining_lots == 2
    assert q.orders["ord1"].queue_ahead_lots == 0


def test_queue_simultaneous_own_orders_fifo():
    """Two own orders at the same price level follow strict FIFO priority."""
    q = QueueEstimator()
    q.add_order("ord1", is_buy=True, price_ticks=100, lots=3, current_displayed_lots=5)
    # ord2 joins after ord1: queue ahead = 5 external + 3 ord1 = 8 lots
    q.add_order("ord2", is_buy=True, price_ticks=100, lots=4, current_displayed_lots=5)
    assert q.orders["ord2"].queue_ahead_lots == 8

    # Trade of 10 lots: 5 external, 3 ord1 (fills completely), 2 ord2
    fills = q.apply_trade(is_buy=False, trade_price_ticks=100, trade_lots=10)
    assert fills == {"ord1": 3, "ord2": 2}
    assert "ord1" not in q.orders  # fully filled
    assert q.orders["ord2"].own_remaining_lots == 2
    assert q.orders["ord2"].queue_ahead_lots == 0


def test_queue_no_fill_on_touch_alone():
    """Touching price or non-matching trade aggressor does not fill passive order."""
    q = QueueEstimator()
    q.add_order("ord1", is_buy=True, price_ticks=100, lots=5, current_displayed_lots=2)

    # Trade at adjacent price 101 does not fill bid at 100
    f1 = q.apply_trade(is_buy=False, trade_price_ticks=101, trade_lots=10)
    assert not f1
    assert q.orders["ord1"].own_remaining_lots == 5

    # BUY aggressor trade at 100 (matches ask, not bid) does not fill bid
    f2 = q.apply_trade(is_buy=True, trade_price_ticks=100, trade_lots=10)
    assert not f2
    assert q.orders["ord1"].own_remaining_lots == 5


def test_queue_proportional_cancel_scenario():
    """Depth reduction in proportional scenario reduces queue-ahead without creating fills."""
    q = QueueEstimator()
    q.add_order("ord1", is_buy=True, price_ticks=100, lots=5, current_displayed_lots=10)
    assert q.orders["ord1"].queue_ahead_lots == 10

    # Conservative model: cancellations do NOT improve queue ahead
    q.apply_book_depth_reduction(
        is_buy=True, price_ticks=100, reduction_lots=5, total_depth_before=20, proportional=False
    )
    assert q.orders["ord1"].queue_ahead_lots == 10

    # Proportional model: cancellations reduce ahead proportionally
    q.apply_book_depth_reduction(
        is_buy=True, price_ticks=100, reduction_lots=5, total_depth_before=20, proportional=True
    )
    assert q.orders["ord1"].queue_ahead_lots < 10
    # Crucially, remaining lots are unchanged; no fills created
    assert q.orders["ord1"].own_remaining_lots == 5
    assert q.orders["ord1"].own_filled_lots == 0


def test_fee_calculation():
    fp = FeeProfile(maker_rate=Decimal("0.0002"), taker_rate=Decimal("0.00055"))
    notional = Decimal("10000")
    maker_fee = calculate_fee(notional, True, fp)
    taker_fee = calculate_fee(notional, False, fp)
    assert maker_fee == Decimal("2.0000")
    assert taker_fee == Decimal("5.5000")


def test_funding_payment():
    # Long position (qty 2), mark 1000, funding rate +0.0001 -> longs pay shorts: -0.2
    p1 = calculate_funding_payment(Decimal("2"), Decimal("1000"), Decimal("0.0001"))
    assert p1 == Decimal("-0.20000")

    # Short position (qty -2), mark 1000, funding rate +0.0001 -> shorts receive: +0.2
    p2 = calculate_funding_payment(Decimal("-2"), Decimal("1000"), Decimal("0.0001"))
    assert p2 == Decimal("0.20000")


def _make_book_envelope(bids: list[tuple[int, int]], asks: list[tuple[int, int]]) -> Envelope:
    return Envelope(
        event_type="BookSnapshot",
        schema_version=1,
        run_id="run-1",
        account_id=None,
        venue="SIM",
        environment="SIM",
        instrument_id="BTC-USDT",
        source_channel="FEED",
        connection_epoch="epoch-1",
        source_message_id=None,
        source_sequence=None,
        exchange_event_ns=0,
        exchange_transaction_ns=None,
        receive_wall_ns=0,
        receive_monotonic_ns=0,
        available_ns=0,
        causation_id=None,
        correlation_id="corr-1",
        raw_ref=None,
        producer_version="v1",
        payload=json.dumps({"bids": bids, "asks": asks}).encode("utf-8"),
        event_id="e-book",
        engine_seq=0,
    )


def test_sim_venue_aggressive_depth_walking():
    tl = VirtualTimeline()
    lp = LatencyProfile.synthetic_defaults()
    fp = FeeProfile()
    venue = SimVenue(tl, lp, fp)

    # Initial book: Ask level 1: (101, 4), Ask level 2: (102, 6)
    venue.on_market(_make_book_envelope(bids=[(99, 10)], asks=[(101, 4), (102, 6)]))

    # Aggressive BUY of 7 lots at MARKET (or limit 105)
    venue.submit({
        "action": "ENTER",
        "intent_id": "ord_agg",
        "side": "BUY",
        "desired_quantity": 7,
        "price_policy": "MARKET",
    })

    # Advance past submit + venue handling + fill delivery
    total_time = lp.submit_outbound_ns + lp.venue_handling_ns + lp.fill_report_delivery_ns + 1000
    events = venue.advance(total_time)

    fill_events = [
        json.loads(e.payload.decode("utf-8")) for e in events if e.event_type == "OrderFill"
    ]
    assert len(fill_events) == 2
    # Level 1 fill: 4 lots at 101
    assert fill_events[0]["filled_lots"] == 4
    assert fill_events[0]["price_ticks"] == 101
    assert fill_events[0]["is_taker"] is True
    # Level 2 fill: 3 lots at 102
    assert fill_events[1]["filled_lots"] == 3
    assert fill_events[1]["price_ticks"] == 102
    assert fill_events[1]["is_taker"] is True
    assert venue.position_lots == 7


def test_sim_venue_tif_ioc_and_fok():
    tl = VirtualTimeline()
    lp = LatencyProfile.synthetic_defaults()
    fp = FeeProfile()
    venue = SimVenue(tl, lp, fp)

    # Book with 5 lots at 101
    venue.on_market(_make_book_envelope(bids=[(99, 10)], asks=[(101, 5)]))

    # 1. FOK order for 8 lots when only 5 exist -> Rejected
    venue.submit({
        "action": "ENTER",
        "intent_id": "ord_fok",
        "side": "BUY",
        "desired_quantity": 8,
        "price_policy": "LIMIT",
        "price_ticks": 101,
        "time_in_force": "FOK",
    })
    events = venue.advance(
        lp.submit_outbound_ns + lp.venue_handling_ns + lp.private_ack_ns + 1000
    )
    rejects = [
        json.loads(e.payload.decode("utf-8")) for e in events if e.event_type == "OrderRejected"
    ]
    assert len(rejects) == 1
    assert rejects[0]["reason"] == "FOK_INSUFFICIENT_LIQUIDITY"

    # 2. IOC order for 8 lots when only 5 exist -> Fills 5, cancels remaining 3
    venue.submit({
        "action": "ENTER",
        "intent_id": "ord_ioc",
        "side": "BUY",
        "desired_quantity": 8,
        "price_policy": "LIMIT",
        "price_ticks": 101,
        "time_in_force": "IOC",
    })
    events2 = venue.advance(
        tl.current_time_ns
        + lp.submit_outbound_ns
        + lp.venue_handling_ns
        + lp.fill_report_delivery_ns
        + 1000
    )
    fills = [json.loads(e.payload.decode("utf-8")) for e in events2 if e.event_type == "OrderFill"]
    cancels = [
        json.loads(e.payload.decode("utf-8")) for e in events2 if e.event_type == "OrderCanceled"
    ]
    assert len(fills) == 1
    assert fills[0]["filled_lots"] == 5
    assert len(cancels) == 1
    assert cancels[0]["canceled_lots"] == 3
    assert cancels[0]["reason"] == "IOC_EXPIRED"


def test_sim_venue_post_only_cross_rejection():
    tl = VirtualTimeline()
    lp = LatencyProfile.synthetic_defaults()
    venue = SimVenue(tl, lp, FeeProfile())

    # Book has asks at 101
    venue.on_market(_make_book_envelope(bids=[(99, 10)], asks=[(101, 10)]))

    # Post-only BUY at 101 crosses ask -> Rejected
    venue.submit({
        "action": "ENTER",
        "intent_id": "po_cross",
        "side": "BUY",
        "desired_quantity": 2,
        "price_policy": "POST_ONLY",
        "price_ticks": 101,
    })
    events = venue.advance(
        lp.submit_outbound_ns + lp.venue_handling_ns + lp.private_ack_ns + 1000
    )
    rejects = [
        json.loads(e.payload.decode("utf-8")) for e in events if e.event_type == "OrderRejected"
    ]
    assert len(rejects) == 1
    assert rejects[0]["reason"] == "POST_ONLY_RESTING_CROSS_REJECTED"


def test_sim_venue_cancel_race_fill_before_cancel():
    tl = VirtualTimeline()
    lp = LatencyProfile.synthetic_defaults()
    venue = SimVenue(tl, lp, FeeProfile())

    venue.on_market(_make_book_envelope(bids=[(100, 5)], asks=[(101, 5)]))

    # User submits passive limit order at 100 for 3 lots
    venue.submit({
        "action": "ENTER",
        "intent_id": "ord_race",
        "side": "BUY",
        "desired_quantity": 3,
        "price_ticks": 100,
        "price_policy": "LIMIT",
    })
    # Advance so order reaches venue book (ahead = 5)
    venue.advance(lp.submit_outbound_ns + lp.venue_handling_ns + 1000)

    # User submits cancel request (takes cancel_outbound + cancel_handling to reach venue)
    venue.submit({"action": "CANCEL", "intent_id": "ord_race"})

    # But BEFORE cancel arrives, a trade of 8 lots hits the bid at 100!
    # Trade consumes 5 ahead + fills all 3 lots of our order!
    trade_env = Envelope(
        event_type="Trade",
        schema_version=1,
        run_id="run-1",
        account_id=None,
        venue="SIM",
        environment="SIM",
        instrument_id="BTC-USDT",
        source_channel="FEED",
        connection_epoch="epoch-1",
        source_message_id=None,
        source_sequence=None,
        exchange_event_ns=tl.current_time_ns,
        exchange_transaction_ns=None,
        receive_wall_ns=tl.current_time_ns,
        receive_monotonic_ns=tl.current_time_ns,
        available_ns=tl.current_time_ns,
        causation_id=None,
        correlation_id="c-trade",
        raw_ref=None,
        producer_version="v1",
        payload=json.dumps({"price_ticks": 100, "lots": 8, "aggressor": "SELL"}).encode("utf-8"),
        event_id="e-trade",
        engine_seq=0,
    )
    venue.on_market(trade_env)

    # Advance so cancel reaches venue and confirmation travels back to client
    total_cancel_roundtrip = (
        tl.current_time_ns
        + lp.cancel_outbound_ns
        + lp.cancel_handling_ns
        + lp.private_ack_ns
        + 1000
    )
    events = venue.advance(total_cancel_roundtrip)
    fill_events = [e for e in events if e.event_type == "OrderFill"]
    cancel_events = [e for e in events if e.event_type == "OrderCanceled"]

    assert len(fill_events) == 1
    f_data = json.loads(fill_events[0].payload.decode("utf-8"))
    assert f_data["filled_lots"] == 3
    # When cancel arrived, 0 lots remained
    assert len(cancel_events) == 1
    c_data = json.loads(cancel_events[0].payload.decode("utf-8"))
    assert c_data["canceled_lots"] == 0


def test_sim_venue_margin_breach_liquidation():
    tl = VirtualTimeline()
    lp = LatencyProfile.synthetic_defaults()
    # Initial isolated collateral of 500 ticks
    venue = SimVenue(
        tl,
        lp,
        FeeProfile(),
        isolated_collateral_ticks=Decimal("500"),
        maintenance_margin_rate=Decimal("0.05"),
    )

    # Establish long position of 10 lots at 100 (notional = 1000)
    venue.on_market(_make_book_envelope(bids=[(99, 10)], asks=[(100, 10)]))
    venue.submit({
        "action": "ENTER",
        "intent_id": "pos_order",
        "side": "BUY",
        "desired_quantity": 10,
        "price_policy": "MARKET",
    })
    venue.advance(lp.submit_outbound_ns + lp.venue_handling_ns + 1000)
    assert venue.position_lots == 10
    assert venue.entry_price_ticks == 100

    # Adverse price crash: bid drops to 40 (PnL = (40 - 100) * 10 = -600 ticks)
    # Margin equity = 500 - 600 = -100 ticks <= maint margin (40*10*0.05 = 20) + close fee
    venue.on_market(_make_book_envelope(bids=[(40, 10)], asks=[(41, 10)]))
    events = venue.advance(tl.current_time_ns + 1000)

    liq_events = [e for e in events if e.event_type == "SimulatedLiquidationTriggered"]
    assert len(liq_events) == 1
    assert venue.is_liquidated is True
    # Forced depth reduction liquidated the position
    assert venue.position_lots == 0


def test_sim_venue_virtual_collateral_check():
    tl = VirtualTimeline()
    lp = LatencyProfile.synthetic_defaults()
    # Collateral = 100 ticks, maintenance margin = 5%
    venue = SimVenue(
        tl,
        lp,
        FeeProfile(),
        isolated_collateral_ticks=Decimal("100"),
        maintenance_margin_rate=Decimal("0.05"),
    )
    venue.on_market(_make_book_envelope(bids=[(100, 10)], asks=[(101, 10)]))

    # Order of 100 lots at 100 requires 100 * 100 * 0.05 = 500 margin > 100 collateral
    venue.submit({
        "action": "ENTER",
        "intent_id": "huge_order",
        "side": "BUY",
        "desired_quantity": 100,
        "price_policy": "LIMIT",
        "price_ticks": 100,
    })
    events = venue.advance(
        lp.submit_outbound_ns + lp.venue_handling_ns + lp.private_ack_ns + 1000
    )
    rejects = [
        json.loads(e.payload.decode("utf-8")) for e in events if e.event_type == "OrderRejected"
    ]
    assert len(rejects) == 1
    assert rejects[0]["reason"] == "INSUFFICIENT_MARGIN"


def test_sim_venue_on_end_of_data():
    tl = VirtualTimeline()
    lp = LatencyProfile.synthetic_defaults()
    venue = SimVenue(tl, lp, FeeProfile())
    venue.on_market(_make_book_envelope(bids=[(100, 10)], asks=[(101, 10)]))

    # Rest an order and hold a position
    venue.submit({
        "action": "ENTER",
        "intent_id": "rest_1",
        "side": "BUY",
        "desired_quantity": 5,
        "price_policy": "LIMIT",
        "price_ticks": 98,
    })
    venue.advance(lp.submit_outbound_ns + lp.venue_handling_ns + 1000)
    assert len(venue.queue.orders) == 1

    venue.position_lots = 4
    venue.entry_price_ticks = 95
    venue.mark_price_ticks = 100

    # End of data without forced close
    events = venue.on_end_of_data(forced_close=False)
    # Cancels pending resting orders
    cancels = [e for e in events if e.event_type == "OrderCanceled"]
    assert len(cancels) == 1
    assert len(venue.queue.orders) == 0
    # Marks open position to last mark price
    marks = [e for e in events if e.event_type == "PositionMarkedToEnd"]
    assert len(marks) == 1
    m_data = json.loads(marks[0].payload.decode("utf-8"))
    assert m_data["position_lots"] == 4
    assert m_data["unrealized_pnl_ticks"] == "20"  # (100 - 95) * 4

