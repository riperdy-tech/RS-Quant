from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import NewType, cast

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from quantdesk.core.events import BarClosed, canonical_bytes
from quantdesk.data.canonical import (
    canonical_int,
    decode_market_event,
    json_object,
    validate_payload,
)
from quantdesk.persistence.manifests import sync_directory
from quantdesk.venues.capabilities import Capability

DatasetId = NewType("DatasetId", str)


def confined(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if (
        Path(relative).is_absolute()
        or not path.is_relative_to(root.resolve())
        or path == root.resolve()
    ):
        raise ValueError("artifact path escapes catalog")
    return path


def publish_bytes(path: Path, data: bytes) -> None:
    """Link a verified fsynced temporary file without replacing an existing artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".publish-")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError("immutable artifact collision") from None
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class RawRange:
    journal_id: str
    first_ordinal: int
    last_ordinal: int
    references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    dataset_id: DatasetId
    artifact_path: str
    sha256: str
    row_count: int
    capabilities: frozenset[str]
    instrument_id: str
    instrument_spec_hash: str
    origin: str
    schema_revision: str
    normalizer_revisions: tuple[str, ...]
    raw_ranges: tuple[RawRange, ...]
    engine_sequence_range: tuple[int, int] | None
    exchange_range: tuple[int, int] | None
    receive_range: tuple[int, int] | None
    availability_range: tuple[int, int]
    quality_flags: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()

    def to_bytes(self) -> bytes:
        value = asdict(self)
        value["capabilities"] = sorted(self.capabilities)
        return canonical_bytes(value)

    @classmethod
    def from_bytes(cls, data: bytes) -> DatasetManifest:
        obj = json.loads(data)
        for key in (
            "engine_sequence_range",
            "exchange_range",
            "receive_range",
            "availability_range",
        ):
            obj[key] = tuple(int(v) for v in obj[key]) if obj[key] is not None else None
        for key in ("normalizer_revisions", "quality_flags", "assumptions"):
            obj[key] = tuple(obj[key])
        obj["capabilities"] = frozenset(obj["capabilities"])
        obj["raw_ranges"] = tuple(
            RawRange(
                r["journal_id"],
                int(r["first_ordinal"]),
                int(r["last_ordinal"]),
                tuple(r["references"]),
            )
            for r in obj["raw_ranges"]
        )
        return cls(**obj)


def identified(manifest: DatasetManifest) -> DatasetManifest:
    digest = hashlib.sha256(replace(manifest, dataset_id=DatasetId("")).to_bytes()).hexdigest()
    return replace(manifest, dataset_id=DatasetId(digest))


def _content_capabilities(path: Path, manifest: DatasetManifest) -> frozenset[str]:
    """Capability evidence is the validated row schema/content, never self-declared metadata."""
    schemas = {
        "ohlcv-v1": {"bar_json", "start_ns", "end_ns", "available_ns"},
        "incoming-v1": {"source_ordinal", "incoming_json"},
        "canonical-v1": {
            "engine_seq",
            "event_id",
            "event_type",
            "raw_ref",
            "exchange_event_ns",
            "receive_wall_ns",
            "available_ns",
            "envelope_json",
            "payload",
        },
    }
    file = pq.ParquetFile(path)
    names = file.schema_arrow.names
    if (
        manifest.schema_revision not in schemas
        or len(names) != len(set(names))
        or set(names) != schemas[manifest.schema_revision]
    ):
        raise ValueError("unsupported artifact capability schema")
    capabilities: set[str] = set()
    event_capability = {
        "Trade": "TRADES",
        "BookSnapshot": "L2",
        "BookDelta": "L2",
        "Quote": "BBO",
        "BarClosed": "OHLCV",
        "MarkPrice": "MARK",
        "FundingRateAnnounced": "FUNDING",
    }
    previous_sequence, previous_available = 0, -1
    for batch in file.iter_batches(batch_size=1024):
        for row in cast(list[dict[str, object]], batch.to_pylist()):
            available = (
                canonical_int(row["available_ns"], minimum=0) if "available_ns" in row else 0
            )
            if manifest.schema_revision == "ohlcv-v1":
                bar = validate_payload("BarClosed", row["bar_json"])
                assert isinstance(bar, BarClosed)
                if (
                    canonical_int(row["start_ns"], minimum=0) != bar.start_ns
                    or canonical_int(row["end_ns"], minimum=0) != bar.end_ns
                    or available < bar.end_ns
                ):
                    raise ValueError("OHLCV artifact row disagrees with bar payload")
                capabilities.add("OHLCV")
            else:
                envelope = manifest.schema_revision == "canonical-v1"
                raw = row["envelope_json" if envelope else "incoming_json"]
                event = decode_market_event(raw, envelope=envelope)
                sequence = canonical_int(
                    row["engine_seq" if envelope else "source_ordinal"], minimum=1
                )
                if sequence <= previous_sequence or event.instrument_id != manifest.instrument_id:
                    raise ValueError("artifact source order or instrument mismatch")
                previous_sequence = sequence
                if envelope:
                    obj = json_object(raw)
                    exchange_ns = row["exchange_event_ns"]
                    if (
                        canonical_int(obj["engine_seq"], minimum=1) != sequence
                        or row["event_id"] != obj["event_id"]
                        or row["event_type"] != event.event_type
                        or row["raw_ref"] != event.raw_ref
                        or row["payload"] != event.payload
                        or (
                            canonical_int(exchange_ns, minimum=0)
                            if exchange_ns is not None
                            else None
                        )
                        != event.exchange_event_ns
                        or canonical_int(row["receive_wall_ns"], minimum=0) != event.receive_wall_ns
                        or available != event.available_ns
                    ):
                        raise ValueError("artifact row disagrees with canonical envelope")
                available = event.available_ns
                capabilities.update((event_capability[event.event_type], "RECEIVE_TIMESTAMPS"))
            if available < previous_available:
                raise ValueError("artifact availability regression")
            previous_available = available
    return frozenset(capabilities)


class Catalog:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "manifests").mkdir(exist_ok=True)

    def publish(self, manifest: DatasetManifest) -> DatasetId:
        if identified(manifest).dataset_id != manifest.dataset_id:
            raise ValueError("manifest identity mismatch")
        if not manifest.capabilities <= {cap.value for cap in Capability}:
            raise ValueError("unknown dataset capability")
        self._validate_artifact(manifest)
        publish_bytes(self.root / "manifests" / f"{manifest.dataset_id}.json", manifest.to_bytes())
        return manifest.dataset_id

    def _validate_artifact(self, manifest: DatasetManifest) -> Path:
        path = confined(self.root, manifest.artifact_path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest.sha256:
            raise ValueError("artifact hash mismatch")
        file = pq.ParquetFile(path)
        if file.metadata.num_rows != manifest.row_count or manifest.row_count < 1:
            raise ValueError("artifact row count mismatch")
        metadata = file.schema_arrow.metadata or {}
        if json.loads(metadata.get(b"quantdesk.capabilities", b"[]")) != sorted(
            manifest.capabilities
        ):
            raise ValueError("artifact capability metadata mismatch")
        if _content_capabilities(path, manifest) != manifest.capabilities:
            raise ValueError("manifest capabilities contradict validated artifact content")
        return path

    def get(self, dataset_id: str) -> DatasetManifest:
        if re.fullmatch(r"[a-f0-9]{64}", dataset_id) is None:
            raise ValueError("invalid dataset ID")
        manifest = DatasetManifest.from_bytes(
            confined(self.root, f"manifests/{dataset_id}.json").read_bytes()
        )
        if identified(manifest).dataset_id != dataset_id:
            raise ValueError("catalog manifest corruption")
        self._validate_artifact(manifest)
        return manifest

    def artifact(self, dataset_id: str) -> Path:
        manifest = self.get(dataset_id)
        return confined(self.root, manifest.artifact_path)

    def require(self, dataset_id: str, required: frozenset[str]) -> DatasetManifest:
        manifest = self.get(dataset_id)
        if not required <= manifest.capabilities:
            raise ValueError(
                f"missing dataset capabilities: {sorted(required - manifest.capabilities)}"
            )
        return manifest

    def list(self) -> tuple[DatasetManifest, ...]:
        return tuple(
            self.get(path.stem) for path in sorted((self.root / "manifests").glob("*.json"))
        )
