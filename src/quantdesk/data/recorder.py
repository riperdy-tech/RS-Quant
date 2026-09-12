from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from quantdesk.core.events import canonical_bytes
from quantdesk.data.catalog import (
    Catalog,
    DatasetId,
    DatasetManifest,
    RawRange,
    confined,
    identified,
    publish_bytes,
)
from quantdesk.persistence.event_store import EventStore
from quantdesk.persistence.manifests import RawRef
from quantdesk.persistence.raw_journal import RawFrame, RawJournal
from quantdesk.venues.instruments import InstrumentSpec

EVENT_CAPABILITIES = {
    "Trade": "TRADES",
    "BookSnapshot": "L2",
    "BookDelta": "L2",
    "Quote": "BBO",
    "BarClosed": "OHLCV",
    "MarkPrice": "MARK",
    "FundingRateAnnounced": "FUNDING",
}


def span(values: list[int]) -> tuple[int, int] | None:
    return (min(values), max(values)) if values else None


@dataclass(slots=True)
class Recorder:
    journal: RawJournal
    store: EventStore
    catalog: Catalog

    def record(self, frame: RawFrame) -> RawRef:
        ref = self.journal.append(frame)
        self.journal.sync()
        return ref

    def export(
        self,
        *,
        spec: InstrumentSpec,
        first_seq: int,
        last_seq: int,
        origin: str,
        max_rows: int = 100000,
    ) -> DatasetManifest:
        if first_seq < 1 or last_seq < first_seq or last_seq - first_seq + 1 > max_rows:
            raise ValueError("invalid or oversized export range")
        records = [
            r for r in self.store.read_after(first_seq - 1) if r.envelope.engine_seq <= last_seq
        ]
        if [r.envelope.engine_seq for r in records] != list(range(first_seq, last_seq + 1)):
            raise ValueError("export range includes uncommitted events")
        events = [r.envelope for r in records]
        raw_refs = []
        for event in events:
            if (
                event.event_type not in EVENT_CAPABILITIES
                or event.account_id is not None
                or event.instrument_id != spec.instrument_id
                or event.raw_ref is None
            ):
                raise ValueError("public export requires scoped market facts with raw evidence")
            ref = RawRef.parse(event.raw_ref)
            frame = self.journal.read(ref)
            if (
                frame.private
                or frame.venue != event.venue
                or frame.environment != event.environment
                or frame.connection_epoch != event.connection_epoch
                or frame.receive_wall_ns != event.receive_wall_ns
                or frame.receive_monotonic_ns != event.receive_monotonic_ns
            ):
                raise ValueError("raw evidence scope mismatch or private export")
            raw_refs.append(ref)
        if len({(e.venue, e.environment, e.event_type) for e in events}) != 1:
            raise ValueError("export one venue/environment/event type partition at a time")
        capabilities = frozenset(
            {EVENT_CAPABILITIES[e.event_type] for e in events} | {"RECEIVE_TIMESTAMPS"}
        )
        rows = [
            {
                "engine_seq": str(e.engine_seq),
                "event_id": e.event_id,
                "event_type": e.event_type,
                "raw_ref": e.raw_ref,
                "exchange_event_ns": str(e.exchange_event_ns)
                if e.exchange_event_ns is not None
                else None,
                "receive_wall_ns": str(e.receive_wall_ns),
                "available_ns": str(e.available_ns),
                "envelope_json": canonical_bytes(e),
                "payload": e.payload,
            }
            for e in events
        ]
        table = pa.Table.from_pylist(rows).replace_schema_metadata(
            {b"quantdesk.capabilities": json.dumps(sorted(capabilities)).encode()}
        )
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink, compression="zstd")
        data = sink.getvalue().to_pybytes()
        digest = hashlib.sha256(data).hexdigest()
        if pq.ParquetFile(pa.BufferReader(data)).metadata.num_rows != len(rows):
            raise ValueError("Parquet verification failed")
        first = events[0]
        date = datetime.fromtimestamp(first.receive_wall_ns // 1_000_000_000, UTC)
        if any(
            e.receive_wall_ns // 3_600_000_000_000 != first.receive_wall_ns // 3_600_000_000_000
            for e in events
        ):
            raise ValueError("export must be partitioned by receipt hour")
        parts = spec.instrument_id.split(":")
        import re

        components = (first.venue or "", first.environment, parts[1], parts[5], first.event_type)
        if any(re.fullmatch(r"[A-Za-z0-9_-]+", p) is None for p in components):
            raise ValueError("unsafe partition component")
        relative = "/".join(
            (*components, date.strftime("%Y-%m-%d"), date.strftime("%H"), f"part-{digest}.parquet")
        )
        publish_bytes(confined(self.catalog.root, relative), data)
        manifest = identified(
            DatasetManifest(
                DatasetId(""),
                relative,
                digest,
                len(rows),
                capabilities,
                spec.instrument_id,
                spec.revision_hash,
                origin,
                "canonical-v1",
                tuple(sorted({e.producer_version for e in events})),
                (
                    RawRange(
                        raw_refs[0].journal_id,
                        min(r.frame_ordinal for r in raw_refs),
                        max(r.frame_ordinal for r in raw_refs),
                        tuple(str(r) for r in raw_refs),
                    ),
                ),
                (first_seq, last_seq),
                span([e.exchange_event_ns for e in events if e.exchange_event_ns is not None]),
                span([e.receive_wall_ns for e in events]),
                (min(e.available_ns for e in events), max(e.available_ns for e in events)),
            )
        )
        self.catalog.publish(manifest)
        return manifest
