from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from threading import get_ident
from types import TracebackType
from typing import BinaryIO

from quantdesk.persistence.migrations import migrate


class WriterOwnershipError(RuntimeError):
    pass


class FileOwnership:
    """Same-host nonblocking OS lock, released by the OS on process death.

    Every writer must acquire this lock. This is not a cross-host gateway fence;
    the supervisor owns account fencing and confirms previous process exit.
    """

    def __init__(self, path: Path) -> None:
        self._stream: BinaryIO = path.open("a+b")
        try:
            if path.stat().st_size == 0:
                self._stream.write(b"\0")
                self._stream.flush()
            self._stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._stream.close()
            raise WriterOwnershipError(f"writer already owns {path}") from exc

    def close(self) -> None:
        self._stream.close()


class Database:
    """One SQLite writer connection per account directory, owned by one thread.

    API/gateway readers must use independent read-only connections. Projections
    and audit tables may only be changed by the owning engine's transitions.
    """

    def __init__(
        self, path: Path, *, connection_factory: type[sqlite3.Connection] = sqlite3.Connection
    ) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ownership = FileOwnership(self.path.with_suffix(self.path.suffix + ".writer.lock"))
        self._thread_id = get_ident()
        try:
            self.connection = sqlite3.connect(
                self.path, isolation_level=None, factory=connection_factory
            )
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.execute("PRAGMA foreign_keys=ON")
            migrate(self.connection)
        except BaseException:
            if hasattr(self, "connection"):
                self.connection.close()
            self._ownership.close()
            raise

    def assert_owner(self) -> None:
        if get_ident() != self._thread_id:
            raise WriterOwnershipError("account writer called from another thread")

    def close(self) -> None:
        self.assert_owner()
        self.connection.close()
        self._ownership.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def open_reader(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None
    )
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection
