"""server_cluster.maintainer.backup — the robocopy mirror + its audit.

The stats-db backup's mechanics, split out of ``jobs.py`` (one class per
concern):

- :class:`RobocopyMirror` — ONE disk-speed mirror pass of the source
  tree onto the backup drive. The copier is ``robocopy.exe`` reached
  through the WSL interop (the maintainer resident is a WSL process;
  robocopy runs as the host's native Windows process), so the copy runs
  at native disk speed — ``/MT`` parallel workers, NO per-file
  throttling (the former 1s-per-file pause is gone; a 14 GB / 199k-file
  seed measured 178 MB/s vs the old ~days-long shutil+pause walk).
  Timestamps are preserved (``/COPY:DAT /DCOPY:DAT``) — that is what
  makes both the destination comparison and the /MAXAGE cutoff correct.
- :class:`BackupAudit` — the ``maintenance.src_data_backup`` face: the
  cutoff read (the latest OK batch's local date → robocopy ``/MAXAGE``,
  so only files newer than the last backup are candidates) and the
  one-row-per-batch insert. Reads go to the replica, the insert to the
  primary, all SQL rendered from ``database/sql_views`` /
  ``database/sql_writes`` action files.

A failed mirror is a ``backup_status = failed`` audit ROW (with the
counts the pass managed) — never a silent skip. The DB is allowed to
be down: the cutoff read then falls back to the FULL mirror and a
failed audit write degrades to a logged warning on the job's detail.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from _common.db_commons import (get_db_connection_async,
                                 get_read_db_connection_async)
from _common.labels import BACKUP_MODE_CODE, BACKUP_STATUS_CODE
from _common.sql_views import render_sql_view
from _common.sql_writes import render_sql_write

logger = logging.getLogger(__name__)

# The repo's operational timezone (the scheduler's fire-time convention);
# the batch's cutoff date is the batch start seen in THIS zone — the same
# wall the robocopy host's file mtimes live on.
LOCAL_TZ = ZoneInfo("Asia/Shanghai")

DEFAULT_MT_THREADS = 16
"""robocopy /MT worker threads — saturates the disk; the speed dial of
the disk-speed copy (16 measured ~178 MB/s on the seed pass)."""

DEFAULT_TIMEOUT_S = 4 * 3600.0
"""Hard cap on one robocopy pass — a stuck drive fails the batch
instead of hanging the maintainer's daily thread forever."""

ROBOCOPY_EXE = "robocopy.exe"
"""WSL interop resolves it on the Windows PATH (/mnt/c/WINDOWS/system32)."""

_RC_MISMATCH_BIT = 4   # exit 4-7: some files mismatched (present, differ)
_RC_FAILED_BIT = 8     # exit 8+: copy failures (16 fatal included)

_LATEST_OK_VIEW = "server_cluster/maintainer/backup/latest_ok_batch"
_INSERT_WRITE = "server_cluster/maintainer/backup/insert_batch"


class BackupBatchRecord(BaseModel):
    """One mirror batch, as the audit table stores it — the pydantic
    carrier between the mirror pass and the write boundary
    (``model_dump()`` feeds the sql_write params)."""

    src_root: str
    dest_root: str
    mode: int
    status: int
    num_files: int = 0
    num_skipped: int = 0
    num_bytes: int = 0
    detail: Optional[str] = None
    started_at: datetime
    duration_ms: int = 0


class RobocopyMirror:
    """One disk-speed mirror pass (``robocopy /E /MT``), additive — the
    destination is never pruned (a backup mirror must not delete)."""

    def __init__(self, source_root: Path, dest_root: Path,
                 mt_threads: int = DEFAULT_MT_THREADS,
                 timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self._src_win = self.windows_form(source_root)
        self._dest_win = self.windows_form(dest_root)
        self._mt_threads = mt_threads
        self._timeout_s = timeout_s

    @classmethod
    def windows_form(cls, path: Path) -> str:
        """The Windows path string of a repo path — robocopy's argv
        form. A posix ``/mnt/<drive>/...`` maps to ``<X>:\\...`` (the
        inverse of the drive mapping the job does for its own posix
        checks); a native-Windows path passes verbatim. Anything else
        (a plain ext4 path) is invisible to the Windows copier — fail
        named instead of copying nothing."""
        if os.name != "posix":
            return str(path)
        parts = Path(path).parts
        if len(parts) >= 3 and parts[1] == "mnt":
            return str(PureWindowsPath(f"{parts[2].upper()}:\\",
                                       *parts[3:]))
        raise ValueError(
            f"{path} is not a Windows-visible path (/mnt/<drive>/... "
            "under WSL) — robocopy cannot reach it")

    def run(self, max_age: Optional[date] = None) -> BackupBatchRecord:
        """One timed pass; ``max_age`` (a local date) rides as
        ``/MAXAGE`` so only files newer than the last backup are
        candidates — robocopy still compares each candidate against the
        destination, so a file copies only when genuinely missing or
        changed there."""
        started = datetime.now(timezone.utc)
        record = BackupBatchRecord(
            src_root=self._src_win, dest_root=self._dest_win,
            mode=(BACKUP_MODE_CODE["incremental"] if max_age is not None
                  else BACKUP_MODE_CODE["full"]),
            status=BACKUP_STATUS_CODE["failed"], started_at=started)
        cmd = self._build_command(max_age)
        logger.info("backup mirror pass: %s", " ".join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True,
                                  timeout=self._timeout_s)
        except subprocess.TimeoutExpired:
            record.detail = (f"robocopy timed out after "
                             f"{self._timeout_s:.0f}s — killed")
            return record
        except OSError as exc:
            record.detail = f"robocopy spawn failed: {exc}"
            return record
        took_ms = int((datetime.now(timezone.utc) - started)
                      .total_seconds() * 1000)
        copied, skipped, num_bytes = self._parse_summary(proc.stdout)
        record.num_files = copied
        record.num_skipped = skipped
        record.num_bytes = num_bytes
        record.duration_ms = took_ms
        record.status = self._status_from_exit(proc.returncode)
        if record.status != BACKUP_STATUS_CODE["ok"]:
            record.detail = (f"robocopy exit {proc.returncode}: "
                             f"{copied} copied, {skipped} skipped, "
                             f"{num_bytes} bytes")
        return record

    def _build_command(self, max_age: Optional[date]) -> list[str]:
        cmd = [ROBOCOPY_EXE, self._src_win, self._dest_win,
               # /E the whole tree; DAT timestamps preserved (the
               # dest-compare + /MAXAGE correctness); /MT the
               # disk-speed workers; /BYTES exact byte counts in the
               # summary; /NFL /NDL /NP quiet listing
               "/E", "/COPY:DAT", "/DCOPY:DAT",
               f"/MT:{self._mt_threads}", "/R:1", "/W:2", "/BYTES",
               "/NFL", "/NDL", "/NP"]
        if max_age is not None:
            cmd.append(f"/MAXAGE:{max_age:%Y%m%d}")
        return cmd

    @staticmethod
    def _parse_summary(raw: bytes | str) -> tuple[int, int, int]:
        """(copied, skipped, bytes_copied) from the summary table's
        Files / Bytes rows (columns: Total Copied Skipped Mismatch
        FAILED Extras; ``/BYTES`` keeps the counts raw). An unparseable
        summary yields zeros — the exit-code bits still decide the
        batch's status."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        files = re.search(r"^\s*Files\s*:\s*(\d+)\s+(\d+)\s+(\d+)",
                          raw, re.MULTILINE)
        byte_row = re.search(r"^\s*Bytes\s*:\s*(\d+)\s+(\d+)\s+(\d+)",
                             raw, re.MULTILINE)
        copied = int(files.group(2)) if files else 0
        skipped = int(files.group(3)) if files else 0
        num_bytes = int(byte_row.group(2)) if byte_row else 0
        return copied, skipped, num_bytes

    @staticmethod
    def _status_from_exit(exit_code: int) -> int:
        if exit_code >= _RC_FAILED_BIT:
            return BACKUP_STATUS_CODE["failed"]
        if exit_code & _RC_MISMATCH_BIT:
            return BACKUP_STATUS_CODE["partial"]
        return BACKUP_STATUS_CODE["ok"]


class BackupAudit:
    """The ``maintenance.src_data_backup`` face — the batch audit's
    cutoff read + insert. Sync face (the job's thread), asyncpg under a
    short-lived ``asyncio.run`` loop: no loop lives in a scheduler /
    threadpool worker thread."""

    def next_cutoff(self) -> Optional[date]:
        """The incremental cutoff: the latest OK batch's start converted
        to the local date (robocopy /MAXAGE's day granularity). ``None``
        = no OK batch yet — the next pass mirrors fully."""
        return asyncio.run(self._latest_ok_date())

    def insert(self, record: BackupBatchRecord) -> None:
        asyncio.run(self._insert(record))

    @staticmethod
    async def _fetch_latest_ok() -> Optional[dict]:
        view = render_sql_view(
            _LATEST_OK_VIEW, ok_status=BACKUP_STATUS_CODE["ok"])
        conn = await get_read_db_connection_async()
        try:
            row = await conn.fetchrow(view.sql, *view.params)
        finally:
            await conn.close()
        return dict(row) if row is not None else None

    async def _latest_ok_date(self) -> Optional[date]:
        row = await self._fetch_latest_ok()
        if row is None:
            return None
        started: datetime = row["started_at"]
        return started.astimezone(LOCAL_TZ).date()

    async def _insert(self, record: BackupBatchRecord) -> None:
        write = render_sql_write(
            _INSERT_WRITE, record.src_root, record.dest_root,
            record.mode, record.status, record.num_files,
            record.num_skipped, record.num_bytes, record.detail,
            record.started_at, record.duration_ms)
        conn = await get_db_connection_async()
        try:
            await conn.execute(write.sql, *write.params)
        finally:
            await conn.close()
