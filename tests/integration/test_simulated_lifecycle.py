import json
from decimal import Decimal

from quantdesk.core.events import Envelope
from quantdesk.simulation.fees import FeeProfile
from quantdesk.simulation.latency import LatencyProfile
from quantdesk.simulation.scheduler import VirtualTimeline
from quantdesk.simulation.venue import SimVenue


def test_queue_and_shared_volume_conservation(case):
    r = case("passive_queue", queue_ahead_lots=5, order_lots=3, trade_lots=6)
    assert r["own_filled_lots"] == 1
    assert r["own_remaining_lots"] == 2
    assert r["total_allocated_trade_lots"] <= 6
    assert r["fills_from_depth_deletion_alone"] == 0


def _create_book_snapshot(
    time_ns: int, bids: list[tuple[int, int]], asks: list[tuple[int, int]]
) -> Envelope:
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
        exchange_event_ns=time_ns,
        exchange_transaction_ns=None,
        receive_wall_ns=time_ns,
        receive_monotonic_ns=time_ns,
        available_ns=time_ns,
        causation_id=None,
        correlation_id="corr-snap",
        raw_ref=None,
        producer_version="v1",
        payload=json.dumps({"bids": bids, "asks": asks}).encode("utf-8"),
        event_id=f"snap-{time_ns}",
        engine_seq=0,
    )


def _create_trade(time_ns: int, price: int, lots: int, aggressor: str) -> Envelope:
    return Envelope(
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
        exchange_event_ns=time_ns,
        exchange_transaction_ns=None,
        receive_wall_ns=time_ns,
        receive_monotonic_ns=time_ns,
        available_ns=time_ns,
        causation_id=None,
        correlation_id=f"corr-trade-{time_ns}",
        raw_ref=None,
        producer_version="v1",
        payload=json.dumps({"price_ticks": price, "lots": lots, "aggressor": aggressor}).encode(
            "utf-8"
        ),
        event_id=f"trade-{time_ns}",
        engine_seq=0,
    )


def test_full_passive_order_lifecycle():
    """Submit -> Ack -> Partial Fill -> Final Fill -> Complete."""
    tl = VirtualTimeline()
    lp = LatencyProfile.synthetic_defaults()
    fp = FeeProfile(maker_rate=Decimal("0.0002"), taker_rate=Decimal("0.00055"))
    venue = SimVenue(tl, lp, fp)

    # Initial book: bid 100 with 5 lots
    venue.on_market(_create_book_snapshot(0, bids=[(100, 5)], asks=[(101, 10)]))
    venue.advance(lp.public_feed_delivery_ns)

    # Submit passive BUY of 4 lots at 100
    venue.submit({
        "action": "ENTER",
        "intent_id": "ord_life",
        "side": "BUY",
        "desired_quantity": 4,
        "price_policy": "LIMIT",
        "price_ticks": 100,
    })

    # Advance until OrderAck is delivered
    t_ack = (
        tl.current_time_ns
        + lp.submit_outbound_ns
        + lp.venue_handling_ns
        + lp.private_ack_ns
        + 1000
    )
    ack_events = venue.advance(t_ack)
    acks = [e for e in ack_events if e.event_type == "OrderAck"]
    assert len(acks) == 1
    assert "ord_life" in venue.queue.orders
    assert venue.queue.orders["ord_life"].queue_ahead_lots == 5

    # First trade: 7 lots sell (5 external consumed, 2 fills ord_life)
    venue.on_market(_create_trade(tl.current_time_ns, price=100, lots=7, aggressor="SELL"))
    t_fill1 = tl.current_time_ns + lp.fill_report_delivery_ns
    fill1_events = venue.advance(t_fill1)
    fills1 = [
        json.loads(e.payload.decode("utf-8")) for e in fill1_events if e.event_type == "OrderFill"
    ]
    assert len(fills1) == 1
    assert fills1[0]["filled_lots"] == 2
    assert fills1[0]["is_taker"] is False
    assert venue.queue.orders["ord_life"].own_remaining_lots == 2

    # Second trade: 3 lots sell (fills remaining 2 of ord_life)
    venue.on_market(_create_trade(tl.current_time_ns, price=100, lots=3, aggressor="SELL"))
    t_fill2 = tl.current_time_ns + lp.fill_report_delivery_ns
    fill2_events = venue.advance(t_fill2)
    fills2 = [
        json.loads(e.payload.decode("utf-8")) for e in fill2_events if e.event_type == "OrderFill"
    ]
    assert len(fills2) == 1
    assert fills2[0]["filled_lots"] == 2
    assert "ord_life" not in venue.queue.orders
    assert venue.position_lots == 4


def test_stress_latency_profile_scaling():
    """Verify stress latency profile multipliers scale event arrival times deterministically."""
    tl_base = VirtualTimeline()
    lp_base = LatencyProfile.synthetic_defaults()
    venue_base = SimVenue(tl_base, lp_base, FeeProfile())

    tl_stress = VirtualTimeline()
    lp_stress = LatencyProfile.stress_profile(multiplier=2)
    venue_stress = SimVenue(tl_stress, lp_stress, FeeProfile())

    venue_base.on_market(_create_book_snapshot(0, bids=[(100, 10)], asks=[(101, 10)]))
    venue_stress.on_market(_create_book_snapshot(0, bids=[(100, 10)], asks=[(101, 10)]))

    # Advance base to exact base feed delivery time
    ev_base = venue_base.advance(lp_base.public_feed_delivery_ns)
    assert len(ev_base) == 1

    # At that same time, stress venue has NOT yet delivered (2x delay)
    ev_stress_early = venue_stress.advance(lp_base.public_feed_delivery_ns)
    assert len(ev_stress_early) == 0

    # Advance stress venue to 2x delay -> delivered!
    ev_stress = venue_stress.advance(lp_stress.public_feed_delivery_ns)
    assert len(ev_stress) == 1


def test_simulation_reproducibility():
    """Two independent simulation runs with identical inputs produce identical traces."""

    def run_sim():
        tl = VirtualTimeline()
        venue = SimVenue(tl, LatencyProfile.synthetic_defaults(), FeeProfile())
        venue.on_market(_create_book_snapshot(0, [(100, 10)], [(101, 10)]))
        venue.submit({
            "action": "ENTER",
            "intent_id": "ord_rep",
            "side": "BUY",
            "desired_quantity": 5,
            "price_policy": "MARKET",
        })
        return [(e.event_type, e.payload) for e in venue.advance(100_000_000)]

    trace1 = run_sim()
    trace2 = run_sim()
    assert trace1 == trace2

