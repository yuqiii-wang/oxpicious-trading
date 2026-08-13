"""Internal shared scaffolding for gov-family title-list downloaders.

Each source module (``news`` = gov.cn 政策解读 JSON, ``ndrc`` = ndrc.gov.cn
新闻发布 HTML) defines a :class:`SourceConfig`, a ``fetch_fn`` that returns the
raw item list, and a ``parse_fn`` that filters the raw list into CSV rows, then
calls :func:`download_source`. This module owns the anti-bot session/proxy,
date parsing, CSV writing, raw-JSON snapshot caching, and the summary/CLI
plumbing so the source modules stay thin.

Only the list is scraped — detail links are NOT followed (no per-article
crawling). Anti-bot behaviour (browser-fingerprint rotation, ``random`` query
param, host-blocking detection, sleep cadence) is provided by the shared
``AntiBotProxy`` from ``downloads._common.core``; the inter-request sleep
defaults to ``LONG_SLEEP_INTERVAL`` (90s) per the project's anti-bot policy.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# Make the project root importable when this module is executed directly
# (``python downloads/macro/gov/main_gov.py``) as well as imported as a
# package. ``__file__`` is downloads/macro/gov/main_gov.py, so parents[3] is
# the project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from downloads._common.core import (  # noqa: E402
    AntiBotConfig,
    AntiBotProxy,
    COMMON_BASE_HEADERS,
    DEFAULT_START_DATE,
    LONG_SLEEP_INTERVAL,
    build_default_session,
    resolve_out_dir,
    setup_logger,
)

logger = setup_logger("gov_main")

# ----------------------------------------------------------------------------
# Source configuration
# ----------------------------------------------------------------------------
@dataclass
class SourceConfig:
    """Describes a gov-family title-list source.

    Attributes:
        name: short identifier used in logs and the summary (e.g. ``gov_news``).
        list_url: human-readable list page URL (used as Referer / in logs).
        out_dirname: sub-directory under ``temps/`` for this source's output.
        csv_filename: name of the combined titles CSV file.
        csv_columns: ordered CSV column names.
        raw_min_bytes: minimum bytes for a cached raw-JSON snapshot to be
            considered valid (sources may override for very small lists).
    """
    name: str
    list_url: str
    out_dirname: str
    csv_filename: str
    csv_columns: List[str] = field(default_factory=lambda: ["pub_date", "title", "url"])
    raw_min_bytes: int = 1000


# Type aliases for the source-provided callables.
FetchFn = Callable[[Any, AntiBotProxy, SourceConfig], Optional[List[Dict[str, Any]]]]
ParseFn = Callable[[List[Dict[str, Any]], date], List[Dict[str, str]]]


# ----------------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------------
def make_session_and_proxy(sleep_sec: float) -> Tuple[Any, AntiBotProxy]:
    """Build a default requests session + an AntiBotProxy with the given sleep."""
    session = build_default_session()
    proxy = AntiBotProxy(AntiBotConfig(
        base_sleep_sec=sleep_sec,
        enable_host_tracking=True,
    ))
    return session, proxy


def parse_date_str(raw: Any) -> Optional[date]:
    """Parse a date value into a :class:`date`.

    Accepts ``YYYY-MM-DD`` and ``YYYY/MM/DD`` (the two formats used by gov.cn
    JSON and ndrc.gov.cn HTML respectively). Returns None if unparseable.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def is_cached_json(path: Path, min_bytes: int) -> bool:
    """Return True if *path* exists and looks like a valid stored JSON list."""
    if not path.exists() or not path.is_file():
        return False
    try:
        if path.stat().st_size < min_bytes:
            return False
    except OSError:
        return False
    try:
        with path.open("r", encoding="utf-8") as f:
            obj = json.load(f)
    except (ValueError, OSError):
        return False
    return isinstance(obj, list)


def load_json(path: Path) -> Optional[List[Dict[str, Any]]]:
    """Load a JSON list from *path*, returning None on any failure."""
    try:
        with path.open("r", encoding="utf-8") as f:
            obj = json.load(f)
    except (ValueError, OSError):
        return None
    return obj if isinstance(obj, list) else None


def save_json(path: Path, data: List[Dict[str, Any]]) -> None:
    """Write *data* as a UTF-8 JSON list to *path* (creates parent dirs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_csv(rows: List[Dict[str, str]], out_path: Path, columns: List[str]) -> None:
    """Write *rows* to *out_path* as UTF-8-SIG CSV with *columns* header."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def filter_sort_rows(
    rows: List[Dict[str, str]],
    start: date,
    columns: List[str],
    dedup_key: str = "url",
) -> List[Dict[str, str]]:
    """Drop rows whose pub_date < *start*, dedupe by *dedup_key*, sort newest-first.

    Rows with an empty title or unparseable pub_date should already have been
    dropped by the source's parse_fn; this helper only enforces the start floor,
    dedup, and sort. Each row is projected onto *columns* (missing keys -> "").
    """
    seen: set = set()
    out: List[Dict[str, str]] = []
    for r in rows:
        pd = parse_date_str(r.get("pub_date", ""))
        if pd is None or pd < start:
            continue
        k = r.get(dedup_key, "")
        if k:
            if k in seen:
                continue
            seen.add(k)
        out.append({c: r.get(c, "") for c in columns})
    out.sort(key=lambda x: (x["pub_date"], x["title"]), reverse=True)
    return out


# ----------------------------------------------------------------------------
# Orchestrator
# ----------------------------------------------------------------------------
def download_source(
    config: SourceConfig,
    fetch_fn: FetchFn,
    parse_fn: ParseFn,
    *,
    out_root: Optional[str] = None,
    start_date: Optional[str] = None,
    sleep_sec: float = LONG_SLEEP_INTERVAL,
    force: bool = False,
) -> Dict[str, Any]:
    """Download a source's title list and write it to CSV.

    The raw item list is cached per run-date as ``<name>_raw_<YYYY-MM-DD>.json``
    and re-used on subsequent same-day runs unless *force* is set. The combined
    titles CSV (``config.csv_filename``) is rewritten every run from the
    (cached or fresh) raw items.
    """
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), config.out_dirname, out_root)
    start = datetime.strptime(start_date or DEFAULT_START_DATE, "%Y-%m-%d").date()
    today_str = date.today().strftime("%Y-%m-%d")
    raw_path = out_dir / f"{config.name}_raw_{today_str}.json"
    csv_path = out_dir / config.csv_filename

    logger.info(
        "%s: start=%s sleep=%.1fs -> %s",
        config.name, start, sleep_sec, out_dir,
    )

    session, proxy = make_session_and_proxy(sleep_sec)

    raw_items: Optional[List[Dict[str, Any]]] = None
    if not force and is_cached_json(raw_path, config.raw_min_bytes):
        logger.info("[%s] today's raw snapshot cached: %s", config.name, raw_path.name)
        raw_items = load_json(raw_path)

    if raw_items is None:
        raw_items = fetch_fn(session, proxy, config)
        if raw_items is None:
            logger.error("[%s] fetch failed; no CSV written", config.name)
            return {
                "name": config.name, "downloaded": 0, "rows": 0, "failed": True,
                "out_dir": str(out_dir), "raw_path": str(raw_path),
                "csv_path": str(csv_path), "start_date": str(start),
            }
        save_json(raw_path, raw_items)
        logger.info(
            "[%s] saved raw snapshot: %s (%d items)",
            config.name, raw_path.name, len(raw_items),
        )

    parsed_rows = parse_fn(raw_items, start)
    rows = filter_sort_rows(parsed_rows, start, config.csv_columns)
    write_csv(rows, csv_path, config.csv_columns)

    if rows:
        dates = [r["pub_date"] for r in rows]
        date_range = f"{dates[-1]} .. {dates[0]}"
    else:
        date_range = "-"

    summary = {
        "name": config.name,
        "downloaded": 1,
        "raw_items": len(raw_items),
        "rows": len(rows),
        "date_range": date_range,
        "out_dir": str(out_dir),
        "raw_path": str(raw_path),
        "csv_path": str(csv_path),
        "start_date": str(start),
        "failed": False,
    }
    logger.info(
        "[%s] Done. raw=%d rows=%d [%s] -> %s",
        config.name, len(raw_items), len(rows), date_range, csv_path.name,
    )
    return summary


# ----------------------------------------------------------------------------
# Shared CLI builder
# ----------------------------------------------------------------------------
def build_cli(description: str) -> argparse.ArgumentParser:
    """Build the common argparse parser used by every source's ``main()``."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--start-date", type=str, default=None,
                        help=f"Floor date YYYY-MM-DD. Default: {DEFAULT_START_DATE}")
    parser.add_argument("--sleep-sec", type=float, default=LONG_SLEEP_INTERVAL,
                        help=f"Anti-bot sleep between requests (s). Default: {LONG_SLEEP_INTERVAL}")
    parser.add_argument("--out-root", type=str, default=None,
                        help="Output root dir. Default: <project>/temps/<source>")
    parser.add_argument("--force", action="store_true",
                        help="Force re-fetch of today's raw snapshot even if cached.")
    return parser


def run_cli(
    config: SourceConfig,
    fetch_fn: FetchFn,
    parse_fn: ParseFn,
    description: str,
) -> None:
    """Parse args and invoke :func:`download_source` — call from source main()."""
    args = build_cli(description).parse_args()
    summary = download_source(
        config, fetch_fn, parse_fn,
        out_root=args.out_root,
        start_date=args.start_date,
        sleep_sec=args.sleep_sec,
        force=args.force,
    )
    print(summary)
