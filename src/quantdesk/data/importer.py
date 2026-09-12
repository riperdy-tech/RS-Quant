from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from dataclasses import dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from quantdesk.core.events import BarClosed, IncomingEvent, canonical_bytes
from quantdesk.data.bars import ClosedBar
from quantdesk.data.catalog import (
    Catalog,
    DatasetId,
    DatasetManifest,
    RawRange,
    identified,
    publish_bytes,
)
from quantdesk.data.normalizer import Normalizer
from quantdesk.data.orderbook.validator import levels
from quantdesk.persistence.manifests import RawRef
from quantdesk.persistence.raw_journal import RawJournal
from quantdesk.venues.instruments import InstrumentSpec, aligned


@dataclass(frozen=True, slots=True)
class ImportMapping:
    spec: InstrumentSpec
    timestamp_column: str
    timestamp_unit: str
    timezone: str
    timestamp_role: str
    interval_ns: int
    source: str
    delivery_delay_ns: int
    columns: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ImportPreview:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    required_mapping: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CanonicalImportMapping:
    spec: InstrumentSpec
    source: str
    run_id: str = "imported-market"


@dataclass(frozen=True, slots=True)
class RowError:
    row: int
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ImportReport:
    rows: tuple[ClosedBar, ...]
    errors: tuple[RowError, ...]
    capabilities: frozenset[str] = frozenset({"OHLCV"})
    assumptions: tuple[str, ...] = ("RECONSTRUCTED_AVAILABILITY", "BAR_ONLY_NO_INTRABAR_DEPTH")
    events: tuple[IncomingEvent, ...] = ()
    mapping_hash: str = ""

    @property
    def valid(self) -> bool:
        return bool(self.rows or self.events) and not self.errors

    def error_csv(self) -> bytes:
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(("row", "code", "message"))
        for error in self.errors:
            safe = error.message
            if safe.lstrip().startswith(("=", "+", "-", "@")):
                safe = "'" + safe
            writer.writerow((error.row, error.code, safe))
        return stream.getvalue().encode()


def timestamp_ns(value: object, mapping: ImportMapping) -> int:
    if mapping.timestamp_unit != "iso":
        from decimal import Decimal

        factors = {"s": 1_000_000_000, "ms": 1_000_000, "us": 1000, "ns": 1}
        factor = factors[mapping.timestamp_unit]
        return aligned(value, Decimal(1) / Decimal(factor))
    if not isinstance(value, str):
        raise ValueError("ISO timestamp must be text")
    time = datetime.fromisoformat(value)
    if time.tzinfo is None:
        zone = ZoneInfo(mapping.timezone)
        one, two = time.replace(tzinfo=zone, fold=0), time.replace(tzinfo=zone, fold=1)
        if one.utcoffset() != two.utcoffset():
            raise ValueError("ambiguous or nonexistent local timestamp; explicit offset required")
        time = one
    delta = time.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return ((delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds) * 1000


class Importer:
    def __init__(
        self,
        *,
        max_bytes: int = 64 * 1024 * 1024,
        max_rows: int = 100000,
        max_decoded_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        self.max_bytes, self.max_rows, self.max_decoded_bytes = (
            max_bytes,
            max_rows,
            max_decoded_bytes,
        )

    def _read(self, upload: bytes | Path) -> tuple[tuple[str, ...], list[dict[str, object]]]:
        if isinstance(upload, Path):
            if upload.stat().st_size > self.max_bytes:
                raise ValueError("upload exceeds size limit")
            data = upload.read_bytes()
        else:
            data = upload
        if len(data) > self.max_bytes:
            raise ValueError("upload exceeds size limit")
        if data.startswith(b"PAR1"):
            file = pq.ParquetFile(pa.BufferReader(data))
            if file.metadata.num_rows > self.max_rows:
                raise ValueError("Parquet exceeds row limit")
            if (
                sum(
                    file.metadata.row_group(i).total_byte_size
                    for i in range(file.metadata.num_row_groups)
                )
                > self.max_decoded_bytes
            ):
                raise ValueError("Parquet exceeds decompression limit")
            rows: list[dict[str, object]] = file.read().to_pylist()
            return tuple(file.schema_arrow.names), rows
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        columns = tuple(reader.fieldnames or ())
        if not columns or len(set(columns)) != len(columns):
            raise ValueError("missing or duplicate CSV columns")
        result: list[dict[str, object]] = []
        for row in reader:
            if len(result) >= self.max_rows:
                raise ValueError("CSV exceeds row limit")
            if None in row or any(v is None for v in row.values()):
                raise ValueError("CSV row column count mismatch")
            result.append(dict(row))
        return columns, result

    def preview(self, upload: bytes | Path) -> ImportPreview:
        columns, rows = self._read(upload)
        return ImportPreview(
            columns,
            tuple(tuple(str(r[c]) for c in columns) for r in rows[:10]),
            (
                "instrument",
                "units",
                "timestamp_unit",
                "timezone",
                "timestamp_role",
                "interval_ns",
                "source",
                "delivery_delay_ns",
            ),
        )

    def validate(
        self,
        upload: bytes | Path | RawJournal,
        mapping: ImportMapping | CanonicalImportMapping | None,
    ) -> ImportReport:
        if isinstance(mapping, CanonicalImportMapping):
            return self._canonical(upload, mapping)
        if isinstance(upload, RawJournal):
            return ImportReport(
                (), (RowError(0, "INVALID_MAPPING", "raw schema mapping required"),)
            )
        if mapping is None:
            return ImportReport(
                (), (RowError(0, "AMBIGUOUS_MAPPING", "explicit mapping required"),)
            )
        if (
            mapping.timestamp_unit not in {"s", "ms", "us", "ns", "iso"}
            or not mapping.timezone
            or not mapping.source
            or mapping.timestamp_role not in {"start", "end"}
            or mapping.interval_ns < 1
            or mapping.delivery_delay_ns < 0
        ):
            return ImportReport(
                (), (RowError(0, "INVALID_MAPPING", "invalid time/source mapping"),)
            )
        try:
            _, rows = self._read(upload)
        except (ValueError, OSError, UnicodeError, pa.ArrowException) as exc:
            return ImportReport((), (RowError(0, "INVALID_UPLOAD", str(exc)),))
        output: list[ClosedBar] = []
        errors: list[RowError] = []
        seen: dict[int, BarClosed] = {}
        last = -1
        names = dict(mapping.columns)
        for index, row in enumerate(rows, 2):
            try:
                stamp = timestamp_ns(row[mapping.timestamp_column], mapping)
                start = stamp if mapping.timestamp_role == "start" else stamp - mapping.interval_ns
                if start < 0:
                    raise ValueError("negative bar start")
                prices = [
                    mapping.spec.price_to_ticks(row[names.get(key, key)])
                    for key in ("open", "high", "low", "close")
                ]
                bar = BarClosed(
                    start,
                    start + mapping.interval_ns,
                    prices[0],
                    prices[1],
                    prices[2],
                    prices[3],
                    mapping.spec.quantity_to_lots(row[names.get("volume", "volume")]),
                    False,
                )
                if start in seen:
                    if seen[start] != bar:
                        errors.append(RowError(index, "CONFLICTING_DUPLICATE", "conflicting bar"))
                    continue
                if start < last:
                    errors.append(RowError(index, "OUT_OF_ORDER", "bar timestamps regress"))
                seen[start] = bar
                last = max(last, start)
                output.append(
                    ClosedBar(start, bar.end_ns, bar.end_ns + mapping.delivery_delay_ns, bar)
                )
            except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
                errors.append(RowError(index, "INVALID_ROW", str(exc)))
        return ImportReport(
            tuple(output),
            tuple(errors),
            mapping_hash=hashlib.sha256(canonical_bytes(mapping)).hexdigest(),
        )

    def _canonical(
        self, upload: bytes | Path | RawJournal, mapping: CanonicalImportMapping
    ) -> ImportReport:
        events: list[IncomingEvent] = []
        errors: list[RowError] = []
        capabilities: set[str] = set()
        try:
            if isinstance(upload, RawJournal):
                references = upload.references()
                if len(references) > self.max_rows:
                    raise ValueError("raw import exceeds row limit")
                for ref in references:
                    if not upload.durable_watermark.covers(ref):
                        raise ValueError("raw import crosses durable watermark")
                    frame = upload.read(ref)
                    events.append(
                        Normalizer(mapping.spec).trade(
                            frame,
                            ref,
                            run_id=mapping.run_id,
                            available_ns=max(
                                frame.receive_wall_ns, events[-1].available_ns if events else 0
                            ),
                        )
                    )
                capabilities.update(("TRADES", "RECEIVE_TIMESTAMPS"))
            else:
                _, rows = self._read(upload)
                previous_seq = 0
                for index, row in enumerate(rows, 2):
                    try:
                        raw = row.get("envelope_json", row.get("incoming_json"))
                        if not isinstance(raw, (bytes, str)):
                            raise ValueError("canonical envelope bytes required")
                        obj = json.loads(raw)
                        sequence_value = obj.get("engine_seq", row.get("source_ordinal"))
                        if type(sequence_value) is not int and not (
                            isinstance(sequence_value, str) and sequence_value.isdecimal()
                        ):
                            raise ValueError("integer source sequence required")
                        sequence = int(sequence_value)
                        if sequence <= previous_seq:
                            raise ValueError("canonical sequence order regression")
                        previous_seq = sequence
                        obj["payload"] = base64.b64decode(obj["payload"]["$bytes"], validate=True)
                        for key in (
                            "schema_version",
                            "receive_wall_ns",
                            "receive_monotonic_ns",
                            "available_ns",
                            "exchange_event_ns",
                            "exchange_transaction_ns",
                        ):
                            obj[key] = int(obj[key]) if obj[key] is not None else None
                        event = IncomingEvent(
                            **{f.name: obj[f.name] for f in fields(IncomingEvent)}
                        )
                        if (
                            event.account_id is not None
                            or event.instrument_id != mapping.spec.instrument_id
                        ):
                            raise ValueError("private or incompatible instrument event")
                        payload = json.loads(event.payload)
                        if event.event_type == "Trade":
                            for name in ("price_ticks", "size_lots"):
                                value = payload[name]
                                if type(value) is not int or value <= 0:
                                    raise ValueError("positive integer ticks/lots required")
                            capabilities.add("TRADES")
                        elif event.event_type in {"BookSnapshot", "BookDelta"}:
                            snapshot = event.event_type == "BookSnapshot"
                            levels(payload["bids" if snapshot else "bid_updates"])
                            levels(payload["asks" if snapshot else "ask_updates"])
                            capabilities.add("L2")
                        else:
                            raise ValueError("normalized import supports trades/L2 only")
                        if events and event.available_ns < events[-1].available_ns:
                            raise ValueError("canonical availability regression")
                        events.append(event)
                        capabilities.add("RECEIVE_TIMESTAMPS")
                    except (ValueError, KeyError, TypeError) as exc:
                        errors.append(RowError(index, "INVALID_ROW", str(exc)))
        except (ValueError, OSError, TypeError, KeyError, pa.ArrowException) as exc:
            errors.append(RowError(0, "INVALID_UPLOAD", str(exc)))
        return ImportReport(
            (),
            tuple(errors),
            frozenset(capabilities),
            ("SOURCE_RAW_REFERENCES_PRESERVED", "L2_REQUIRES_SEQUENCE_VALIDATION"),
            tuple(events),
            hashlib.sha256(canonical_bytes(mapping)).hexdigest(),
        )

    def publish(
        self,
        report: ImportReport,
        mapping: ImportMapping | CanonicalImportMapping,
        catalog: Catalog,
    ) -> DatasetManifest:
        if isinstance(mapping, CanonicalImportMapping):
            return self._publish_canonical(report, mapping, catalog)
        if not report.valid or not report.rows or report.events:
            raise ValueError("only validated OHLCV report can be published through this mapping")
        if report.mapping_hash != hashlib.sha256(canonical_bytes(mapping)).hexdigest():
            raise ValueError("import mapping differs from validated report")
        rows = [
            {
                "bar_json": canonical_bytes(row.bar),
                "start_ns": str(row.start_ns),
                "end_ns": str(row.end_ns),
                "available_ns": str(row.available_ns),
            }
            for row in report.rows
        ]
        table = pa.Table.from_pylist(rows).replace_schema_metadata(
            {b"quantdesk.capabilities": b'["OHLCV"]'}
        )
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink, compression="zstd")
        data = sink.getvalue().to_pybytes()
        digest = hashlib.sha256(data).hexdigest()
        relative = f"imports/part-{digest}.parquet"
        publish_bytes(catalog.root / relative, data)
        manifest = identified(
            DatasetManifest(
                DatasetId(""),
                relative,
                digest,
                len(rows),
                frozenset({"OHLCV"}),
                mapping.spec.instrument_id,
                mapping.spec.revision_hash,
                "IMPORTED",
                "ohlcv-v1",
                ("ohlcv-import-v1",),
                (),
                None,
                (report.rows[0].start_ns, report.rows[-1].end_ns),
                None,
                (report.rows[0].available_ns, report.rows[-1].available_ns),
                (),
                (
                    *report.assumptions,
                    f"SOURCE:{mapping.source}",
                    f"DELIVERY_DELAY_NS:{mapping.delivery_delay_ns}",
                ),
            )
        )
        catalog.publish(manifest)
        return manifest

    def _publish_canonical(
        self, report: ImportReport, mapping: CanonicalImportMapping, catalog: Catalog
    ) -> DatasetManifest:
        if not report.valid or not report.events or report.rows:
            raise ValueError("validated canonical report required")
        if report.mapping_hash != hashlib.sha256(canonical_bytes(mapping)).hexdigest():
            raise ValueError("import mapping differs from validated report")
        rows = [
            {"source_ordinal": str(index), "incoming_json": canonical_bytes(event)}
            for index, event in enumerate(report.events, 1)
        ]
        table = pa.Table.from_pylist(rows).replace_schema_metadata(
            {b"quantdesk.capabilities": json.dumps(sorted(report.capabilities)).encode()}
        )
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink, compression="zstd")
        data = sink.getvalue().to_pybytes()
        digest = hashlib.sha256(data).hexdigest()
        relative = f"imports/part-{digest}.parquet"
        publish_bytes(catalog.root / relative, data)
        grouped: dict[str, list[RawRef]] = {}
        for event in report.events:
            if event.raw_ref is not None:
                ref = RawRef.parse(event.raw_ref)
                grouped.setdefault(ref.journal_id, []).append(ref)
        ranges = tuple(
            RawRange(
                key,
                min(r.frame_ordinal for r in refs),
                max(r.frame_ordinal for r in refs),
                tuple(str(r) for r in refs),
            )
            for key, refs in sorted(grouped.items())
        )
        event_times = [
            e.exchange_event_ns for e in report.events if e.exchange_event_ns is not None
        ]
        manifest = identified(
            DatasetManifest(
                DatasetId(""),
                relative,
                digest,
                len(rows),
                report.capabilities,
                mapping.spec.instrument_id,
                mapping.spec.revision_hash,
                "IMPORTED",
                "incoming-v1",
                tuple(sorted({e.producer_version for e in report.events})),
                ranges,
                None,
                (min(event_times), max(event_times)) if event_times else None,
                (
                    min(e.receive_wall_ns for e in report.events),
                    max(e.receive_wall_ns for e in report.events),
                ),
                (report.events[0].available_ns, report.events[-1].available_ns),
                (),
                (*report.assumptions, f"SOURCE:{mapping.source}", "ASSUMED_INSTRUMENT_SPEC"),
            )
        )
        catalog.publish(manifest)
        return manifest
