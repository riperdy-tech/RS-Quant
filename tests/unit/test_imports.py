from dataclasses import replace
from decimal import Decimal

import pytest


def mapping():
    from quantdesk.data.importer import ImportMapping
    from quantdesk.venues.instruments import InstrumentSpec

    spec = InstrumentSpec.create(
        instrument_id="fixture:perp:BTC:USDT:USDT:BTCUSDT",
        tick_size=Decimal("0.1"),
        quantity_step=Decimal("0.01"),
    )
    return ImportMapping(
        spec=spec,
        timestamp_column="time",
        timestamp_unit="s",
        timezone="UTC",
        timestamp_role="start",
        interval_ns=60_000_000_000,
        source="user CSV",
        delivery_delay_ns=5,
    )


def test_import_preview_requires_explicit_time_and_units():
    from quantdesk.data.importer import Importer

    importer = Importer()
    upload = b"time,open,high,low,close,volume\n0,10,12,9,11,0.20\n"
    preview = importer.preview(upload)
    assert preview.columns == ("time", "open", "high", "low", "close", "volume")
    assert "timestamp_unit" in preview.required_mapping
    assert not importer.validate(upload, None).valid
    report = importer.validate(upload, mapping())
    assert report.valid and report.capabilities == frozenset({"OHLCV"})
    assert report.rows[0].bar.volume_lots == 20
    assert report.rows[0].available_ns == 60_000_000_005


@pytest.mark.parametrize(
    "row,code",
    [
        ("0,10,9,8,11,1", "INVALID_ROW"),
        ("0,10,12,8,11,-1", "INVALID_ROW"),
        ("nonsense,10,12,8,11,1", "INVALID_ROW"),
    ],
)
def test_bad_imports_have_row_reports(row, code):
    from quantdesk.data.importer import Importer

    report = Importer().validate(("time,open,high,low,close,volume\n" + row).encode(), mapping())
    assert not report.valid and report.errors[0].row == 2 and report.errors[0].code == code
    assert b"row,code,message" in report.error_csv()


def test_conflicting_duplicates_and_order_rejected():
    from quantdesk.data.importer import Importer

    report = Importer().validate(
        b"time,open,high,low,close,volume\n60,10,12,9,11,1\n0,10,12,9,11,1\n60,10,13,9,11,1",
        mapping(),
    )
    assert {error.code for error in report.errors} == {"OUT_OF_ORDER", "CONFLICTING_DUPLICATE"}


def test_dst_ambiguity_and_parquet_float_boundaries_rejected(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quantdesk.data.importer import Importer

    upload = b"time,open,high,low,close,volume\n2025-11-02T01:30:00,10,12,9,11,1"
    report = Importer().validate(
        upload, replace(mapping(), timestamp_unit="iso", timezone="America/New_York")
    )
    assert not report.valid
    path = tmp_path / "input.parquet"
    pq.write_table(
        pa.table(
            {
                "time": [0],
                "open": [10.1],
                "high": [12.0],
                "low": [9.0],
                "close": [11.0],
                "volume": [1.0],
            }
        ),
        path,
    )
    assert not Importer().validate(path, mapping()).valid


def test_import_confirmation_publishes_immutable_ohlcv(tmp_path):
    from quantdesk.data.catalog import Catalog
    from quantdesk.data.importer import Importer

    importer = Importer()
    report = importer.validate(b"time,open,high,low,close,volume\n0,10,12,9,11,1", mapping())
    catalog = Catalog(tmp_path)
    manifest = importer.publish(report, mapping(), catalog)
    assert catalog.require(manifest.dataset_id, frozenset({"OHLCV"})).row_count == 1
    with pytest.raises(ValueError):
        catalog.require(manifest.dataset_id, frozenset({"L2"}))
    assert importer.publish(report, mapping(), catalog) == manifest


def test_raw_journal_import_retains_durable_receipts(tmp_path):
    from quantdesk.data.importer import CanonicalImportMapping, Importer
    from quantdesk.persistence.raw_journal import RawFrame, RawJournal

    with RawJournal(tmp_path / "raw") as journal:
        ref = journal.append(
            RawFrame(
                b'{"id":"a","price":"10","size":"0.02","event_ns":0}',
                "fixture",
                "DEMO",
                "websocket",
                100,
                10,
                "a",
                1,
            )
        )
        journal.sync()
        report = Importer().validate(journal, CanonicalImportMapping(mapping().spec, "raw fixture"))
        assert report.valid and report.events[0].raw_ref == str(ref)
        assert report.events[0].available_ns == 100
        assert "RECEIVE_TIMESTAMPS" in report.capabilities
        from quantdesk.data.catalog import Catalog

        catalog = Catalog(tmp_path / "catalog")
        selected_mapping = CanonicalImportMapping(mapping().spec, "raw fixture")
        manifest = Importer().publish(report, selected_mapping, catalog)
        restored = Importer().validate(catalog.artifact(manifest.dataset_id), selected_mapping)
        assert restored.valid and restored.events == report.events


def test_report_cannot_be_published_under_different_mapping(tmp_path):
    from quantdesk.data.catalog import Catalog
    from quantdesk.data.importer import Importer

    importer = Importer()
    report = importer.validate(b"time,open,high,low,close,volume\n0,10,12,9,11,1", mapping())
    with pytest.raises(ValueError, match="mapping"):
        importer.publish(report, replace(mapping(), source="different"), Catalog(tmp_path))


def test_raw_import_rejects_frames_beyond_durable_watermark(tmp_path):
    from quantdesk.data.importer import CanonicalImportMapping, Importer
    from quantdesk.persistence.raw_journal import RawFrame, RawJournal

    with RawJournal(tmp_path / "raw") as journal:
        journal.append(
            RawFrame(
                b'{"id":"a","price":"10","size":"0.02","event_ns":0}',
                "fixture",
                "DEMO",
                "websocket",
                100,
                10,
                "a",
                1,
            )
        )
        assert (
            not Importer()
            .validate(journal, CanonicalImportMapping(mapping().spec, "raw fixture"))
            .valid
        )
