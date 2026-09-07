"""Central logging setup: console (stdout) + per-component file under
``logs/<YYYY-MM-DD>/``.

Usage (entry points and modules alike)::

    from _common.log_setup import setup_logging  # noqa: E402
    logger = setup_logging("my_pipeline")

Each run writes to ``logs/<day>/<component>.log`` where ``<day>`` is the
local date the process started (the file handler re-opens into the next
day's directory at midnight, so long-running processes roll over too).
The FIRST caller in a process picks the component file name (one run ->
one file, on the root logger so ``logging.getLogger(__name__)`` children
land there as well); later callers with different names additionally get
their own file handler on their named logger. The ``logs/`` directory is
created on demand next to the repo root. Level is overridable with the
``LOG_LEVEL`` env var (DEBUG/INFO/...).
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


def setup_logging(name: str, level: int | None = None) -> logging.Logger:
    """Configure root handlers once; return a named child logger."""
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

        root.addHandler(_file_handler(name))

        logging.getLogger("urllib3.connection").setLevel(logging.ERROR)
        setattr(root, _CONFIGURED_ATTR, True)
        setattr(root, _CONFIGURED_ATTR + "_name", name)
    elif name != getattr(root, _CONFIGURED_ATTR + "_name", None):
        # A second component in the same process also gets its own file,
        # while its records still propagate to the root handlers.
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.addHandler(_file_handler(name))
        return logger

    return logging.getLogger(name)
