"""Cross-process single-instance file locks.

The nightly pipeline occasionally gets triggered twice a few minutes apart
(overlapping scheduler fires). Concurrent runs of the same downloader race
on the same output files and double the anti-bot request budget, so each
expensive downloader guards its ``__main__`` entry with an OS-level lock
that is released automatically when the process exits — including on
crash/kill, so no stale-lock cleanup is ever needed.
"""
from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

LOCK_DIR = Path(tempfile.gettempdir()) / "oxpicious-trading-locks"


if os.name == "nt":
    import msvcrt

    def _lock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_fd(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_fd(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def single_instance_lock(name: str) -> Iterator[int]:
    """Hold an exclusive cross-process lock for ``name``.

    Raises ``RuntimeError`` when another process already holds the lock.
    The holding process's pid is written into the lock file (best-effort)
    for diagnostics.
    """
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCK_DIR / f"{name}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode())
            _lock_fd(fd)
        except OSError:
            raise RuntimeError(
                f"another instance of {name} is already running "
                f"(lock: {lock_path})"
            ) from None
        try:
            yield fd
        finally:
            try:
                _unlock_fd(fd)
            except OSError:
                pass  # the close below releases it regardless
    finally:
        os.close(fd)
