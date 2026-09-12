from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import NewType

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from quantdesk.core.events import canonical_bytes
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
        publish_bytes(self.root / "manifests" / f"{manifest.dataset_id}.json", manifest.to_bytes())
        return manifest.dataset_id

    def get(self, dataset_id: str) -> DatasetManifest:
        if re.fullmatch(r"[a-f0-9]{64}", dataset_id) is None:
            raise ValueError("invalid dataset ID")
        manifest = DatasetManifest.from_bytes(
            confined(self.root, f"manifests/{dataset_id}.json").read_bytes()
        )
        if identified(manifest).dataset_id != dataset_id:
            raise ValueError("catalog manifest corruption")
        return manifest

    def artifact(self, dataset_id: str) -> Path:
        manifest = self.get(dataset_id)
        path = confined(self.root, manifest.artifact_path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest.sha256:
            raise ValueError("dataset artifact corruption")
        return path

    def require(self, dataset_id: str, required: frozenset[str]) -> DatasetManifest:
        manifest = self.get(dataset_id)
        if not required <= manifest.capabilities:
            raise ValueError(
                f"missing dataset capabilities: {sorted(required - manifest.capabilities)}"
            )
        self.artifact(dataset_id)
        return manifest

    def list(self) -> tuple[DatasetManifest, ...]:
        return tuple(
            self.get(path.stem) for path in sorted((self.root / "manifests").glob("*.json"))
        )
