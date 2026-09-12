from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import time
import uuid
import zlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from threading import get_ident
from types import TracebackType
from typing import BinaryIO, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import zstandard
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from quantdesk.core.events import canonical_bytes
from quantdesk.persistence.db import FileOwnership, WriterOwnershipError
from quantdesk.persistence.manifests import (
    ChunkManifest,
    DurableWatermark,
    RawRef,
    atomic_write,
    sync_directory,
)

_MAGIC = b"QDJ1"
_HEADER = struct.Struct(">4sIQ")
_CRC = struct.Struct(">I")
_MAX_METADATA = 64 * 1024
_MAX_PAYLOAD = 64 * 1024 * 1024
_SECRET_NAMES = frozenset(
    {
        "authorization",
        "proxyauthorization",
        "cookie",
        "setcookie",
        "apikey",
        "apisecret",
        "secret",
        "secretkey",
        "signature",
        "sign",
        "passphrase",
        "password",
        "token",
        "accesstoken",
        "refreshtoken",
        "accesskey",
        "accesssign",
        "accesspassphrase",
        "privatekey",
        "session",
        "sessionid",
        "credential",
        "credentials",
    }
)


class JournalCorruption(ValueError):
    """History cannot be trusted; halt normalization and require recovery."""


class CaptureRejected(ValueError):
    """Transport capture contained authentication material or lacked encryption."""


class JournalCipher(Protocol):
    @property
    def key_id(self) -> str: ...

    def encrypt(self, plaintext: bytes, associated_data: bytes) -> bytes: ...

    def decrypt(self, ciphertext: bytes, associated_data: bytes) -> bytes: ...


class AESGCMCipher:
    """Injected key boundary. The caller loads a key from the OS secret store.

    Only key_id is persisted. Nonces are fresh transport randomness, never core
    decision input. Key acquisition/rotation/recovery belongs to the supervisor.
    """

    def __init__(self, key_id: str, key: bytes) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", key_id):
            raise ValueError("invalid key identifier")
        self._key_id = key_id
        self._cipher = AESGCM(key)

    @property
    def key_id(self) -> str:
        return self._key_id

    def encrypt(self, plaintext: bytes, associated_data: bytes) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._cipher.encrypt(nonce, plaintext, associated_data)

    def decrypt(self, ciphertext: bytes, associated_data: bytes) -> bytes:
        try:
            return self._cipher.decrypt(ciphertext[:12], ciphertext[12:], associated_data)
        except (InvalidTag, ValueError) as exc:
            raise JournalCorruption("private journal authentication failed") from exc


@dataclass(frozen=True, slots=True)
class RawFrame:
    payload: bytes
    venue: str
    environment: str
    transport: str
    receive_wall_ns: int
    receive_monotonic_ns: int
    connection_epoch: str
    message_ordinal: int
    private: bool = False
    url: str | None = None
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    complete_frames: int
    truncated_bytes: int


def _secret_name(name: str) -> bool:
    return re.sub(r"[^a-z0-9]", "", name.lower()) in _SECRET_NAMES


@dataclass(frozen=True, slots=True)
class _JSONNumber:
    """Original JSON number lexeme; capture never rounds or quotes it."""

    token: str


def _redacted_json(value: object) -> str:
    # This is a transport serializer, not canonical core serialization: numeric
    # JSON tokens (including exponent spelling and large IDs) retain their type
    # and exact representation before the normalizer sees them.
    if isinstance(value, _JSONNumber):
        return value.token
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=False) + ":" + _redacted_json(child)
                for key, child in value.items()
            )
            + "}"
        )
    if isinstance(value, list):
        return "[" + ",".join(_redacted_json(child) for child in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _strip_secrets(value: object) -> tuple[object, bool]:
    if isinstance(value, dict):
        cleaned: dict[str, object] = {}
        changed = False
        for key, child in value.items():
            if _secret_name(str(key)):
                changed = True
            else:
                cleaned[str(key)], child_changed = _strip_secrets(child)
                changed |= child_changed
        return cleaned, changed
    if isinstance(value, list):
        items = [_strip_secrets(child) for child in value]
        return [item for item, _ in items], any(changed for _, changed in items)
    return value, False


def sanitize_capture(frame: RawFrame) -> RawFrame:
    """Strip credentials before framing/encryption; preserve safe body bytes exactly.

    Capture excludes *all* transport headers. Auth/login messages are not market
    or account evidence and are rejected in full. Opaque payloads are accepted as
    binary market messages; adapters must never classify opaque auth as data.
    """
    if not isinstance(frame.payload, bytes):
        raise TypeError("raw payload must be bytes")
    if frame.message_ordinal < 0 or min(frame.receive_wall_ns, frame.receive_monotonic_ns) < 0:
        raise ValueError("invalid raw receipt metadata")
    payload = frame.payload
    try:
        parsed = json.loads(payload, parse_int=_JSONNumber, parse_float=_JSONNumber)
    except (ValueError, UnicodeDecodeError):
        # Plain form authentication bodies must not evade JSON redaction.
        if re.search(
            rb"(?i)(api[_-]?key|signature|password|passphrase|token|authorization)\s*[=:]", payload
        ):
            raise CaptureRejected("opaque authentication payload is not journalable") from None
    else:
        if isinstance(parsed, dict) and any(
            str(parsed.get(key, "")).lower() in {"login", "auth", "authenticate", "authentication"}
            for key in ("op", "method", "action", "type")
        ):
            raise CaptureRejected("authentication frames are excluded from journals")
        safe, changed = _strip_secrets(parsed)
        if changed:
            payload = _redacted_json(safe).encode("utf-8")
    url = frame.url
    if url is not None:
        split = urlsplit(url)
        if split.username is not None or split.password is not None:
            raise CaptureRejected("URL credentials are excluded from journals")
        query = [
            (key, value)
            for key, value in parse_qsl(split.query, keep_blank_values=True)
            if not _secret_name(key)
        ]
        url = urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), ""))
    return replace(frame, payload=payload, headers=(), url=url)


@dataclass(frozen=True, slots=True)
class _StoredFrame:
    ref: RawRef
    metadata: bytes
    payload: bytes


def _has_later_complete_frame(data: bytes, start: int, minimum_ordinal: int) -> bool:
    """Recognize framing evidence, never a magic string inside a payload alone."""
    candidate = data.find(_MAGIC, start)
    while candidate >= 0:
        if len(data) - candidate >= _HEADER.size:
            _, metadata_length, payload_length = _HEADER.unpack_from(data, candidate)
            end = candidate + _HEADER.size + metadata_length + payload_length + _CRC.size
            if (
                metadata_length <= _MAX_METADATA
                and payload_length <= _MAX_PAYLOAD
                and end <= len(data)
                and zlib.crc32(data[candidate : end - _CRC.size])
                == _CRC.unpack_from(data, end - _CRC.size)[0]
            ):
                metadata_start = candidate + _HEADER.size
                try:
                    metadata = json.loads(data[metadata_start : metadata_start + metadata_length])
                    if (
                        isinstance(metadata, dict)
                        and int(metadata["frame_ordinal"]) >= minimum_ordinal
                    ):
                        return True
                except (ValueError, TypeError, KeyError):
                    pass
        candidate = data.find(_MAGIC, candidate + len(_MAGIC))
    return False


def _scan(
    data: bytes, journal_id: str, chunk: int, first_ordinal: int, *, allow_tail: bool
) -> tuple[list[_StoredFrame], int]:
    frames: list[_StoredFrame] = []
    offset = 0
    while offset < len(data):
        remaining = len(data) - offset
        if remaining < _HEADER.size:
            if not allow_tail:
                raise JournalCorruption("incomplete sealed header")
            break
        magic, metadata_length, payload_length = _HEADER.unpack_from(data, offset)
        if magic != _MAGIC or metadata_length > _MAX_METADATA or payload_length > _MAX_PAYLOAD:
            raise JournalCorruption("invalid journal frame header")
        end = offset + _HEADER.size + metadata_length + payload_length + _CRC.size
        if end > len(data):
            if not allow_tail or _has_later_complete_frame(
                data, offset + _HEADER.size, first_ordinal + len(frames) + 1
            ):
                raise JournalCorruption("incomplete interior or sealed journal frame")
            break
        if zlib.crc32(data[offset : end - _CRC.size]) != _CRC.unpack_from(data, end - _CRC.size)[0]:
            raise JournalCorruption("journal CRC mismatch in complete frame")
        metadata_start = offset + _HEADER.size
        metadata = data[metadata_start : metadata_start + metadata_length]
        ordinal = first_ordinal + len(frames)
        try:
            decoded = json.loads(metadata)
            if int(decoded["frame_ordinal"]) != ordinal:
                raise JournalCorruption("raw frame ordinal discontinuity")
        except (ValueError, KeyError, TypeError) as exc:
            raise JournalCorruption("invalid journal metadata") from exc
        frames.append(
            _StoredFrame(
                RawRef(journal_id, chunk, offset, end, ordinal),
                metadata,
                data[metadata_start + metadata_length : end - _CRC.size],
            )
        )
        offset = end
    return frames, len(data) - offset


class RawJournal:
    """Single-writer raw journal with explicit durability acknowledgement.

    Incomplete unacknowledged active suffixes can be truncated. A bad complete
    frame, any sealed corruption, or damage at/before a durable watermark is fatal.
    Sealed publication writes compressed data, then its hash manifest; the active
    source survives until both are fsynced/published, making each crash cut safe.
    """

    def __init__(
        self,
        directory: Path,
        *,
        cipher: JournalCipher | None = None,
        chunk_target_bytes: int = 32 * 1024 * 1024,
        chunk_target_seconds: float = 5.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if chunk_target_bytes < 1 or chunk_target_seconds <= 0:
            raise ValueError("chunk targets must be positive")
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._ownership = FileOwnership(self.directory / ".writer.lock")
        self._thread_id = get_ident()
        self._cipher = cipher
        self._chunk_target_bytes = chunk_target_bytes
        self._chunk_target_seconds = chunk_target_seconds
        self._monotonic = monotonic
        self._opened_at = monotonic()
        self._failed = False
        self._closed = False
        try:
            self._initialize()
        except BaseException:
            if hasattr(self, "_stream"):
                self._stream.close()
            self._ownership.close()
            raise

    def _assert_owner(self) -> None:
        if get_ident() != self._thread_id:
            raise WriterOwnershipError("raw journal called from another writer thread")

    def _initialize(self) -> None:
        identity_path = self.directory / "journal.json"
        watermark_path = self.directory / "watermark.json"
        if not identity_path.exists():
            if any(self.directory.glob("chunk-*")) or watermark_path.exists():
                raise JournalCorruption("missing journal identity")
            journal_id = uuid.uuid4().hex
            atomic_write(identity_path, canonical_bytes({"journal_id": journal_id, "version": 1}))
            # Establish durability metadata before any active file can accept
            # writes. Missing metadata thereafter is never a zero-watermark hint.
            atomic_write(watermark_path, DurableWatermark(journal_id, 0, 0, 0).to_bytes())
        elif not watermark_path.is_file():
            raise JournalCorruption("established journal is missing its durable watermark")
        try:
            identity = json.loads(identity_path.read_bytes())
            self.journal_id = str(identity["journal_id"])
            if identity["version"] != 1:
                raise JournalCorruption("unsupported raw journal version")
            self.durable_watermark = DurableWatermark.from_bytes(watermark_path.read_bytes())
        except (ValueError, KeyError, TypeError) as exc:
            raise JournalCorruption("invalid journal identity/watermark") from exc
        if self.durable_watermark.journal_id != self.journal_id:
            raise JournalCorruption("journal watermark identity mismatch")
        self._manifests: list[ChunkManifest] = []
        self._active_frames: dict[int, _StoredFrame] = {}
        self._frame_count = 0
        self._has_private = False
        previous_hash = None
        for index, manifest_path in enumerate(sorted(self.directory.glob("chunk-*.json"))):
            manifest = ChunkManifest.from_bytes(manifest_path.read_bytes())
            if (
                manifest.journal_id != self.journal_id
                or manifest.chunk_index != index
                or manifest.filename != f"chunk-{index:08d}.zst"
                or manifest.previous_hash != previous_hash
                or manifest.first_ordinal != self._frame_count + 1
            ):
                raise JournalCorruption("chunk manifest chain discontinuity")
            compressed = (self.directory / manifest.filename).read_bytes()
            if hashlib.sha256(compressed).hexdigest() != manifest.sha256:
                raise JournalCorruption("sealed chunk hash mismatch")
            try:
                data = zstandard.ZstdDecompressor().decompress(
                    compressed, max_output_size=manifest.uncompressed_bytes
                )
            except zstandard.ZstdError as exc:
                raise JournalCorruption("sealed chunk decompression failed") from exc
            if (
                len(data) != manifest.uncompressed_bytes
                or hashlib.sha256(data).hexdigest() != manifest.uncompressed_sha256
            ):
                raise JournalCorruption("sealed chunk content mismatch")
            frames, _ = _scan(
                data, self.journal_id, index, manifest.first_ordinal, allow_tail=False
            )
            if (
                len(frames) != manifest.frame_count
                or not frames
                or frames[-1].ref.frame_ordinal != manifest.last_ordinal
            ):
                raise JournalCorruption("sealed frame count mismatch")
            self._frame_count = manifest.last_ordinal
            self._has_private |= any(json.loads(stored.metadata)["private"] for stored in frames)
            self._manifests.append(manifest)
            previous_hash = manifest.sha256
            leftover_active = self.directory / f"chunk-{index:08d}.active"
            if leftover_active.exists():
                if leftover_active.read_bytes() != data:
                    raise JournalCorruption("published chunk disagrees with active source")
                leftover_active.unlink()
        self._chunk_index = len(self._manifests)
        self.active_path = self.directory / f"chunk-{self._chunk_index:08d}.active"
        extras = set(self.directory.glob("*.active")) - {self.active_path}
        if extras:
            raise JournalCorruption("unexpected active chunk")
        data = self.active_path.read_bytes() if self.active_path.exists() else b""
        frames, truncated = _scan(
            data, self.journal_id, self._chunk_index, self._frame_count + 1, allow_tail=True
        )
        self._active_frames.update((stored.ref.frame_ordinal, stored) for stored in frames)
        self._frame_count += len(frames)
        self._has_private |= any(json.loads(stored.metadata)["private"] for stored in frames)
        durable = self.durable_watermark
        if durable.frame_ordinal:
            last_durable = next(
                (
                    stored
                    for stored in self._chunk_frames(durable.chunk_index)
                    if stored.ref.frame_ordinal == durable.frame_ordinal
                ),
                None,
            )
            # A synced new empty chunk's last durable frame is in its predecessor.
            if last_durable is None and durable.end_offset == 0 and durable.chunk_index > 0:
                last_durable = next(
                    (
                        stored
                        for stored in self._chunk_frames(durable.chunk_index - 1)
                        if stored.ref.frame_ordinal == durable.frame_ordinal
                    ),
                    None,
                )
            if last_durable is None or not durable.covers(last_durable.ref):
                raise JournalCorruption("durable journal frame is missing")
            if durable.chunk_index > self._chunk_index or (
                durable.chunk_index == self._chunk_index
                and durable.end_offset > len(data) - truncated
            ):
                raise JournalCorruption("durable raw watermark beyond recovered data")
        self.recovery = RecoveryResult(self._frame_count, truncated)
        self._stream: BinaryIO = self.active_path.open("a+b", buffering=0)
        if truncated:
            self._stream.truncate(len(data) - truncated)
            self._stream.flush()
            os.fsync(self._stream.fileno())
        self._stream.seek(0, os.SEEK_END)

    def append(self, frame: RawFrame) -> RawRef:
        self._assert_owner()
        if self._failed or self._closed:
            raise RuntimeError("journal writer is unavailable after failure/close")
        safe = sanitize_capture(frame)
        if safe.private and self._cipher is None:
            raise CaptureRejected("private journal requires an injected encryption key")
        ordinal = self._frame_count + 1
        metadata = canonical_bytes(
            {
                "venue": safe.venue,
                "environment": safe.environment,
                "transport": safe.transport,
                "receive_wall_ns": safe.receive_wall_ns,
                "receive_monotonic_ns": safe.receive_monotonic_ns,
                "connection_epoch": safe.connection_epoch,
                "message_ordinal": safe.message_ordinal,
                "private": safe.private,
                "url": safe.url,
                "frame_ordinal": ordinal,
                "key_id": self._cipher.key_id if safe.private and self._cipher else None,
            }
        )
        payload = (
            self._cipher.encrypt(safe.payload, metadata)
            if safe.private and self._cipher
            else safe.payload
        )
        if len(metadata) > _MAX_METADATA or len(payload) > _MAX_PAYLOAD:
            raise ValueError("raw frame exceeds bounded size")
        encoded = _HEADER.pack(_MAGIC, len(metadata), len(payload)) + metadata + payload
        encoded += _CRC.pack(zlib.crc32(encoded))
        start = self._stream.tell()
        try:
            if self._stream.write(encoded) != len(encoded):
                raise OSError("incomplete journal write")
        except BaseException:
            self._failed = True
            raise
        ref = RawRef(self.journal_id, self._chunk_index, start, start + len(encoded), ordinal)
        self._active_frames[ordinal] = _StoredFrame(ref, metadata, payload)
        self._frame_count = ordinal
        self._has_private |= safe.private
        if (
            ref.end_offset >= self._chunk_target_bytes
            or self._monotonic() - self._opened_at >= self._chunk_target_seconds
        ):
            self.seal()
        elif safe.private:
            self.sync()
        return ref

    def sync(self) -> DurableWatermark:
        self._assert_owner()
        if self._failed or self._closed:
            raise RuntimeError("journal writer is unavailable after failure/close")
        try:
            self._stream.flush()
            os.fsync(self._stream.fileno())
            watermark = DurableWatermark(
                self.journal_id, self._chunk_index, self._stream.tell(), self._frame_count
            )
            atomic_write(self.directory / "watermark.json", watermark.to_bytes())
        except BaseException:
            self._failed = True
            raise
        self.durable_watermark = watermark
        return watermark

    def seal(self) -> ChunkManifest | None:
        watermark = self.sync()
        if watermark.end_offset == 0:
            return None
        try:
            data = self.active_path.read_bytes()
            compressed = zstandard.ZstdCompressor(level=3).compress(data)
            filename = f"chunk-{self._chunk_index:08d}.zst"
            first = (self._manifests[-1].last_ordinal + 1) if self._manifests else 1
            manifest = ChunkManifest(
                self.journal_id,
                self._chunk_index,
                filename,
                hashlib.sha256(compressed).hexdigest(),
                self._manifests[-1].sha256 if self._manifests else None,
                hashlib.sha256(data).hexdigest(),
                len(data),
                first,
                self._frame_count,
                self._frame_count - first + 1,
            )
            atomic_write(self.directory / filename, compressed)
            atomic_write(
                self.directory / f"chunk-{self._chunk_index:08d}.json", canonical_bytes(manifest)
            )
            self._manifests.append(manifest)
            self._active_frames.clear()
            self._stream.close()
            self.active_path.unlink()
            sync_directory(self.directory)
            self._chunk_index += 1
            self.active_path = self.directory / f"chunk-{self._chunk_index:08d}.active"
            self._stream = self.active_path.open("a+b", buffering=0)
            self._opened_at = self._monotonic()
        except BaseException:
            self._failed = True
            raise
        return manifest

    def _chunk_frames(self, chunk_index: int) -> tuple[_StoredFrame, ...]:
        if chunk_index == self._chunk_index:
            return tuple(self._active_frames.values())
        if not 0 <= chunk_index < len(self._manifests):
            raise JournalCorruption("missing raw chunk")
        manifest = self._manifests[chunk_index]
        compressed = (self.directory / manifest.filename).read_bytes()
        if hashlib.sha256(compressed).hexdigest() != manifest.sha256:
            raise JournalCorruption("sealed chunk hash mismatch")
        try:
            data = zstandard.ZstdDecompressor().decompress(
                compressed, max_output_size=manifest.uncompressed_bytes
            )
        except zstandard.ZstdError as exc:
            raise JournalCorruption("sealed chunk decompression failed") from exc
        frames, _ = _scan(
            data, self.journal_id, chunk_index, manifest.first_ordinal, allow_tail=False
        )
        return tuple(frames)

    def read(self, ref: RawRef) -> RawFrame:
        self._assert_owner()
        stored = next(
            (
                stored
                for stored in self._chunk_frames(ref.chunk_index)
                if stored.ref.frame_ordinal == ref.frame_ordinal
            ),
            None,
        )
        if stored is None or stored.ref != ref:
            raise ValueError("unknown raw reference")
        metadata = json.loads(stored.metadata)
        payload = stored.payload
        if metadata["private"]:
            if self._cipher is None or self._cipher.key_id != metadata["key_id"]:
                raise JournalCorruption("private journal decryption key is unavailable")
            payload = self._cipher.decrypt(payload, stored.metadata)
        return RawFrame(
            payload,
            metadata["venue"],
            metadata["environment"],
            metadata["transport"],
            int(metadata["receive_wall_ns"]),
            int(metadata["receive_monotonic_ns"]),
            metadata["connection_epoch"],
            int(metadata["message_ordinal"]),
            bool(metadata["private"]),
            metadata["url"],
        )

    def manifests(self) -> tuple[ChunkManifest, ...]:
        return tuple(self._manifests)

    def references(self) -> tuple[RawRef, ...]:
        self._assert_owner()
        return tuple(
            stored.ref
            for index in range(self._chunk_index + 1)
            for stored in self._chunk_frames(index)
        )

    @property
    def has_private_frames(self) -> bool:
        return self._has_private

    def close(self) -> None:
        self._assert_owner()
        # Close deliberately does not acknowledge durability. Only sync can do so.
        if not self._closed:
            self._stream.close()
            self._ownership.close()
            self._closed = True

    def __enter__(self) -> RawJournal:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
