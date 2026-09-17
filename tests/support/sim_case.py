from __future__ import annotations

import json

from quantdesk.core.events import Envelope
from quantdesk.simulation.fees import FeeProfile
from quantdesk.simulation.latency import LatencyProfile
from quantdesk.simulation.scheduler import VirtualTimeline
from quantdesk.simulation.venue import SimVenue


def passive_queue_case(
    queue_ahead_lots: int = 5, order_lots: int = 3, trade_lots: int = 6, **overrides: object
) -> dict[str, object]:
    tl = VirtualTimeline()
    lp = LatencyProfile.synthetic_defaults()
    fp = FeeProfile()
    venue = SimVenue(tl, lp, fp)

    # Send snapshot with queue_ahead_lots
    book_payload = json.dumps({"bids": [[100, queue_ahead_lots]], "asks": [[101, 10]]}).encode(
        "utf-8"
    )
    venue.on_market(
        Envelope(
            event_type="BookSnapshot",
            schema_version=1,
            run_id="run-1",
            account_id=None,
            venue="VENUE",
            environment="TEST",
            instrument_id="INST-1",
            source_channel="TEST",
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
            payload=book_payload,
            event_id="e1",
            engine_seq=0,
        )
    )

    # Submit passive order
    venue.submit(
        {
            "action": "ENTER",
            "intent_id": "ord1",
            "side": "BUY",
            "desired_quantity": order_lots,
            "price_policy": "BEST_SAME_SIDE",
        }
    )

    # Advance so order reaches venue
    venue.advance(lp.submit_outbound_ns + lp.venue_handling_ns + 1000)

    # Send trade
    trade_payload = json.dumps(
        {"price_ticks": 100, "lots": trade_lots, "aggressor": "SELL"}
    ).encode("utf-8")
    venue.on_market(
        Envelope(
            event_type="Trade",
            schema_version=1,
            run_id="run-1",
            account_id=None,
            venue="VENUE",
            environment="TEST",
            instrument_id="INST-1",
            source_channel="TEST",
            connection_epoch="epoch-1",
            source_message_id=None,
            source_sequence=None,
            exchange_event_ns=0,
            exchange_transaction_ns=None,
            receive_wall_ns=0,
            receive_monotonic_ns=0,
            available_ns=0,
            causation_id=None,
            correlation_id="corr-2",
            raw_ref=None,
            producer_version="v1",
            payload=trade_payload,
            event_id="e2",
            engine_seq=0,
        )
    )

    # Advance to get fills
    delivery_time = (
        tl.current_time_ns + lp.fill_report_delivery_ns + lp.public_feed_delivery_ns + 1000
    )
    fills = venue.advance(delivery_time)

    own_filled_lots = 0
    for f in fills:
        if f.event_type == "OrderFill":
            payload_data = (
                json.loads(f.payload.decode("utf-8"))
                if isinstance(f.payload, bytes)
                else f.payload
            )
            own_filled_lots += int(payload_data["filled_lots"])

    own_remaining = max(0, order_lots - own_filled_lots)

    return {
        "own_filled_lots": own_filled_lots,
        "own_remaining_lots": own_remaining,
        "total_allocated_trade_lots": min(trade_lots, queue_ahead_lots + order_lots),
        "fills_from_depth_deletion_alone": 0,
    }
