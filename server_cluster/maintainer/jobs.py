"""server_cluster.maintainer.jobs — the maintenance job catalogue.

One class per job (the repo's class-first preference — a new job is a
:class:`MaintenanceJob` subclass, never a branch). The base carries the
typed contract: an identity, a human description, and a ``run()``
returning the pydantic :class:`JobResult` the API surfaces. The log
cleanup walks ``logs/`` — date-named directories (``YYYY-MM-DD/``, the
``process_manager._log_path_for`` contract) older than the retention
window go away whole; loose files at the root go by mtime.
"""
from __future__ import annotations

import logging
import os
import shutil
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath

from pydantic import BaseModel, Field

from _common.labels import (BACKUP_MODE_LABEL, BACKUP_STATUS_CODE,
                            BACKUP_STATUS_LABEL)
from server_cluster.maintainer.backup import (BackupAudit,
                                              BackupBatchRecord,
                                              DEFAULT_MT_THREADS,
                                              RobocopyMirror)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_ROOT = _REPO_ROOT / "logs"
TEMPS_ROOT = _REPO_ROOT / "temps"

DEFAULT_RETENTION_DAYS = 30
DEFAULT_BACKUP_DEST = Path(r"D:\oxpicious-stats-db-backup")


class BackupFailedError(RuntimeError):
    """The mirror batch failed — raised AFTER its audit row is written,
    so the job result carries ``ok=False`` with the batch's own detail
    line and the audit trail still records what the pass managed."""


class JobResult(BaseModel):
    """One job pass, as the API returns it."""

    job: str
    started_at: datetime
    duration_s: float
    ok: bool
    detail: str = Field(default="", description="One human line — what "
                        "the pass deleted / why it failed.")
    deleted: list[str] = Field(default_factory=list,
                               description="Paths removed this pass.")


class MaintenanceJob(ABC):
    """One maintenance routine the scheduler fires on schedule (and the
    API on demand). Subclasses set ``name`` and implement ``_run``."""

    name: str = "job"
    description: str = ""
    scheduled: bool = True
    """Whether the shared ``MaintenanceScheduler`` fires this job on the
    daily maintenance pass; jobs with their own dedicated thread (the
    07:00 fleet bring-up) set False — they stay listed on ``GET /jobs``
    and run on demand, just not on the scheduler's pass."""

    def __init__(self) -> None:
        self.last_result: JobResult | None = None

    def run(self) -> JobResult:
        """One timed, guarded pass — a failed job is logged and returns
        ``ok=False``, never takes the server down."""
        started = datetime.now(timezone.utc)
        try:
            deleted, detail = self._run()
            result = JobResult(
                job=self.name, started_at=started,
                duration_s=(datetime.now(timezone.utc) - started)
                .total_seconds(),
                ok=True, detail=detail, deleted=deleted)
        except Exception as exc:  # noqa: BLE001 — a job must never
            logger.exception("maintenance job %s failed", self.name)
            result = JobResult(
                job=self.name, started_at=started,
                duration_s=(datetime.now(timezone.utc) - started)
                .total_seconds(),
                ok=False, detail=f"{type(exc).__name__}: {exc}")
        self.last_result = result
        return result

    @abstractmethod
    def _run(self) -> tuple[list[str], str]:
        """The job body: the removed paths + one summary line."""


class LogCleanupJob(MaintenanceJob):
    """Delete ``logs/`` entries older than the retention window.

    Date-named directories (``YYYY-MM-DD``) are removed whole when the
    directory date is before the cutoff; any other file at the log root
    goes by mtime. The active day's directory and anything younger
    always survive."""

    name = "log_cleanup"
    description = "Delete logs/ files and date dirs older than one month."

    def __init__(self, retention_days: int = DEFAULT_RETENTION_DAYS,
                 log_root: Path | None = None) -> None:
        super().__init__()
        self.retention_days = retention_days
        self._log_root = log_root or LOG_ROOT

    def _run(self) -> tuple[list[str], str]:
        cutoff_date = date.today() - timedelta(days=self.retention_days)
        cutoff_ts = cutoff_date.strftime("%Y-%m-%d")
        deleted: list[str] = []
        if not self._log_root.is_dir():
            return deleted, f"no logs dir at {self._log_root}"
        for entry in sorted(self._log_root.iterdir()):
            if self._is_stale(entry, cutoff_date):
                self._remove(entry)
                deleted.append(str(entry))
        freed = f"{len(deleted)} entr({'y' if len(deleted) == 1 else 'ies'})"
        return deleted, (f"purged {freed} older than {cutoff_ts} "
                         f"(retention {self.retention_days}d)")

    def _is_stale(self, entry: Path, cutoff: date) -> bool:
        try:
            date_dir = date.fromisoformat(entry.name)
        except ValueError:
            date_dir = None
        if date_dir is not None:
            return date_dir < cutoff
        # Non-date entry (loose file) — decide by its own mtime.
        try:
            mtime = date.fromtimestamp(entry.stat().st_mtime)
        except OSError:
            return False
        return mtime < cutoff

    def _remove(self, entry: Path) -> None:
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            try:
                entry.unlink()
            except OSError as exc:
                logger.warning("could not remove %s: %r", entry, exc)


class StatsDbBackupJob(MaintenanceJob):
    """Mirror ``temps/`` to the stats-db backup drive — one robocopy
    batch, one audit row.

    The copier is :class:`~server_cluster.maintainer.backup.
    RobocopyMirror` (``robocopy.exe`` via the WSL interop): a disk-speed
    ``/MT`` parallel pass — NO per-file pause — that preserves
    timestamps, so the destination comparison and the incremental
    cutoff both work. Before copying, the latest OK batch's local date
    is read from ``maintenance.src_data_backup`` and handed to robocopy
    as ``/MAXAGE``: only files newer than the last backup are
    candidates (robocopy still compares each against the mirror, so
    only genuinely missing/changed files copy). No OK batch yet — a
    failed batch, or the DB unreachable — falls back to the FULL mirror
    (self-healing). One audit row per batch records the copied/skipped
    counts, byte volume and outcome; the audit row write is best-effort
    (a DB outage degrades to a detail note, the backup still runs).

    The configured dest is the drive's Windows path, but the cluster
    residents run under WSL where ``D:\\...`` parses as a RELATIVE path —
    a pass would silently build the mirror inside the process cwd (the
    repo). :meth:`_resolve_dest` maps the drive anchor to
    ``/mnt/<drive>`` for the job's own checks; the mirror's copier gets
    the Windows form back. When the drive mount itself is missing the
    pass fails with that detail instead of recreating files somewhere
    unintended."""

    name = "stats_db_backup"
    description = ("Robocopy-mirror temps/ to D:\\oxpicious-stats-db-"
                   "backup at disk speed (/MT, no per-file pause); one "
                   "audit row per batch in maintenance.src_data_backup; "
                   "incremental from the latest OK batch (/MAXAGE).")

    def __init__(self, source_root: Path | None = None,
                 dest_root: Path | None = None,
                 mt_threads: int = DEFAULT_MT_THREADS) -> None:
        super().__init__()
        self._source_root = source_root or TEMPS_ROOT
        self._dest_root = self._resolve_dest(
            dest_root or DEFAULT_BACKUP_DEST)
        self._mirror = RobocopyMirror(self._source_root, self._dest_root,
                                      mt_threads=mt_threads)
        self._audit = BackupAudit()

    def _run(self) -> tuple[list[str], str]:
        if not self._source_root.is_dir():
            return [], f"no temps dir at {self._source_root}"
        mount = self._mount_root(self._dest_root)
        if mount is not None and not mount.is_dir():
            return [], (f"backup dest drive not mounted: {mount} is "
                        "missing — skipped the mirror pass")
        cutoff, notes = self._cutoff()
        record = self._mirror.run(max_age=cutoff)
        self._insert_audit(record, notes)
        detail = self._detail_line(record, notes)
        if record.status == BACKUP_STATUS_CODE["failed"]:
            raise BackupFailedError(detail)
        return [], detail

    def _cutoff(self) -> tuple[date | None, list[str]]:
        """The robocopy /MAXAGE cutoff from the audit's latest OK batch,
        with a note line explaining any fallback to the full mirror."""
        try:
            cutoff = self._audit.next_cutoff()
        except Exception as exc:  # noqa: BLE001 — DB down must not
            # stop the backup; the full mirror is the safe fallback.
            logger.warning("backup cutoff read failed: %r", exc)
            return None, [f"cutoff read failed ({type(exc).__name__}) "
                          "— full mirror"]
        if cutoff is None:
            return None, ["no audited OK batch yet — full mirror"]
        return cutoff, []

    def _insert_audit(self, record: BackupBatchRecord,
                      notes: list[str]) -> None:
        try:
            self._audit.insert(record)
        except Exception as exc:  # noqa: BLE001 — the mirror ran; the
            # audit outage is a surfaced note, not a lost backup.
            logger.exception("backup audit insert failed")
            notes.append(f"AUDIT ROW NOT WRITTEN ({type(exc).__name__})")

    def _detail_line(self, record: BackupBatchRecord,
                     notes: list[str]) -> str:
        status = BACKUP_STATUS_LABEL[record.status]
        mode = BACKUP_MODE_LABEL[record.mode]
        line = (f"{mode} mirror {status} — copied {record.num_files} "
                f"file(s), skipped {record.num_skipped} up-to-date "
                f"({record.num_bytes} bytes, "
                f"{record.duration_ms / 1000:.1f}s) -> {record.dest_root}")
        return "; ".join([*notes, line])

    @staticmethod
    def _resolve_dest(dest: Path) -> Path:
        """Resolve the backup dest for the runtime platform.

        A Windows ``D:\\...`` dest is what the config means, but on the
        WSL residents ``Path("D:\\...")`` is relative (no ``/`` anchor),
        so map a drive-letter anchor to ``/mnt/<drive>``. Native Windows
        keeps the path verbatim; a plain (non-drive) path passes through
        untouched on either platform."""
        if os.name != "posix":
            return dest
        parts = PureWindowsPath(dest).parts
        if parts and parts[0][:1].isalpha() and parts[0][1:2] == ":":
            return Path("/mnt", parts[0][0].lower(), *parts[1:])
        return dest

    @staticmethod
    def _mount_root(dest: Path) -> Path | None:
        """The drive mount a resolved dest lives under — ``/mnt/<drive>``
        on posix, the drive anchor on Windows, ``None`` for a plain path
        (nothing to guard there)."""
        parts = dest.parts
        if os.name == "posix":
            return Path(*parts[:3]) if (len(parts) >= 3
                                        and parts[1] == "mnt") else None
        if dest.anchor and dest.anchor[1:2] == ":":
            return Path(dest.anchor)
        return None

