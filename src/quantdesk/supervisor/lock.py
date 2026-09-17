"""Exclusive OS file locking and durable ownership epochs for account directories (§16.2)."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from pathlib import Path
from types import TracebackType
from typing import IO, Any

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class AccountLockedError(Exception):
    """Raised when an account directory is already locked by another process."""

    def __init__(self, directory: Path, owner_pid: int | None = None, message: str = ""):
        self.directory = directory
        self.owner_pid = owner_pid
        super().__init__(
            message
            or f"Account directory {directory} is locked by another process (PID: {owner_pid})"
        )


class AccountLock:
    """Exclusive OS-level directory lock with durable ownership epoch (§16.2).

    Guarantees single-writer ownership of the account storage directory.
    Uses native OS locking primitives (msvcrt on Windows, fcntl on POSIX)
    and maintains an incrementing ownership epoch file.
    """

    def __init__(self, directory: Path | str, lock_filename: str = ".account.lock"):
        self.directory = Path(directory).resolve()
        self.lock_path = self.directory / lock_filename
        self.epoch_path = self.directory / "epoch.json"
        self._file: IO[bytes] | None = None
        self._acquired = False
        self._epoch: int = 0

    @property
    def is_acquired(self) -> bool:
        return self._acquired

    @property
    def epoch(self) -> int:
        return self._epoch

    def acquire(self) -> int:
        """Acquires exclusive OS lock and increments ownership epoch.

        Returns:
            The newly assigned ownership epoch integer.

        Raises:
            AccountLockedError: If directory is already locked by another process.
        """
        if self._acquired:
            return self._epoch

        self.directory.mkdir(parents=True, exist_ok=True)

        try:
            # Open for append/update without truncating
            self._file = open(self.lock_path, "a+b")  # noqa: SIM115
        except OSError as exc:
            raise AccountLockedError(self.directory, message=str(exc)) from exc

        # Attempt non-blocking exclusive OS lock
        try:
            if sys.platform == "win32":
                # Seek to start of file before locking
                self._file.seek(0)
                # Lock 1 byte with non-blocking exclusive lock
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            if self._file:
                with contextlib.suppress(OSError):
                    self._file.close()
                self._file = None
            owner_pid = self._read_owner_pid()
            raise AccountLockedError(self.directory, owner_pid=owner_pid) from exc

        self._acquired = True

        # Increment and persist durable ownership epoch
        self._epoch = self._advance_epoch()
        return self._epoch

    def release(self) -> None:
        """Releases the OS lock and closes file handle."""
        if not self._acquired or not self._file:
            return

        try:
            if sys.platform == "win32":
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                self._file.close()
            self._file = None
            self._acquired = False

    def _read_owner_pid(self) -> int | None:
        """Attempts to read the PID from epoch.json if available."""
        if not self.epoch_path.exists():
            return None
        try:
            data = json.loads(self.epoch_path.read_text(encoding="utf-8"))
            pid_val = data.get("owner_pid")
            return int(pid_val) if pid_val is not None else None
        except Exception:
            return None

    def _advance_epoch(self) -> int:
        """Reads current epoch, increments by 1, writes atomically."""
        current_epoch = 0
        if self.epoch_path.exists():
            try:
                data = json.loads(self.epoch_path.read_text(encoding="utf-8"))
                current_epoch = int(data.get("epoch", 0))
            except Exception:
                current_epoch = 0

        new_epoch = current_epoch + 1
        payload: dict[str, Any] = {
            "epoch": new_epoch,
            "owner_pid": os.getpid(),
            "acquired_at_ns": time.time_ns(),
            "directory": str(self.directory),
        }

        temp_path = self.directory / f".epoch-{os.getpid()}.tmp"
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_path.replace(self.epoch_path)
        return new_epoch

    def read_current_epoch(self) -> int:
        """Reads the currently stored epoch without mutating it."""
        if not self.epoch_path.exists():
            return 0
        try:
            data = json.loads(self.epoch_path.read_text(encoding="utf-8"))
            return int(data.get("epoch", 0))
        except Exception:
            return 0

    def __enter__(self) -> AccountLock:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.release()
