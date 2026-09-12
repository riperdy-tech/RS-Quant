from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from quantdesk.core.events import BookDelta, BookSnapshot, canonical_bytes
from quantdesk.core.types import BookLevel
from tests.support.engine_case import incoming


def book_event(seq=1, *, snapshot=True, bids=((100, 5),), asks=((102, 6),), epoch="a"):
    def levels(rows):
        return tuple(BookLevel(*row) for row in rows)

    payload = (
        BookSnapshot(levels(bids), levels(asks), str(seq), None, ())
        if snapshot
        else BookDelta(levels(bids), levels(asks), str(seq), None, None, ())
    )
    return replace(
        incoming(seq),
        event_type=type(payload).__name__,
        payload=canonical_bytes(payload),
        connection_epoch=epoch,
        instrument_id="fixture:perp:BTC:USDT:USDT:BTCUSDT",
        source_channel="books",
    )


def test_absolute_updates_delete_and_immutable_top_n():
    from quantdesk.data.orderbook.builder import BookBuilder

    book = BookBuilder("contiguous_fixture", depth=1)
    first = book.apply(book_event(bids=((100, 5), (99, 8)))).view
    assert first.bids == (BookLevel(100, 5),)
    assert first.bid_levels == 2
    updated = book.apply(book_event(2, snapshot=False, bids=((100, 0), (99, 7)), asks=())).view
    assert updated.bids == (BookLevel(99, 7),)
    assert first.bids == (BookLevel(100, 5),)
    with pytest.raises(FrozenInstanceError):
        first.bids = ()


def test_gap_rebuild_epoch_duplicate_and_stale_snapshot():
    from quantdesk.data.orderbook.builder import BookBuilder

    book = BookBuilder("contiguous_fixture")
    event = book_event()
    assert book.apply(event).state == "VALID"
    assert book.apply(event).duplicate
    assert book.apply(book_event(3, snapshot=False)).state == "INVALID"
    assert book.view is None
    assert book.begin_sync("b").state == "SYNCING"
    assert book.apply(book_event(4, epoch="a")).view is None
    assert book.apply(book_event(1, epoch="b", bids=((100, 7),))).view.bids[0].size_lots == 7
    assert book.apply(book_event(0, epoch="b")).state == "INVALID"


@pytest.mark.parametrize(
    "bids,asks", [(((103, 1),), ((102, 1),)), ((), ((102, 1),)), (((100, 0),), ((102, 1),))]
)
def test_crossed_empty_books_fail_closed(bids, asks):
    from quantdesk.data.orderbook.builder import BookBuilder

    result = BookBuilder("contiguous_fixture").apply(book_event(bids=bids, asks=asks))
    assert result.state == "INVALID" and result.view is None


def test_recorded_sequence_contract_does_not_invent_contiguity():
    from quantdesk.data.orderbook.builder import BookBuilder

    book = BookBuilder("recorded_monotonic")
    book.apply(book_event())
    assert book.apply(book_event(99, snapshot=False)).state == "VALID"
    assert book.apply(book_event(98, snapshot=False)).view is None


def test_overflow_and_freshness():
    from quantdesk.data.orderbook.builder import BookBuilder

    book = BookBuilder("contiguous_fixture", max_levels=2, max_age_ns=10)
    book.apply(book_event())
    assert book.check_freshness(111).state == "STALE"
    assert book.view is None
    book.begin_sync("b")
    assert book.apply(book_event(epoch="b", bids=((100, 1), (99, 1)))).state == "INVALID"


def test_instrument_exact_alignment_and_point_in_time_revisions():
    from quantdesk.venues.instruments import InstrumentRegistry, InstrumentSpec

    spec = InstrumentSpec.create(
        instrument_id="fixture:perp:BTC:USDT:USDT:BTCUSDT",
        tick_size=Decimal("0.1"),
        quantity_step=Decimal("0.001"),
        contract_multiplier=Decimal("2"),
        valid_from_ns=0,
        known_from_ns=10,
    )
    assert spec.price_to_ticks("12.3") == 123
    assert spec.quantity_to_lots("0.007") == 7
    assert spec.base_quantity(-7) == Decimal("-0.014")
    with pytest.raises(ValueError):
        spec.price_to_ticks("12.31")
    with pytest.raises(TypeError):
        spec.quantity_to_lots(0.007)
    registry = InstrumentRegistry()
    registry.add(spec)
    later = InstrumentSpec.create(
        instrument_id=spec.instrument_id,
        tick_size=Decimal("0.2"),
        quantity_step=Decimal("0.001"),
        valid_from_ns=0,
        known_from_ns=20,
    )
    registry.add(later)
    assert registry.at(spec.instrument_id, 5, 15) == spec
    assert registry.at(spec.instrument_id, 5, 25) == later
    with pytest.raises(LookupError):
        registry.at(spec.instrument_id, 5, 5)


def test_causal_bars_boundaries_lateness_and_missing_intervals():
    from quantdesk.data.bars import BarBuilder

    bars = BarBuilder(interval_ns=100, allowed_lateness_ns=10)
    bars.apply(replace(incoming(1, price=100, available_ns=2), exchange_event_ns=0))
    bars.apply(replace(incoming(2, price=102, available_ns=101), exchange_event_ns=100))
    assert bars.finalize(109) == ()
    first = bars.finalize(110)[0]
    assert first.bar.close_ticks == 100 and first.bar.volume_lots == 1
    late = bars.apply(replace(incoming(3, price=90, available_ns=120), exchange_event_ns=50))
    assert late.reason == "LATE_TRADE" and first.bar.close_ticks == 100
    closed = bars.finalize(410)
    assert closed[0].bar.open_ticks == 102
    assert [row.start_ns for row in closed if row.bar is None] == [200, 300]


def test_malformed_levels_and_conflicting_duplicate_quarantine():
    from quantdesk.data.orderbook.builder import BookBuilder

    book = BookBuilder("contiguous_fixture")
    book.apply(book_event())
    assert book.apply(book_event(bids=((100, 7),))).state == "INVALID"
    book.begin_sync("b")
    bad = book_event(epoch="b")
    import json

    payload = json.loads(bad.payload)
    payload["bids"][0]["size_lots"] = 0.5
    assert book.apply(replace(bad, payload=canonical_bytes(payload))).view is None


def test_snapshot_feed_rejects_deltas_and_old_epoch_cannot_replace_current():
    from quantdesk.data.orderbook.builder import BookBuilder

    book = BookBuilder("recorded_snapshot")
    book.apply(book_event())
    book.begin_sync("b")
    current = book.apply(book_event(10, epoch="b", bids=((100, 7),))).view
    assert book.apply(book_event(11, epoch="a")).view == current
    assert book.apply(book_event(12, epoch="b", snapshot=False)).state == "INVALID"


def test_late_trade_after_deadline_before_timer_is_included_only_when_available():
    from quantdesk.data.bars import BarBuilder

    bars = BarBuilder(interval_ns=100, allowed_lateness_ns=10)
    bars.apply(replace(incoming(1, price=100, available_ns=1), exchange_event_ns=0))
    # The timer's actual availability controls the causal cut, not hidden wall time.
    bars.apply(replace(incoming(2, price=105, available_ns=115), exchange_event_ns=90))
    result = bars.finalize(120)
    assert result[0].bar.close_ticks == 105 and result[0].available_ns == 120


def test_order_capabilities_fail_independently():
    from quantdesk.venues.capabilities import UnsupportedCapability, VenueCapabilities

    venue = VenueCapabilities(
        order_types=frozenset({"LIMIT"}), time_in_force=frozenset({"GTC", "POST_ONLY"})
    )
    venue.require_order("LIMIT", "GTC")
    with pytest.raises(UnsupportedCapability):
        venue.require_order("LIMIT", "GTC", reduce_only=True)
    with pytest.raises(UnsupportedCapability):
        venue.require_order("LIMIT", "POST_ONLY")


def test_same_epoch_rebuild_rejects_older_snapshot():
    from quantdesk.data.orderbook.builder import BookBuilder

    book = BookBuilder("recorded_monotonic")
    book.apply(book_event(10))
    book.invalidate("gap")
    book.begin_sync("a")
    assert book.apply(replace(book_event(9), available_ns=1100)).state == "INVALID"


def test_out_of_order_event_times_before_first_timer_are_still_known_bars():
    from quantdesk.data.bars import BarBuilder

    bars = BarBuilder(interval_ns=100)
    bars.apply(replace(incoming(1, price=102, available_ns=100), exchange_event_ns=100))
    assert (
        bars.apply(replace(incoming(2, price=100, available_ns=101), exchange_event_ns=0)) is None
    )
    assert [row.bar.close_ticks for row in bars.finalize(200)] == [100, 102]


@pytest.mark.parametrize("changes", [{"environment": "LIVE"}, {"venue": "another-venue"}])
def test_book_rejects_cross_environment_or_venue_deltas(changes):
    from quantdesk.data.orderbook.builder import BookBuilder

    book = BookBuilder("contiguous_fixture")
    book.apply(book_event())
    update = book.apply(replace(book_event(2, snapshot=False), **changes))
    assert update.state == "INVALID" and update.view is None
    assert update.reason == "STREAM_SCOPE_MISMATCH"


def test_identical_fresh_snapshot_updates_receipt_without_replaying_same_receipt():
    from quantdesk.data.orderbook.builder import BookBuilder

    book = BookBuilder("recorded_snapshot", max_age_ns=100)
    first = replace(book_event(), raw_ref="first")
    book.apply(first)
    second = replace(
        first, receive_wall_ns=180, receive_monotonic_ns=80, available_ns=180, raw_ref="second"
    )
    updated = book.apply(second)
    assert not updated.duplicate
    assert updated.view.available_ns == 180 and updated.view.raw_ref == "second"
    assert book.check_freshness(250).state == "VALID"
    assert book.apply(second).duplicate


def test_bars_preserve_canonical_large_integer_ticks_and_lots():
    import json

    from quantdesk.data.bars import BarBuilder

    large = 2**53 + 1
    event = replace(incoming(1, price=large, available_ns=1), exchange_event_ns=0)
    payload = json.loads(event.payload)
    payload["size_lots"] = str(large)
    bars = BarBuilder(interval_ns=100)
    bars.apply(replace(event, payload=canonical_bytes(payload)))
    closed = bars.finalize(100)[0].bar
    assert closed.close_ticks == large and closed.volume_lots == large


@pytest.mark.parametrize("digits", [51, 80, 130])
def test_instrument_conversions_round_trip_arbitrary_accepted_precision(digits):
    from fractions import Fraction

    from quantdesk.venues.instruments import InstrumentSpec

    spec = InstrumentSpec.create(
        instrument_id="fixture:perp:BTC:USDT:USDT:BTCUSDT",
        tick_size=Decimal("0.12345678901234567890123456789"),
        quantity_step=Decimal("0.000000001234567890123456789"),
        contract_multiplier=Decimal("1.234567890123456789"),
    )
    count = 10**digits + 12345
    price = spec.price(count)
    assert spec.price_to_ticks(price) == count
    assert Fraction(price) == count * Fraction(spec.tick_size)
    assert Fraction(spec.base_quantity(-count)) == (
        -count * Fraction(spec.quantity_step) * Fraction(spec.contract_multiplier)
    )
