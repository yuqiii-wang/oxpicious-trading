"""Download Zhihu search results for daily A-share market commentary.

Each run queries the Zhihu content search API with::

    如何看待{year}年{month}月{day}日A股市场行情？

for a set of target biz dates and stores the (filtered) response under
``temps/zhihu_news/``. Targets (newest-first):

  * **Tomorrow** (next trading day after today) -> ``<keyword>_tmr_forcast_<today>.json``
  * **Today** (only if a trading day)           -> ``<keyword>_tday_snapshot_<today>.json``
  * **Last ``--lookback`` biz days** (default 5) -> ``<keyword>_<biz-date>.json``,
    force-refreshed every run so late-arriving posts overwrite the file.
  * **Older biz dates** back to ``--start-date`` (default 2020-01-01) ->
    ``<keyword>_<biz-date>.json``, skipped when a valid cached file exists.

When an API call is rejected / fails (returns ``None``), an empty JSON with
the required filename (containing ``target_date`` and empty ``items``) is
written so that ``is_cached`` can skip the target on future runs — this keeps
the cache-skip backfill compatible with permanent API rejections.

``_tmr_forcast_`` / ``_tday_snapshot_`` files are keyed by the run date (today) and are
re-fetched every run; ``_<biz-date>_`` files are keyed by the target date.

Response items are validated against the target biz date using **both** the
title date and the ``EditTime`` publish/update timestamp: the search keyword
embeds a date, so fuzzy matches frequently return posts about the same
month/day in an adjacent year, or about a different day entirely. An item is
kept only if its title date exactly matches the target (or has no parseable
date) **and** its ``EditTime`` date falls in the same year within
±``EDIT_TIME_TOLERANCE_DAYS`` (default 3) of the target. Title-date rejections
are classified as ``year_off_by_one`` / ``year_mismatch`` / ``month_mismatch``
/ ``day_mismatch``; ``EditTime`` rejections as ``edit_time_year_off_by_one``
/ ``edit_time_year_mismatch`` / ``edit_time_out_of_window``.

Usage::

    python -m downloads.macro.zhihu.news                          # tmr + tday + last 5 + older(2020-01-01..)
    python -m downloads.macro.zhihu.news --lookback 10            # force-refresh last 10 biz days
    python -m downloads.macro.zhihu.news --start-date 2024-01-01 # extend older backfill floor
    python -m downloads.macro.zhihu.news --force                  # also force-refresh older targets

Anti-bot behaviour (browser-fingerprint rotation, ``random`` query param,
host-blocking detection, sleep cadence) is provided by the shared
``AntiBotProxy`` from ``downloads._common.core``. Trading days are enumerated
via ``_common._holidays_and_weekdays`` so holidays and weekends are skipped.
API credentials are read from ``ZHIHU_API_KEY`` in the project-root ``.env``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# Make the project root importable when this module is executed directly
# (``python downloads/macro/zhihu/news/__init__.py``) as well as imported as
# a package. ``__file__`` is downloads/macro/zhihu/news/__init__.py, so
# parents[4] is the project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import requests  # noqa: E402

from downloads._common.core import (  # noqa: E402
    AntiBotConfig,
    AntiBotProxy,
    DEFAULT_START_DATE,
    LONG_SLEEP_INTERVAL,
    resolve_out_dir,
    setup_logger,
)
from _common._holidays_and_weekdays import (  # noqa: E402
    business_days,
    is_trading_day,
    last_business_day,
    next_business_day,
)


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------
ZHIHU_SEARCH_URL = "https://developer.zhihu.com/api/v1/content/zhihu_search"
DEFAULT_KEYWORD = "如何看待A股"
DEFAULT_COUNT = 10
DEFAULT_LOOKBACK = 5  # recent biz days force-refreshed each run
OUTPUT_DIR_NAME = "zhihu_news"
CACHED_MIN_BYTES = 80  # a valid stored JSON (even with 0 items) is a few hundred bytes
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
EDIT_TIME_TOLERANCE_DAYS = 3  # accept posts published within ±N days of target

logger = setup_logger("zhihu_news")


# ----------------------------------------------------------------------------
# Env / API key
# ----------------------------------------------------------------------------
def _load_env() -> None:
    """Load ZHIHU_API_KEY from the project-root ``.env`` if not already set."""
    env_path = _PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    with env_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def get_api_key() -> str:
    _load_env()
    key = os.environ.get("ZHIHU_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "ZHIHU_API_KEY not found: set it in the project-root .env or environment."
        )
    return key


# ----------------------------------------------------------------------------
# Date extraction from item titles
# ----------------------------------------------------------------------------
# Ordered patterns: Chinese long form first (matches the query style), then
# dash / dot / compact numeric forms.
_DATE_PATTERNS: List[re.Pattern] = [
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})"),
    re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})"),
    re.compile(r"(?<!\d)(\d{4})(\d{2})(\d{2})(?!\d)"),
]


def extract_date_from_title(title: str) -> Optional[date]:
    """Try to extract a date from *title*. Returns None if no date is found."""
    if not title:
        return None
    for pat in _DATE_PATTERNS:
        m = pat.search(title)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
    return None


# ----------------------------------------------------------------------------
# Query / output path construction
# ----------------------------------------------------------------------------
def build_query(target: date) -> str:
    return f"如何看待{target.year}年{target.month}月{target.day}日A股市场行情？"


def build_output_path(out_dir: Path, keyword: str, target: date) -> Path:
    return out_dir / f"{keyword}_{target.strftime('%Y-%m-%d')}.json"


# ----------------------------------------------------------------------------
# Response validation
# ----------------------------------------------------------------------------
def _rejection_reason(d_item: Optional[date], target: date) -> Optional[str]:
    """Return a rejection reason string, or None if the item should be kept.

    Acceptance rule: keep only items whose title date *exactly* matches the
    target biz date. Items with no parseable date in the title are kept (we
    cannot validate them and prefer not to drop potentially-relevant content).

    The search keyword embeds a date, so the API frequently returns posts
    about the same month/day in an adjacent year (±1) or about a different
    day/month entirely — those are rejected with a classified reason.
    """
    if d_item is None:
        return None  # cannot validate -> keep
    if d_item == target:
        return None  # exact match -> accept
    # Year off by ±1 — the most common adjacent-year false positive
    if d_item.year == target.year - 1 or d_item.year == target.year + 1:
        return "year_off_by_one"
    if d_item.year != target.year:
        return "year_mismatch"
    # Same year from here on
    if d_item.month != target.month:
        return "month_mismatch"
    # Same year, same month, different day (covers yesterday & future days)
    return "day_mismatch"


def _edit_time_to_date(edit_time: Any) -> Optional[date]:
    """Convert an ``EditTime`` Unix-seconds timestamp to an Asia/Shanghai date."""
    if edit_time is None:
        return None
    try:
        ts = int(edit_time)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, tz=SHANGHAI_TZ).date()
    except (OverflowError, OSError, ValueError):
        return None


def _edit_time_rejection_reason(edit_date: Optional[date], target: date) -> Optional[str]:
    """Validate the item's publish/update (``EditTime``) date against *target*.

    Returns a rejection reason, or None to accept. The ``EditTime`` timestamp
    is the authoritative "when" of a post: a market commentary for the target
    biz date is normally published the day before (preview), on the day, or a
    few days after (delayed reflection) — so a ±``EDIT_TIME_TOLERANCE_DAYS``
    window is accepted within the same year. Posts from an adjacent year (±1)
    are rejected (the typical adjacent-year false positive returned by the
    date-embedded search keyword). Missing/invalid ``EditTime`` -> keep (the
    title-date check still applies).
    """
    if edit_date is None:
        return None  # cannot validate -> keep
    if edit_date.year == target.year - 1 or edit_date.year == target.year + 1:
        return "edit_time_year_off_by_one"
    if edit_date.year != target.year:
        return "edit_time_year_mismatch"
    if abs((edit_date - target).days) > EDIT_TIME_TOLERANCE_DAYS:
        return "edit_time_out_of_window"
    return None  # within tolerance, same year -> accept


def validate_items(
    items: List[Dict[str, Any]],
    target: date,
) -> Tuple[List[Dict[str, Any]], Dict[str, int], List[Dict[str, str]]]:
    """Filter *items* by title-date AND ``EditTime`` validation.

    An item is kept only if *both* checks pass: the title date must exactly
    match *target* (or carry no parseable date), and the ``EditTime`` publish
    date must fall within the same year and ±``EDIT_TIME_TOLERANCE_DAYS`` of
    *target*. This catches adjacent-year posts returned by the date-embedded
    search keyword even when the title omits a date.

    Returns (accepted_items, reason_counts, rejected_log) where each
    ``rejected_log`` entry records the title, the parsed title date, the raw
    ``EditTime``, the parsed edit date, and the rejection reason.
    """
    accepted: List[Dict[str, Any]] = []
    reason_counts: Dict[str, int] = {}
    rejected_log: List[Dict[str, str]] = []
    for it in items:
        title = it.get("Title", "") or ""
        edit_time = it.get("EditTime")
        d_item = extract_date_from_title(title)
        edit_date = _edit_time_to_date(edit_time)

        # Title-date check first (exact match required); then EditTime check.
        reason = _rejection_reason(d_item, target)
        if reason is None:
            reason = _edit_time_rejection_reason(edit_date, target)

        if reason is None:
            accepted.append(it)
        else:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            rejected_log.append({
                "title": title,
                "title_date": d_item.strftime("%Y-%m-%d") if d_item else "",
                "edit_time": edit_time,
                "edit_date": edit_date.strftime("%Y-%m-%d") if edit_date else "",
                "reason": reason,
            })
    return accepted, reason_counts, rejected_log


# ----------------------------------------------------------------------------
# API call
# ----------------------------------------------------------------------------
def search_zhihu(
    session: requests.Session,
    proxy: AntiBotProxy,
    query: str,
    api_key: str,
    *,
    count: int = DEFAULT_COUNT,
    max_retries: int = 3,
) -> Optional[Dict[str, Any]]:
    """Call the Zhihu search API via the shared anti-bot proxy.

    Returns the parsed JSON dict or None. Retries on transient rate-limiting
    (API ``Code=30001``) or network failures with exponential backoff. The
    proxy handles browser-fingerprint rotation, the ``random`` query param,
    host-blocking detection, and the inter-request sleep cadence; auth headers
    (Authorization / X-Request-Timestamp / Content-Type) are preserved.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Request-Timestamp": str(int(time.time())),
        "Content-Type": "application/json",
    }
    params = {"Query": query, "Count": count}

    for attempt in range(1, max_retries + 1):
        if proxy.is_blocked(ZHIHU_SEARCH_URL):
            logger.error("  host blocked, aborting: %s", query)
            return None
        resp = proxy.get(
            session, ZHIHU_SEARCH_URL,
            params=params, headers=headers,
            logger=logger, log_tag="  ",
        )
        if resp is None:
            logger.warning("  request failed (attempt %d/%d)", attempt, max_retries)
            time.sleep(min(2 ** attempt, 30))
            continue

        try:
            data = resp.json()
        except ValueError as e:
            logger.error("  JSON parse error: %s (body: %s)", e, resp.text[:200])
            return None

        code = data.get("Code")
        if code == 0:
            return data
        if code == 30001:  # frequency limit — retry with backoff
            wait = min(2 ** attempt * 5, 60)
            logger.warning("  API rate limit (Code=30001), backing off %ds", wait)
            time.sleep(wait)
            continue
        # Non-retryable API error (10001 param, 20001 auth, 90001 internal)
        logger.error("  API error Code=%s Message=%s", code, data.get("Message"))
        return data

    logger.error("  exhausted retries for query: %s", query)
    return None


# ----------------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------------
def save_response(
    out_path: Path,
    *,
    query: str,
    keyword: str,
    target: date,
    raw_data: Optional[Dict[str, Any]],
    accepted_items: List[Dict[str, Any]],
    rejection_reasons: Dict[str, int],
    rejected_log: List[Dict[str, str]],
    total_returned: int,
) -> None:
    """Write the filtered response + filter metadata to *out_path*."""
    data_obj: Dict[str, Any] = raw_data.get("Data") if raw_data else None
    if not isinstance(data_obj, dict):
        data_obj = {}
    payload = {
        "query": query,
        "keyword": keyword,
        "target_date": target.strftime("%Y-%m-%d"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "code": raw_data.get("Code") if raw_data else None,
        "message": raw_data.get("Message") if raw_data else None,
        "search_hash_id": data_obj.get("SearchHashId"),
        "has_more": data_obj.get("HasMore"),
        "empty_reason": data_obj.get("EmptyReason"),
        "items": accepted_items,
        "filter_stats": {
            "total_returned": total_returned,
            "accepted": len(accepted_items),
            "rejected": total_returned - len(accepted_items),
            "rejection_reasons": rejection_reasons,
            "rejected_log": rejected_log,
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def is_cached(out_path: Path) -> bool:
    """Return True if *out_path* exists and looks like a valid stored response."""
    if not out_path.exists() or not out_path.is_file():
        return False
    try:
        if out_path.stat().st_size < CACHED_MIN_BYTES:
            return False
    except OSError:
        return False
    try:
        with out_path.open("r", encoding="utf-8") as f:
            obj = json.load(f)
    except (ValueError, OSError):
        return False
    return isinstance(obj, dict) and "target_date" in obj


# ----------------------------------------------------------------------------
# Main download orchestrator
# ----------------------------------------------------------------------------
def _last_n_biz_days_before(ref: date, n: int) -> List[date]:
    """Return the *n* most recent trading days strictly before *ref*, newest-first.

    Holidays and weekends are skipped via ``is_trading_day``.
    """
    out: List[date] = []
    if n <= 0:
        return out
    cursor = ref - timedelta(days=1)
    while len(out) < n:
        if is_trading_day(cursor):
            out.append(cursor)
        cursor -= timedelta(days=1)
    return out


def _build_targets(
    *,
    out_dir: Path,
    keyword: str,
    start_date: Optional[str],
    lookback: int,
) -> List[Dict[str, Any]]:
    """Build the ordered (newest-first) list of download targets.

    Each target is ``{"date": date, "path": Path, "force": bool}`` where
    ``date`` is the biz date embedded in the query and ``path`` is the output
    JSON path. Holidays/weekends are skipped via ``_common._holidays_and_weekdays``.

    Targets (in execution order):
      1. **Tomorrow** — the next trading day strictly after today. The query
         asks about tomorrow's market; saved to ``<keyword>_tmr_forcast_<today>.json``
         and re-fetched every run.
      2. **Today** — only if today is a trading day. Saved to
         ``<keyword>_tday_snapshot_<today>.json`` and re-fetched every run.
      3. **Last ``lookback`` biz days before today** — saved to
         ``<keyword>_<biz-date>.json`` and force-refreshed (overwritten) every
         run so recent commentary picks up late-arriving posts.
      4. **Older biz dates** from ``start_date`` (default 2020-01-01) up to the
         day before the lookback window — saved to ``<keyword>_<biz-date>.json``
         and skipped when a valid cached file already exists.
    """
    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    targets: List[Dict[str, Any]] = []

    # 1. Tomorrow (next trading day strictly after today)
    tmr = next_business_day(today + timedelta(days=1))
    targets.append({
        "date": tmr,
        "path": out_dir / f"{keyword}_tmr_forcast_{today_str}.json",
        "force": True,
    })

    # 2. Today (only if a trading day)
    if is_trading_day(today):
        targets.append({
            "date": today,
            "path": out_dir / f"{keyword}_tday_snapshot_{today_str}.json",
            "force": True,
        })

    # 3. Last `lookback` biz days strictly before today (force-refresh)
    recent = _last_n_biz_days_before(today, lookback)
    for d in recent:
        targets.append({
            "date": d,
            "path": build_output_path(out_dir, keyword, d),
            "force": True,
        })

    # 4. Older biz dates (cache-skip): start .. day before the lookback window
    if start_date:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        start = datetime.strptime(DEFAULT_START_DATE, "%Y-%m-%d").date()
    older_end = (recent[-1] if recent else today) - timedelta(days=1)
    if older_end >= start:
        for d in business_days(start, older_end, reverse=True):
            targets.append({
                "date": d,
                "path": build_output_path(out_dir, keyword, d),
                "force": False,
            })

    return targets


def download_zhihu_news(
    *,
    out_root: Optional[str] = None,
    start_date: Optional[str] = None,
    lookback: int = DEFAULT_LOOKBACK,
    keyword: str = DEFAULT_KEYWORD,
    count: int = DEFAULT_COUNT,
    sleep_sec: float = LONG_SLEEP_INTERVAL,
    force: bool = False,
) -> Dict[str, Any]:
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), OUTPUT_DIR_NAME, out_root)
    api_key = get_api_key()
    targets = _build_targets(
        out_dir=out_dir, keyword=keyword,
        start_date=start_date, lookback=lookback,
    )

    logger.info(
        "Zhihu news download: %d target(s) [%s .. %s] lookback=%d -> %s",
        len(targets),
        targets[0]["date"] if targets else "-",
        targets[-1]["date"] if targets else "-",
        lookback,
        out_dir,
    )

    session = requests.Session()
    proxy = AntiBotProxy(AntiBotConfig(
        base_sleep_sec=sleep_sec,
        enable_host_tracking=True,
    ))
    downloaded = skipped_cached = failed = 0
    accepted_total = rejected_total = 0
    try:
        for i, t in enumerate(targets, 1):
            if proxy.is_blocked(ZHIHU_SEARCH_URL):
                logger.warning("  host blocked — stopping download loop")
                break

            target_date = t["date"]
            out_path = t["path"]
            force_this = t["force"] or force
            if not force_this and is_cached(out_path):
                skipped_cached += 1
                logger.info("[%d/%d] %s cached, skip", i, len(targets), out_path.name)
                continue

            query = build_query(target_date)
            logger.info("[%d/%d] %s query=%s", i, len(targets), target_date, query)

            raw = search_zhihu(session, proxy, query, api_key, count=count)
            if raw is None:
                failed += 1
                # API call rejected/failed — log an empty JSON with the
                # required filename so is_cached can skip this target on
                # future runs (compatible with the cache-skip backfill).
                save_response(
                    out_path,
                    query=query,
                    keyword=keyword,
                    target=target_date,
                    raw_data=None,
                    accepted_items=[],
                    rejection_reasons={},
                    rejected_log=[],
                    total_returned=0,
                )
                logger.info("  saved empty %s (API rejected)", out_path.name)
                # search_zhihu already backed off during retries; ensure a
                # cadence sleep before the next target on hard failure.
                proxy.sleep()
                continue

            data_obj = raw.get("Data") or {}
            items = data_obj.get("Items") or []
            total_returned = len(items)

            accepted, reasons, rejected_log = validate_items(items, target_date)
            accepted_total += len(accepted)
            rejected_total += total_returned - len(accepted)

            save_response(
                out_path,
                query=query,
                keyword=keyword,
                target=target_date,
                raw_data=raw,
                accepted_items=accepted,
                rejection_reasons=reasons,
                rejected_log=rejected_log,
                total_returned=total_returned,
            )
            downloaded += 1
            logger.info(
                "  saved %s (returned=%d accepted=%d rejected=%s)",
                out_path.name, total_returned, len(accepted),
                {k: v for k, v in reasons.items()} if reasons else "{}",
            )
            # Inter-request cadence is handled by proxy.get()'s auto-sleep.
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = {
        "downloaded": downloaded,
        "skipped_cached": skipped_cached,
        "failed": failed,
        "accepted_total": accepted_total,
        "rejected_total": rejected_total,
        "out_dir": str(out_dir),
        "targets": len(targets),
    }
    logger.info(
        "Done. downloaded=%d skipped=%d failed=%d accepted=%d rejected=%d",
        downloaded, skipped_cached, failed, accepted_total, rejected_total,
    )
    return summary


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Download Zhihu search results for A-share biz dates")
    parser.add_argument("--start-date", type=str, default=None,
                        help=f"Floor for older (cache-skip) biz dates YYYY-MM-DD. Default: {DEFAULT_START_DATE}")
    parser.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK,
                        help=f"Force-refresh the last N biz days before today. Default: {DEFAULT_LOOKBACK}")
    parser.add_argument("--keyword", type=str, default=DEFAULT_KEYWORD,
                        help=f"Keyword / filename prefix. Default: {DEFAULT_KEYWORD}")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                        help=f"Result count per query (1-10). Default: {DEFAULT_COUNT}")
    parser.add_argument("--sleep-sec", type=float, default=LONG_SLEEP_INTERVAL,
                        help=f"Anti-bot sleep between requests (s). Default: {LONG_SLEEP_INTERVAL}")
    parser.add_argument("--out-root", type=str, default=None,
                        help="Output root dir. Default: <project>/temps/zhihu_news")
    parser.add_argument("--force", action="store_true",
                        help="Force re-download of every target, including cache-skip (older) ones.")
    args = parser.parse_args()

    if args.count < 1 or args.count > 10:
        parser.error("--count must be between 1 and 10")
    if args.lookback < 0:
        parser.error("--lookback must be >= 0")

    summary = download_zhihu_news(
        out_root=args.out_root,
        start_date=args.start_date,
        lookback=args.lookback,
        keyword=args.keyword,
        count=args.count,
        sleep_sec=args.sleep_sec,
        force=args.force,
    )
    print(summary)


if __name__ == "__main__":
    main()
