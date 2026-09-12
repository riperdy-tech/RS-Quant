from dataclasses import replace

import pytest


def test_raw_canonical_parquet_traceability_and_capability_checks(tmp_path):
    import pyarrow.parquet as pq

    from quantdesk.core.engine import Engine
    from quantdesk.data.catalog import Catalog
    from quantdesk.data.normalizer import Normalizer
    from quantdesk.data.recorder import Recorder
    from quantdesk.persistence.db import Database
    from quantdesk.persistence.event_store import EventStore
    from quantdesk.persistence.raw_journal import RawFrame, RawJournal
    from tests.unit.test_imports import mapping

    spec = mapping().spec
    with RawJournal(tmp_path / "raw") as journal, Database(tmp_path / "engine.sqlite") as db:
        store = EventStore(db)
        recorder = Recorder(journal, store, Catalog(tmp_path / "catalog"))
        engine = Engine(
            "record-run", store, raw_watermark=journal.sync, code_hash="test", schema_hash="1"
        )
        for ordinal, price in enumerate(("10.1", "9.9"), 1):
            frame = RawFrame(
                (
                    f'{{"id":"{ordinal}","price":"{price}","size":"0.02",'
                    f'"event_ns":"{100 - ordinal}"}}'
                ).encode(),
                "fixture",
                "DEMO",
                "websocket",
                ordinal * 100,
                ordinal * 10,
                "a",
                ordinal,
            )
            ref = recorder.record(frame)
            event = Normalizer(spec).trade(
                journal.read(ref), ref, run_id="record-run", available_ns=ordinal * 100
            )
            assert not hasattr(event, "engine_seq")
            engine.commit(engine.process(event))
        manifest = recorder.export(spec=spec, first_seq=1, last_seq=2, origin="SYNTHETIC")
        rows = pq.read_table(recorder.catalog.artifact(manifest.dataset_id)).to_pylist()
        assert [int(row["engine_seq"]) for row in rows] == [1, 2]
        assert [int(row["exchange_event_ns"]) for row in rows] == [99, 98]
        assert [row["raw_ref"] for row in rows] == [str(ref) for ref in journal.references()]
        assert manifest.row_count == 2 and manifest.raw_ranges[0].first_ordinal == 1
        assert "TRADES" in manifest.capabilities and "L2" not in manifest.capabilities
        with pytest.raises(ValueError, match="capabilit"):
            recorder.catalog.require(manifest.dataset_id, frozenset({"L2"}))
        assert recorder.catalog.publish(manifest) == manifest.dataset_id
        from quantdesk.data.importer import CanonicalImportMapping, Importer

        imported = Importer().validate(
            recorder.catalog.artifact(manifest.dataset_id),
            CanonicalImportMapping(spec, "recorded fixture"),
        )
        assert imported.valid
        assert [e.raw_ref for e in imported.events] == [str(ref) for ref in journal.references()]
        with pytest.raises(ValueError):
            recorder.catalog.publish(replace(manifest, row_count=999))
        with pytest.raises(ValueError):
            recorder.catalog.publish(replace(manifest, artifact_path="../escape.parquet"))
        with pytest.raises(ValueError):
            recorder.export(spec=spec, first_seq=1, last_seq=3, origin="SYNTHETIC")


def test_invalid_book_requires_rebuild(case):
    r = case("book_gap_and_rebuild", sequence_contract="contiguous_fixture")
    assert r["book_states"] == ["VALID", "INVALID", "SYNCING", "VALID"]
    assert r["entry_decisions_while_invalid"] == 0
    assert r["final_bid_lots"] == 7
    assert r["parquet_rows"] == r["published_manifest_rows"]
