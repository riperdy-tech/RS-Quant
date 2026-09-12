import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from quantdesk.core.engine import Engine
from quantdesk.data.catalog import Catalog
from quantdesk.data.orderbook.builder import BookBuilder
from quantdesk.data.recorder import Recorder
from quantdesk.persistence.db import Database
from quantdesk.persistence.event_store import EventStore
from quantdesk.persistence.raw_journal import RawFrame, RawJournal
from tests.unit.test_book import book_event
from tests.unit.test_imports import mapping


def book_gap_and_rebuild_case(**overrides: object) -> dict[str, object]:
    book = BookBuilder(str(overrides["sequence_contract"]))
    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "fixtures"
            / "market_scenarios"
            / "book_gap_and_rebuild.json"
        ).read_text()
    )
    messages = tuple(
        book_event(
            row["sequence"],
            snapshot=row["snapshot"],
            epoch=row["epoch"],
            bids=((100, row["bid_lots"]),),
        )
        for row in fixture["messages"]
    )
    states = []
    invalid_consumption = 0
    with TemporaryDirectory(prefix="quantdesk-book-") as temporary:
        path = Path(temporary)
        with RawJournal(path / "raw") as journal, Database(path / "db.sqlite") as db:
            store = EventStore(db)
            recorder = Recorder(journal, store, Catalog(path / "catalog"))
            engine = Engine(
                "deterministic-run",
                store,
                raw_watermark=journal.sync,
                code_hash="book-case",
                schema_hash="1",
            )
            for ordinal, event in enumerate(messages, 1):
                if ordinal == 3:
                    states.append(book.begin_sync("b").state)
                frame = RawFrame(
                    event.payload,
                    "fixture",
                    "DEMO",
                    "websocket",
                    ordinal * 100,
                    ordinal * 10,
                    event.connection_epoch,
                    ordinal,
                )
                ref = recorder.record(frame)
                event = replace(
                    event,
                    account_id=None,
                    raw_ref=str(ref),
                    receive_wall_ns=ordinal * 100,
                    receive_monotonic_ns=ordinal * 10,
                    available_ns=ordinal * 100,
                )
                engine.commit(engine.process(event))
                update = book.apply(event)
                states.append(update.state)
                if update.state != "VALID" and book.view is not None:
                    invalid_consumption += 1
            manifest = recorder.export(
                spec=mapping().spec, first_seq=3, last_seq=3, origin="SYNTHETIC"
            )
            import pyarrow.parquet as pq

            rows = pq.ParquetFile(recorder.catalog.artifact(manifest.dataset_id)).metadata.num_rows
            return {
                "book_states": states,
                "entry_decisions_while_invalid": invalid_consumption,
                "final_bid_lots": book.view.bids[0].size_lots if book.view else None,
                "parquet_rows": rows,
                "published_manifest_rows": manifest.row_count,
            }
