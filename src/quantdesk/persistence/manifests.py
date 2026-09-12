from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from quantdesk.core.events import canonical_bytes


def sync_directory(directory: Path) -> None:
    """POSIX rename durability; Windows uses fsynced files and same-volume rename.

    Python does not expose a portable Windows directory FlushFileBuffers handle.
    A failed/missing manifest is therefore fail-closed on reopening, never evidence
    that a frame was durable. The storage/OS must honor fsync for power-loss safety.
    """
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        sync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class RawRef:
    journal_id: str
    chunk_index: int
    start_offset: int
    end_offset: int
    frame_ordinal: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", self.journal_id):
            raise ValueError("invalid raw journal identity")
        if self.chunk_index < 0 or self.start_offset < 0:
            raise ValueError("invalid raw reference offset")
        if self.end_offset <= self.start_offset or self.frame_ordinal < 1:
            raise ValueError("invalid raw reference boundary")

    def __str__(self) -> str:
        return ":".join(
            map(
                str,
                (
                    self.journal_id,
                    self.chunk_index,
                    self.start_offset,
                    self.end_offset,
                    self.frame_ordinal,
                ),
            )
        )

    @classmethod
    def parse(cls, value: str) -> RawRef:
        journal_id, chunk, start, end, ordinal = value.split(":")
        return cls(journal_id, int(chunk), int(start), int(end), int(ordinal))


@dataclass(frozen=True, slots=True)
class DurableWatermark:
    journal_id: str
    chunk_index: int
    end_offset: int
    frame_ordinal: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", self.journal_id):
            raise ValueError("invalid watermark journal")
        if min(self.chunk_index, self.end_offset, self.frame_ordinal) < 0:
            raise ValueError("invalid durable watermark")

    def covers(self, ref: RawRef) -> bool:
        return (
            self.journal_id == ref.journal_id
            and ref.frame_ordinal <= self.frame_ordinal
            and (
                ref.chunk_index < self.chunk_index
                or (ref.chunk_index == self.chunk_index and ref.end_offset <= self.end_offset)
            )
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> DurableWatermark:
        value = json.loads(data)
        return cls(
            str(value["journal_id"]),
            int(value["chunk_index"]),
            int(value["end_offset"]),
            int(value["frame_ordinal"]),
        )

    def to_bytes(self) -> bytes:
        return canonical_bytes(self)


@dataclass(frozen=True, slots=True)
class ChunkManifest:
    journal_id: str
    chunk_index: int
    filename: str
    sha256: str
    previous_hash: str | None
    uncompressed_sha256: str
    uncompressed_bytes: int
    first_ordinal: int
    last_ordinal: int
    frame_count: int

    @classmethod
    def from_bytes(cls, data: bytes) -> ChunkManifest:
        value = json.loads(data)
        return cls(
            str(value["journal_id"]),
            int(value["chunk_index"]),
            str(value["filename"]),
            str(value["sha256"]),
            value["previous_hash"],
            str(value["uncompressed_sha256"]),
            int(value["uncompressed_bytes"]),
            int(value["first_ordinal"]),
            int(value["last_ordinal"]),
            int(value["frame_count"]),
        )
