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
    python -m downloads.macro.zhihu.news --question "…如何解读？" [--author NAME]  # one-off search

Anti-bot behaviour (browser-fingerprint rotation, ``random`` query param,
host-blocking detection, sleep cadence) is provided by the shared
``AntiBotProxy`` from ``downloads._common``. Trading days are enumerated
via ``_common._holidays_and_weekdays`` so holidays and weekends are skipped.
API credentials are read from ``ZHIHU_API_KEY`` in the project-root ``.env``.

**One-off question mode** (``--question``): searches the same content API
with the raw question text instead of the scheduled daily query — this is
what the dataviz News page's question bar drives (source routed by the API
layer; ``--author`` optionally narrows the returned items). Items are NOT
date-validated (no date is embedded in the query) and the artifact is stored
like any other zhihu JSON (``search_<question>_<md5>_<today>.json``) so the
next ``builds.text`` run ingests it into the corpus. The parsed result is
additionally echoed on stdout as a marker-prefixed JSON envelope
(``SEARCH_RESULT_MARKER``) for the API layer to parse — stdout also carries
the logger stream, hence the marker.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
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

from downloads._common import (  # noqa: E402
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

# One-off question mode (--question): a single request, so the anti-bot
# cadence drops from the scheduled LONG_SLEEP_INTERVAL to something the
# interactive UI can wait for.
SEARCH_SLEEP_SEC = 2.0
# Comments fetch (--with-comments): per-item cadence against www.zhihu.com
# (public api/v4 root_comments — no Bearer auth), so a light sleep is enough.
COMMENT_SLEEP_SEC = 1.5
COMMENT_PAGE_LIMIT = 20  # root comments per page (api max for one call)
COMMENT_PAGE_CAP = 3     # pages per item — bounds interactive latency
# Stdout marker prefixing the JSON result envelope. The logger stream also
# writes to stdout, so the API layer scans for the LAST marker line.
SEARCH_RESULT_MARKER = "@@NEWS_SEARCH_JSON@@"

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
    author: Optional[str] = None,
    comments: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Write the filtered response + filter metadata to *out_path*.

    *author* records the optional author filter of a one-off question search
    (always None for the scheduled downloads). *comments* carries the
    ``--with-comments`` fetch (list of {content_id, comments}); it is stored
    under the artifact's top-level ``comments`` key so builds.text can load
    them into text.news_comments.
    """
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
        "comments": comments or [],
        "filter_stats": {
            "author_filter": author,
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
# One-off question search (UI question bar -> API route -> this function)
# ----------------------------------------------------------------------------
def _sanitize_filename_part(s: str, max_len: int = 40) -> str:
    """Strip filesystem-unsafe ASCII chars + whitespace for a readable name.

    Full-width CJK punctuation (，？：etc.) is filename-safe and kept, so a
    Chinese question stays readable in the artifact name.
    """
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "", s)
    return cleaned[:max_len] or "search"


def search_zhihu_question(
    question: str,
    api_key: str,
    *,
    author: Optional[str] = None,
    count: int = DEFAULT_COUNT,
    sleep_sec: float = SEARCH_SLEEP_SEC,
    out_dir: Optional[Path] = None,
    with_comments: bool = True,
) -> Dict[str, Any]:
    """Search the Zhihu content API for an arbitrary user *question*.

    Same request format as the scheduled download (GET ``zhihu_search`` with
    ``Query``/``Count`` + Bearer auth), but the query is the raw question
    text: items are NOT date-validated (no date is embedded in the query to
    validate against) and an optional *author* narrows the results to one
    ``AuthorName`` (case-insensitive exact match). The response is stored in
    the shared artifact dir as ``search_<question>_<md5>_<today>.json`` so
    the next ``builds.text`` run ingests it, and the parsed result is echoed
    on stdout as a marker-prefixed JSON envelope for the API layer.

    Returns the envelope dict (also what gets printed).
    """
    if out_dir is None:
        out_dir = resolve_out_dir(str(Path(__file__).resolve()), OUTPUT_DIR_NAME)

    session = requests.Session()
    proxy = AntiBotProxy(AntiBotConfig(
        base_sleep_sec=sleep_sec,
        enable_host_tracking=True,
    ))

    logger.info("question search: %s (author=%s count=%d)", question, author or "-", count)
    raw = search_zhihu(session, proxy, question, api_key, count=count)
    ok = raw is not None and raw.get("Code") == 0
    data_obj = (raw or {}).get("Data") or {}
    items = data_obj.get("Items") or []
    total_returned = len(items)

    if author:
        author_clean = _clean_author(author)
        kept = [
            it for it in items
            if (_clean_author(it.get("AuthorName")) or "").casefold()
            == author_clean.casefold()
        ]
    else:
        kept = list(items)
    logger.info("  returned=%d kept=%d (author=%s) ok=%s",
                total_returned, len(kept), author or "-", ok)

    comments: List[Dict[str, Any]] = []
    if with_comments and ok and kept:
        comments_by_cid = with_comments_for_items(kept, sleep_sec=COMMENT_SLEEP_SEC)
        comments = [
            {"content_id": cid, "comments": rows}
            for cid, rows in comments_by_cid.items()
        ]

    # Persist with the shared schema (target_date = fetch date; items carry
    # their own EditTime publish dates, which is what the loader dates by).
    target = date.today()
    digest = hashlib.md5(question.encode("utf-8")).hexdigest()[:8]
    out_path = out_dir / (
        f"search_{_sanitize_filename_part(question)}_{digest}_"
        f"{target.strftime('%Y-%m-%d')}.json"
    )
    save_response(
        out_path,
        query=question,
        keyword=question,
        target=target,
        raw_data=raw,
        accepted_items=kept,
        rejection_reasons={},
        rejected_log=[],
        total_returned=total_returned,
        author=author,
        comments=comments,
    )
    logger.info("  saved %s (comments=%d)", out_path.name,
                sum(len(c["comments"]) for c in comments))

    envelope = {
        "success": bool(ok),
        "code": (raw or {}).get("Code"),
        "message": (raw or {}).get("Message"),
        "question": question,
        "source": "zhihu",
        "author": author,
        "total_returned": total_returned,
        "returned": len(kept),
        "comments": sum(len(c["comments"]) for c in comments),
        "out_file": str(out_path),
        "items": kept,
    }
    print(
        SEARCH_RESULT_MARKER + json.dumps(envelope, ensure_ascii=False),
        flush=True,
    )
    return envelope


def _clean_author(v: Any) -> str:
    """Trim whitespace/invisible separators — same rule as builds.text."""
    if not v:
        return ""
    v = re.sub(r"[\u200b\u200c\u200d\ufeff\u200e\u200f]", "", str(v)).strip()
    return v


# ----------------------------------------------------------------------------
# Comments (--with-comments): public www.zhihu.com api/v4 root_comments.
#
# The official developer platform exposes no comments endpoint (probed), so
# comments come from the site's public api: /api/v4/answers/{id}/root_comments
# for Answer items, /api/v4/articles/{id}/root_comments for Article items —
# the ids are the tails of the item's Url. Root comments arrive with their
# first-level replies embedded in child_comments; both are flattened into
# rows. No auth: plain browser-profile headers via the shared anti-bot proxy.
# ----------------------------------------------------------------------------
_COMMENT_API = "https://www.zhihu.com/api/v4"

_ANSWER_ID_RE = re.compile(r"/answer/(\d+)")
_ARTICLE_ID_RE = re.compile(r"/p/(\d+)")

# Plain browser headers — proven to work on the public root_comments API (the
# anti-bot browser-profile rotation + random query param trips a 403 there,
# so this fetch deliberately bypasses the proxy's request path).
_COMMENT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.zhihu.com/",
}


def _content_ref(url: str) -> Optional[Tuple[str, str]]:
    """(kind, id) from an item Url, or None: ('answer', id) | ('article', id)."""
    if not url:
        return None
    m = _ANSWER_ID_RE.search(url)
    if m:
        return ("answers", m.group(1))
    m = _ARTICLE_ID_RE.search(url)
    if m:
        return ("articles", m.group(1))
    return None


def _comment_rows(raw_comments: List[Dict[str, Any]], root_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Flatten one root_comments page into threaded rows.

    *root_id* is None for root comments; embedded replies carry their root's
    comment id as ``parent_comment_id`` so the load/UI side can rebuild the
    thread. votes = the comment like count (vote_count), NULL when absent.
    """
    rows: List[Dict[str, Any]] = []
    for c in raw_comments or []:
        if not isinstance(c, dict) or c.get("is_delete"):
            continue
        try:
            cid = int(c.get("id"))
        except (TypeError, ValueError):
            continue
        created = c.get("created_time")
        try:
            created_date = (
                datetime.fromtimestamp(int(created), tz=SHANGHAI_TZ).date().isoformat()
                if created else None
            )
        except (TypeError, ValueError, OSError, OverflowError):
            created_date = None
        try:
            votes = int(c.get("vote_count"))
        except (TypeError, ValueError):
            votes = None
        member = (c.get("author") or {}).get("member") or {}
        rows.append({
            "comment_id": cid,
            "parent_comment_id": root_id,
            "author": _clean_author(member.get("name")) or None,
            "content": (c.get("content") or "").strip() or None,
            "date": created_date,
            "votes": votes,
            "is_reply": root_id is not None,
        })
        if root_id is None and c.get("child_comments"):
            rows.extend(_comment_rows(c["child_comments"], root_id=cid))
    return rows


def fetch_item_comments(
    session: requests.Session,
    url: str,
    *,
    sleep_sec: float = COMMENT_SLEEP_SEC,
) -> List[Dict[str, Any]]:
    """Fetch all root comments (± replies) for one item Url. [] on any failure.

    Plain GET with fixed browser headers (see _COMMENT_HEADERS) — no Bearer
    auth, no proxy randomization; a light sleep paces the page calls.
    """
    ref = _content_ref(url or "")
    if ref is None:
        return []
    kind, cid = ref
    rows: List[Dict[str, Any]] = []
    offset = 0
    for _page in range(COMMENT_PAGE_CAP):
        api = (
            f"{_COMMENT_API}/{kind}/{cid}/root_comments"
            f"?limit={COMMENT_PAGE_LIMIT}&offset={offset}&order=normal&status=open"
        )
        try:
            resp = session.get(api, headers=_COMMENT_HEADERS, timeout=(15, 60))
        except requests.RequestException as e:
            logger.warning("  comments request failed %s/%s: %s", kind, cid, e)
            return rows
        if resp.status_code != 200:
            logger.warning("  comments HTTP %d for %s/%s", resp.status_code, kind, cid)
            return rows
        try:
            data = resp.json()
        except ValueError:
            logger.warning("  comments JSON parse error: %s/%s", kind, cid)
            return rows
        page_items = data.get("data") or []
        rows.extend(_comment_rows(page_items))
        paging = data.get("paging") or {}
        if paging.get("is_end") or not page_items:
            break
        offset += COMMENT_PAGE_LIMIT
        time.sleep(random.uniform(sleep_sec * 0.8, sleep_sec * 1.2))
    return rows


def with_comments_for_items(
    items: List[Dict[str, Any]],
    *,
    sleep_sec: float = COMMENT_SLEEP_SEC,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch comments for every *items* entry that reports CommentCount > 0.

    Returns {ContentID: [comment rows]} (PK-deduped per item — the same
    comment id can never appear twice within one item). Items without a
    parseable content ref or with zero comments are absent from the result.
    """
    session = requests.Session()
    out: Dict[str, List[Dict[str, Any]]] = {}
    fetched = skipped = 0
    for it in items:
        cid = (it.get("ContentID") or "").strip()
        try:
            n_comments = int(it.get("CommentCount") or 0)
        except (TypeError, ValueError):
            n_comments = 0
        if not cid or n_comments <= 0:
            skipped += 1
            continue
        rows = fetch_item_comments(session, it.get("Url") or "", sleep_sec=sleep_sec)
        # PK dedupe within the item: the flat list may repeat an id when the
        # API returns overlapping pages — a batch with duplicate PKs would
        # make the build's upsert raise "cannot affect row a second time".
        deduped: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            deduped.setdefault(row["comment_id"], row)
        out[cid] = list(deduped.values())
        fetched += 1
        logger.info("  comments %s: %d row(s) (api count=%s)",
                    cid, len(out[cid]), n_comments)
        time.sleep(random.uniform(sleep_sec * 0.8, sleep_sec * 1.2))
    if fetched or skipped:
        logger.info("  comments fetched for %d item(s), skipped %d without comments",
                    fetched, skipped)
    return out


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
    with_comments: bool = True,
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
    # Cached skips are buffered silently and summarized as a single count
    # before the next real download, instead of one log line per skip — keeps
    # long backfill runs readable while still showing progress between real
    # downloads.
    cached_since_last_real = 0
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
                cached_since_last_real += 1
                continue

            # About to perform a real download — flush any buffered cached
            # skips so the user sees what was skipped between this and the
            # previous real download.
            if cached_since_last_real > 0:
                logger.info(
                    "[%d/%d] %d cached file(s) skipped since last download",
                    i, len(targets), cached_since_last_real,
                )
                cached_since_last_real = 0

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

            comments: List[Dict[str, Any]] = []
            if with_comments and accepted:
                comments_by_cid = with_comments_for_items(accepted)
                comments = [
                    {"content_id": cid, "comments": rows}
                    for cid, rows in comments_by_cid.items()
                ]

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
                comments=comments,
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
def _b64_arg(value: Optional[str]) -> Optional[str]:
    """Decode a base64(utf-8) CLI value (--question-b64 / --author-b64).

    The API layer passes question/author this way: runPythonModule joins argv
    with spaces inside a bash -lc command line, so raw free text (spaces,
    quotes, $) would be mangled by shell parsing.
    """
    if not value:
        return None
    import base64
    return base64.b64decode(value).decode("utf-8")


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
    parser.add_argument("--sleep-sec", type=float, default=None,
                        help="Anti-bot sleep between requests (s). Default: "
                             f"{LONG_SLEEP_INTERVAL} scheduled / {SEARCH_SLEEP_SEC} question search")
    parser.add_argument("--out-root", type=str, default=None,
                        help="Output root dir. Default: <project>/temps/zhihu_news")
    parser.add_argument("--force", action="store_true",
                        help="Force re-download of every target, including cache-skip (older) ones.")
    # One-off question mode (News page question bar): plain --question is for
    # humans; --question-b64/--author-b64 is what the API route sends (see
    # _b64_arg for why).
    parser.add_argument("--question", type=str, default=None,
                        help="One-off search for this raw question text instead of the scheduled download.")
    parser.add_argument("--question-b64", type=str, default=None,
                        help="Same as --question but base64(utf-8) encoded (API route).")
    parser.add_argument("--author", type=str, default=None,
                        help="Question mode: keep only items whose AuthorName matches (case-insensitive).")
    parser.add_argument("--author-b64", type=str, default=None,
                        help="Same as --author but base64(utf-8) encoded (API route).")
    parser.add_argument("--no-comments", action="store_true",
                        help="Skip the per-item comments fetch (default: ON — comments of every "
                             "kept item are stored in the artifact for text.news_comments).")
    args = parser.parse_args()

    if args.count < 1 or args.count > 10:
        parser.error("--count must be between 1 and 10")
    if args.lookback < 0:
        parser.error("--lookback must be >= 0")

    question = args.question or _b64_arg(args.question_b64)
    with_comments = not args.no_comments
    if question:
        search_zhihu_question(
            question.strip(),
            get_api_key(),
            author=_b64_arg(args.author_b64) or args.author,
            count=args.count,
            sleep_sec=args.sleep_sec if args.sleep_sec is not None else SEARCH_SLEEP_SEC,
            out_dir=resolve_out_dir(str(Path(__file__).resolve()), OUTPUT_DIR_NAME, args.out_root),
            with_comments=with_comments,
        )
        return

    summary = download_zhihu_news(
        out_root=args.out_root,
        start_date=args.start_date,
        lookback=args.lookback,
        keyword=args.keyword,
        count=args.count,
        sleep_sec=args.sleep_sec if args.sleep_sec is not None else LONG_SLEEP_INTERVAL,
        force=args.force,
        with_comments=with_comments,
    )
    logger.info(summary)


if __name__ == "__main__":
    main()
