"""Central logging setup: console (stdout) + ONE file per (date, service)
under ``logs/<YYYY-MM-DD>/<dotted.service>.log``.

Usage (entry points and modules alike)::

    from _common.log_setup import setup_logging  # noqa: E402
    logger = setup_logging("my_pipeline")

The FILE is keyed by the process's SERVICE name — the dotted entry-module
path (``downloads/stream/sse/price/__main__.py`` →
``downloads.stream.sse.price``), the same name
``_common.service_heartbeat`` registers in monitor.* — derived once per
process from ``__main__.__file__``, so every run writes exactly one log:
``logs/<day>/<service>.log``. The ``name`` argument only names the logger
(``%(name)s`` in the line prefix): later ``setup_logging`` calls in the
same process (library helpers like ``_common/post_check``, embedded child
pipelines) return named child loggers whose records propagate to the root
handlers — into the SAME service file, never a second file. Processes with
no derivable entry module (REPL, tests, embedders) fall back to the FIRST
caller's name. The ``logs/`` directory is created on demand next to the
repo root. Level is overridable with the ``LOG_LEVEL`` env var
(DEBUG/INFO/...).

Console captures point at the same file: the nohup/watchdog spawners
(``scripts/stream_always.sh``, ``database/stream_watchdog._spawn``) append
stdout/stderr to the same ``<service>.log``. When stdout already IS the
service log (read from ``/proc/self/fd/1``), the file handler is SKIPPED —
the redirect fd stays the file's single writer (two append handles on one
/mnt/e file clobber each other under WSL's drvfs; 2026-10-12 probe), and
records reach it through the console handler while raw prints and stderr
(tracebacks, ``[service_heartbeat]`` warnings) ride the redirect. The file
handler re-opens into the next day's directory at midnight, so
non-spawned runs roll their records over (a spawner's redirect stays on
the start date).
"""

import logging
import os
import sys
from datetime import date
from pathlib import Path

_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_CONFIGURED_ATTR = "_oxp_log_configured"


class _DailyDirFileHandler(logging.FileHandler):
    """FileHandler that writes into ``logs/<day>/<name>.log`` and re-opens
    into the new day's directory when the local date changes."""

    def __init__(self, name: str, encoding: str) -> None:
        self._component = name
        self._day = date.today()
        super().__init__(self._path(), encoding=encoding)

    def _path(self) -> Path:
        day_dir = _LOG_DIR / self._day.isoformat()
        day_dir.mkdir(parents=True, exist_ok=True)
        return day_dir / f"{self._component}.log"

    def emit(self, record: logging.LogRecord) -> None:
        record_day = date.fromtimestamp(record.created)
        if record_day != self._day:
            self._day = record_day
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            self.baseFilename = os.fspath(self._path())
        super().emit(record)


def _file_handler(name: str) -> _DailyDirFileHandler:
    handler = _DailyDirFileHandler(name, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
    return handler


def _process_log_name() -> str | None:
    """The process's dotted entry-module name — the service identity the
    log file is keyed by (mirrors ``service_heartbeat.autoregister``'s
    derivation, minus the DB import: ``builds/etf/__main__.py`` →
    ``builds.etf``, ``database/monitoring.py`` → ``database.monitoring``).
    None when there is no entry file under the repo root (REPL, tests,
    embedded interpreters)."""
    main_file = getattr(sys.modules.get("__main__"), "__file__", None)
    if not main_file:
        return None
    try:
        rel = Path(main_file).resolve().relative_to(
            Path(__file__).resolve().parent.parent
        )
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__main__":
        parts = parts[:-1]
    return ".".join(parts) if parts else None


def _stdout_is_service_file(service_file: Path) -> bool:
    """True when fd 1 already IS ``service_file`` — the nohup/watchdog
    spawners append stdout (and stderr, via 2>&1) to the service log, so
    the file handler must stay out (one file, one writer; see module
    docstring). Linux-only probe: hosts without /proc (a Windows dev box)
    report False and get the normal console+file pair."""
    try:
        return Path(os.readlink("/proc/self/fd/1")) == service_file
    except OSError:
        return False


def setup_logging(name: str, level: int | None = None) -> logging.Logger:
    """Configure the process's ONE service-file root handler once; return
    a named child logger."""
    if level is None:
        level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    if not getattr(root, _CONFIGURED_ATTR, False):
        root.setLevel(level)
        formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)

        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(formatter)
        root.addHandler(console)

        service_file = _LOG_DIR / f"{date.today().isoformat()}" / (
            f"{_process_log_name() or name}.log"
        )
        if not _stdout_is_service_file(service_file):
            root.addHandler(_file_handler(_process_log_name() or name))

        logging.getLogger("urllib3.connection").setLevel(logging.ERROR)
        setattr(root, _CONFIGURED_ATTR, True)

    return logging.getLogger(name)
