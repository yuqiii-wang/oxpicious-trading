"""Download Shenzhen Stock Exchange (SZSE) per-stock DIVIDEND (分红转增信息) data.

Studies ``https://webapi.cninfo.com.cn/#/apiDoc`` → 分红转增信息, served by the
cninfo (巨潮资讯) unified disclosure platform endpoint::

    https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1139?sc000651

One request per stock returns the full 分红送转 (cash dividend + bonus shares +
capitalization) history. The endpoint covers BOTH exchanges, but this module
filters to SZSE-listed codes only (000xxx / 001xxx / 002xxx / 003xxx /
300xxx / 301xxx); SSE stocks use their own ``downloads.stock.sse.dividend``
module backed by the SSE commonQuery.do endpoint.

Anti-bot (two layers):
  1. cninfo mcode token — the API requires an ``Accept-Enckey`` header whose
     value is an AES-128-CBC encryption of the current unix timestamp, with
     key = iv = ``1234567887654321`` (replicated in pure Python from the
     cninfo.js ``getResCode1`` function — no JS runtime needed). The token
     is regenerated for every request because it is timestamp-bound.
  2. AntiBotProxy — the project's standard anti-bot layer (browser
     fingerprint rotation, random query param, sleep with jitter, 4xx host
     blocking detection). Same configuration as the SSE dividend module:
     ``rotate_browser_profile=True``, ``add_random_param=True``,
     ``enable_host_tracking=True``, ``sleep_jitter=0.3``, base sleep
     ``SUPER_LONG_SLEEP_INTERVAL`` (600s).

Response fields (cninfo p_sysapi1139 record):
  F018D — 实施方案公告日期     (announcement date, YYYY-MM-DD)
  F006D — 股权登记日           (record date, YYYY-MM-DD)
  F020D — 除权日               (ex-dividend date, YYYY-MM-DD)
  F012N — 派息比例             (cash dividend per 10 shares, yuan)
  F010N — 送股比例             (bonus shares per 10 shares)
  F011N — 转增比例             (capitalization shares per 10 shares)
  F007V — 实施方案分红说明     (human-readable description)
  F001V — 报告时间             (e.g. "2024年报")
  F044V — 分红类型             (e.g. "年度分红", "中期分红")
  F023D — 派息日               (payment date)
  F025D — 股份到账日           (share arrival date)

Outputs one CSV per stock under ``temps/szse_archive/``::

    {code}_dividend_{YYYYMMDD}.csv

The filename always carries the download date as a suffix for traceability.
The download script decides whether to re-download by checking:

  1. DB table ``stats.stock_dividends`` — if any row for this stock has
     ``last_updated`` within the last 180 days, the data is still fresh; skip.
  2. Local CSV — if any ``{code}_dividend_*.csv`` exists whose file
     modification time (mtime) is within the last 180 days, the data is
     still considered cached; skip.

CSV columns (same schema as the SSE dividend file for build-module
compatibility — cninfo does not provide post-tax dividend, total payout,
pre-close, open quote, or total shares, so those columns are left empty):

    公告日期, 证券代码, 证券简称, 股权登记日, 除息交易日,
    每股红利(含税)(元), 每股红利(税后)(元), 分红总额(万元),
    除息前日收盘价, 除息报价, 总股本(万股)

Notes:
  • cninfo returns 派息比例 per 10 shares; it is divided by 10 to match
    the SSE schema's per-share convention (yuan per single share).
  • 证券简称 (stock name) is NOT in the cninfo response — it is looked up
    from ``stats.stock_identity`` alongside the stock list and passed
    through. When the DB lookup is unavailable the column is left blank.
  • Resumable: a stock is skipped if (a) a local CSV whose file mtime is
    within the last 180 days exists, or (b) ``stats.stock_dividends`` already
    has rows for the stock with ``last_updated`` within the last 180 days.
    ``--force`` overrides both checks.

Usage:
  python -m downloads.stock.szse.dividend                  # ETF-held SZSE stocks only (default)
  python -m downloads.stock.szse.dividend --no-etf-filter  # all SZSE equities from DB
  python -m downloads.stock.szse.dividend --code 000651    # single stock
  python -m downloads.stock.szse.dividend --force          # overwrite existing files
"""
from __future__ import annotations


import argparse
import base64
import csv
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from downloads._common import (
    DEFAULT_TIMEOUT,
    SUPER_LONG_SLEEP_INTERVAL,
    MIN_VALID_BYTES,
    AntiBotConfig,
    AntiBotProxy,
    COMMON_BASE_HEADERS,
    is_valid_file,
    resolve_out_dir,
    setup_logger,
)

# --- Endpoint constants -----------------------------------------------------
CNINFO_DIVIDEND_URL = "https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1139"
CNINFO_ORIGIN = "https://webapi.cninfo.com.cn"
CNINFO_REFERER = "https://webapi.cninfo.com.cn/"

# AES key/iv for the mcode token — replicated from cninfo.js getResCode1().
_MCODE_KEY = b"1234567887654321"
_MCODE_IV = b"1234567887654321"

# Per-stock archive directory — same convention as SSE (temps/szse_archive/).
# Each stock gets one CSV per download date: {code}_dividend_{YYYYMMDD}.csv
DIVIDEND_DIRNAME = "szse_archive"

# Output columns (Chinese headers, mirroring the SSE dividend file schema so
# the build module can read both exchanges with one code path).
DIVIDEND_COLUMNS: List[str] = [
    "公告日期",
    "证券代码",
    "证券简称",
    "股权登记日",
    "除息交易日",
    "每股红利(含税)(元)",
    "每股红利(税后)(元)",
    "分红总额(万元)",
    "除息前日收盘价",
    "除息报价",
    "总股本(万股)",
]

# SZSE-exclusive code prefixes (000/001 also used by SSE indices, but here we
# only query codes sourced from stats.stock_identity with exchange='SZ',
# so ambiguity is resolved upstream by the DB).
SZSE_CODE_PREFIXES = ("000", "001", "002", "003", "300", "301")

logger = setup_logger("szse_dividend_download")


# ---------------------------------------------------------------------------
# Freshness check — skip download if data is already up-to-date
# ---------------------------------------------------------------------------
# Dividend history is slow-moving (a stock pays dividends at most a few times
# per year), and main.sh schedules this download quarterly. A 180-day
# freshness window therefore safely skips re-downloading stocks that have
# already been fetched within the last half year, regardless of whether the
# CSV was written today or months ago.
#
# The local-cache check uses the file's modification time (mtime) rather than
# the date suffix in the filename — this is more robust because mtime
# reflects the actual last-write time (e.g. a file rewritten today with the
# same date suffix, or a file copied across machines) and is not sensitive
# to filename formatting.
FRESHNESS_WINDOW_DAYS = 180


def _today_str() -> str:
    """Return today's date as YYYYMMDD string (for CSV filename suffix)."""
    return date.today().strftime("%Y%m%d")


def _freshness_cutoff() -> date:
    """Return the earliest date still considered fresh (today minus 180 days)."""
    return date.today() - timedelta(days=FRESHNESS_WINDOW_DAYS)


def _is_fresh_local(archive_dir: Path, code: str) -> bool:
    """Check if a local dividend CSV whose mtime is within the last 180 days exists.

    Scans ``archive_dir`` for any ``{code}_dividend_*.csv`` whose file
    modification time (mtime) is at or after ``today -
    FRESHNESS_WINDOW_DAYS``. The mtime reflects when the file was last
    written, so the date suffix in the filename no longer needs to be parsed
    — only the actual last-write time matters.

    Uses a 1-byte minimum (not MIN_VALID_BYTES=1024) because dividend CSVs
    for stocks with few events can be <700 bytes — still valid.
    """
    if not archive_dir.exists():
        return False
    cutoff = _freshness_cutoff()
    for f in archive_dir.glob(f"{code}_dividend_*.csv"):
        try:
            file_date = date.fromtimestamp(f.stat().st_mtime)
        except OSError:
            continue
        if file_date >= cutoff and is_valid_file(f, min_bytes=1):
            return True
    return False


def _is_fresh_db(conn, full_code: str) -> bool:
    """Check if ``stats.stock_dividends`` has rows for this stock updated
    within the last 180 days.

    ``full_code`` is the DB-format code with exchange suffix (e.g. ``000651.SZ``).
    Returns True if at least one row exists with ``last_updated::date >=
    today - FRESHNESS_WINDOW_DAYS``.
    """
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM stats.stock_dividends "
                "WHERE code = %s AND last_updated::date >= %s LIMIT 1",
                (full_code, _freshness_cutoff()),
            )
            return cur.fetchone() is not None
    except Exception as e:
        logger.debug("DB freshness check failed for %s: %s", full_code, e)
        return False


# ---------------------------------------------------------------------------
# cninfo mcode token — AES-128-CBC encryption of the unix timestamp
# ---------------------------------------------------------------------------
def _generate_mcode() -> str:
    """Generate the cninfo ``Accept-Enckey`` token.

    Replicates ``getResCode1()`` from cninfo.js: AES-128-CBC encrypts the
    current unix timestamp (seconds, as a UTF-8 string) with key = iv =
    ``1234567887654321``, then returns the base64-encoded ciphertext.
    The token is timestamp-bound so it must be regenerated per request.
    """
    plaintext = str(int(time.time())).encode("utf-8")
    cipher = AES.new(_MCODE_KEY, AES.MODE_CBC, _MCODE_IV)
    encrypted = cipher.encrypt(pad(plaintext, AES.block_size))
    return base64.b64encode(encrypted).decode("utf-8")


def _build_cninfo_headers() -> Dict[str, str]:
    """Build cninfo request headers with a fresh mcode token.

    The Accept-Enckey (mcode) is regenerated on every call because it is
    timestamp-bound. Browser fingerprint fields (User-Agent, Sec-Ch-Ua, etc.)
    are left for AntiBotProxy.merge_browser_profile to overlay.
    """
    h = dict(COMMON_BASE_HEADERS)
    h.update({
        "Accept": "*/*",
        "Accept-Enckey": _generate_mcode(),
        "Origin": CNINFO_ORIGIN,
        "Referer": CNINFO_REFERER,
        "X-Requested-With": "XMLHttpRequest",
    })
    return h


# ---------------------------------------------------------------------------
# Fetch + parse
# ---------------------------------------------------------------------------
def _fetch_dividend(
    session: requests.Session,
    scode: str,
    proxy: AntiBotProxy,
) -> Optional[Dict[str, Any]]:
    """Fetch the full dividend history for one stock from cninfo.

    Returns the parsed JSON payload (a dict with ``records`` etc.), or None
    on fetch failure / blocked host. A single request returns ALL dividend
    rows for the stock — the endpoint does not paginate.
    """
    # Fresh mcode per request — timestamp-bound token.
    headers = _build_cninfo_headers()
    resp = proxy.get(
        session,
        CNINFO_DIVIDEND_URL,
        params={"scode": scode},
        headers=headers,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag=f"[dividend {scode}]",
    )
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        logger.warning("  [dividend %s] non-JSON response: %s", scode, resp.text[:200])
        return None


def _parse_dividend_row(
    raw: Dict[str, Any], scode: str, name: str
) -> Dict[str, Any]:
    """Map one cninfo dividend record to the CSV row schema.

    cninfo returns 派息比例 per 10 shares; it is divided by 10 to match the
    SSE schema's per-share convention. Date fields are already ``YYYY-MM-DD``
    strings (no conversion needed, unlike SSE's ``YYYYMMDD``).
    """
    def _num(val: Any) -> Any:
        if val is None or val == "" or val == "-":
            return ""
        try:
            return round(float(val) / 10.0, 6)  # per 10 shares → per share
        except (TypeError, ValueError):
            return ""

    def _date(val: Any) -> str:
        s = str(val).strip() if val is not None else ""
        return s if s and s != "-" else ""

    return {
        "公告日期": _date(raw.get("F018D")),
        "证券代码": scode,
        "证券简称": name,
        "股权登记日": _date(raw.get("F006D")),
        "除息交易日": _date(raw.get("F020D")),
        "每股红利(含税)(元)": _num(raw.get("F012N")),
        "每股红利(税后)(元)": "",  # cninfo does not provide post-tax
        "分红总额(万元)": "",       # cninfo does not provide total payout
        "除息前日收盘价": "",       # cninfo does not provide pre-close
        "除息报价": "",             # cninfo does not provide ex-div open
        "总股本(万股)": "",         # cninfo does not provide total shares
    }


def _write_dividend_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    """(Re)write the dividend CSV, sorted ascending by 除息交易日."""
    rows_sorted = sorted(
        rows, key=lambda r: (r.get("除息交易日") or "", r.get("股权登记日") or "")
    )
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=DIVIDEND_COLUMNS, extrasaction="ignore"
        )
        writer.writeheader()
        for r in rows_sorted:
            writer.writerow(r)


# ---------------------------------------------------------------------------
# Stock list — ETF-held SZSE stocks from the DB
# ---------------------------------------------------------------------------
def load_target_szse_stocks(conn) -> List[Tuple[str, str]]:
    """Return [(bare_code, name), ...] for SZSE stocks held by ETFs.

    Mirrors ``downloads.stock.sse.archive.load_target_stocks`` but filters
    to SZSE codes via ``stock_identity.exchange = 'SZ'``. Joins the latest
    stock_identity snapshot with the latest ETF composition snapshot in
    ``stats.sec_composition``. ``stock_code`` in sec_composition carries the
    exchange suffix (e.g. "000001.SZ"), matching stock_identity.code.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(date) FROM stats.stock_identity WHERE exchange = 'SZ'"
        )
        latest_id_date = cur.fetchone()[0]
        cur.execute(
            "SELECT MAX(snapshot_date) FROM stats.sec_composition "
            "WHERE source_type = 'etf'"
        )
        latest_snapshot = cur.fetchone()[0]

        if latest_id_date is None or latest_snapshot is None:
            logger.warning(
                "load_target_szse_stocks: missing latest dates (id=%s, snapshot=%s)",
                latest_id_date, latest_snapshot,
            )
            return []

        cur.execute(
            """
            WITH etf_targets AS (
                SELECT DISTINCT stock_code
                  FROM stats.sec_composition
                 WHERE source_type = 'etf'
                   AND snapshot_date = %s
            )
            SELECT si.code, si.name
              FROM stats.stock_identity si
              JOIN etf_targets t ON t.stock_code = si.code
             WHERE si.exchange = 'SZ'
               AND si.date = %s
             ORDER BY si.code
            """,
            (latest_snapshot, latest_id_date),
        )
        rows = cur.fetchall()

    stocks: List[Tuple[str, str]] = []
    for r in rows:
        full_code = r[0]
        nm = r[1] or ""
        bare = full_code.split(".")[0]
        stocks.append((bare, nm))
    return stocks


def _load_all_szse_stocks_from_db(conn) -> List[Tuple[str, str]]:
    """Return [(bare_code, name), ...] for ALL SZSE stocks in stock_identity.

    Used when ``--no-etf-filter`` is set. Queries the latest stock_identity
    date for exchange='SZ' and returns every stock on that date.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(date) FROM stats.stock_identity WHERE exchange = 'SZ'"
        )
        latest_date = cur.fetchone()[0]
        if latest_date is None:
            return []
        cur.execute(
            "SELECT code, name FROM stats.stock_identity "
            "WHERE exchange = 'SZ' AND date = %s ORDER BY code",
            (latest_date,),
        )
        rows = cur.fetchall()
    return [(r[0].split(".")[0], r[1] or "") for r in rows]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def download_szse_dividends(
    out_root: Optional[str] = None,
    *,
    sleep_sec: float = SUPER_LONG_SLEEP_INTERVAL,
    force: bool = False,
    session: Optional[requests.Session] = None,
    etf_filter: bool = True,
    code_filter: Optional[str] = None,
    batch_size: int = 20,
    batch_pause_sec: float = 70 * 60,
) -> dict:
    """Download SZSE per-stock dividend (分红转增信息) data from cninfo.

    For each SZSE stock (default: ETF-held only; ``--no-etf-filter`` for all
    in DB), fetches the full dividend history via the cninfo
    ``p_sysapi1139`` endpoint and writes one CSV per stock under
    ``temps/szse_archive/{code}_dividend_{YYYYMMDD}.csv``.

    Anti-bot: cninfo mcode token (AES-128-CBC of timestamp, regenerated per
    request) + AntiBotProxy (browser fingerprint rotation, random param,
    sleep with jitter, host blocking detection, base sleep 90s).

    Batch pause: after every ``batch_size`` successful dividend fetches
    (default 20), the downloader sleeps ``batch_pause_sec`` (default 70
    minutes) before continuing, as an extra anti-bot cool-down. Set
    ``batch_pause_sec=0`` (or ``--batch-pause-sec 0``) to disable.

    Resumable: a stock is skipped if (a) a local CSV whose file mtime is
    within the last 180 days exists, or (b) ``stats.stock_dividends`` already
    has rows for the stock with ``last_updated`` within the last 180 days.
    ``--force`` overrides both checks.
    """
    archive_dir = resolve_out_dir(
        str(Path(__file__).resolve()), DIVIDEND_DIRNAME, out_root
    )
    sess = session or requests.Session()
    today_suffix = _today_str()

    # Resolve the target stock list.
    # ``--code`` overrides everything (single-stock mode).
    # ``--no-etf-filter`` fetches all SZSE stocks from stock_identity.
    # Default: ETF-held SZSE stocks via load_target_szse_stocks (DB-backed).
    db_conn = None
    if code_filter:
        bare = code_filter.split(".")[0]
        stocks: List[Tuple[str, str]] = [(bare, "")]
        logger.info("Single-stock mode: %s", code_filter)
    else:
        from _common.db_commons import get_db_connection
        db_conn = get_db_connection()
        try:
            if etf_filter:
                stocks = load_target_szse_stocks(db_conn)
            else:
                stocks = _load_all_szse_stocks_from_db(db_conn)
        except Exception:
            db_conn.close()
            db_conn = None
            raise
    if not stocks:
        logger.error("No stocks found")
        if db_conn:
            db_conn.close()
        return {"downloaded": 0, "failed": 1, "archive_dir": str(archive_dir)}

    # Freshness check — skip stocks that are already up-to-date.
    # Checks BOTH: (a) local CSV whose mtime is within the last 180 days,
    # (b) DB rows with last_updated within the last 180 days. Skips the
    # stock if either is fresh (unless --force).
    if not force:
        fresh_local = 0
        fresh_db = 0
        filtered: List[Tuple[str, str]] = []
        for code, name in stocks:
            if _is_fresh_local(archive_dir, code):
                fresh_local += 1
                continue
            if db_conn and _is_fresh_db(db_conn, f"{code}.SZ"):
                fresh_db += 1
                continue
            filtered.append((code, name))
        stocks = filtered
        logger.info(
            "Freshness check (%d-day window): %d stocks skipped (local CSV fresh), "
            "%d skipped (DB fresh), %d to process.",
            FRESHNESS_WINDOW_DAYS, fresh_local, fresh_db, len(stocks),
        )
    if db_conn:
        db_conn.close()

    logger.info(
        "Starting SZSE dividend download: %d stocks. "
        "Per-stock: fetch dividend history -> write {code}_dividend_%s.csv",
        len(stocks), today_suffix,
    )

    # cninfo endpoint — quarterly cadence so we use the long 90s sleep.
    # The mcode token is handled separately in _build_cninfo_headers();
    # AntiBotProxy handles fingerprint rotation, random param, sleep, and
    # host blocking.
    dividend_proxy = AntiBotProxy(AntiBotConfig(
        base_sleep_sec=sleep_sec,
        sleep_jitter=0.3,
        rotate_browser_profile=True,
        add_random_param=True,
        enable_host_tracking=True,
        timeout=DEFAULT_TIMEOUT,
    ))

    stocks_done = 0
    stocks_failed = 0
    rows_fetched = 0
    rows_empty = 0
    # Counter for the periodic long cool-down: every ``batch_size``
    # successful dividend fetches (empty or not), sleep ``batch_pause_sec``
    # before continuing. Failed fetches don't count toward the batch.
    downloads_since_pause = 0

    for idx, (code, name) in enumerate(stocks):
        if dividend_proxy.is_blocked(CNINFO_DIVIDEND_URL):
            logger.warning(
                "  [host-blocked] webapi.cninfo.com.cn blocked, stopping"
            )
            stocks_failed += len(stocks) - idx
            break

        if (idx + 1) % 25 == 0 or idx == 0:
            logger.info(
                "Stock %d/%d: %s %s", idx + 1, len(stocks), code, name,
            )

        out_file = archive_dir / f"{code}_dividend_{today_suffix}.csv"

        payload = _fetch_dividend(sess, code, dividend_proxy)

        if payload is None:
            stocks_failed += 1
            continue

        records = payload.get("records") or []
        if not records:
            logger.info("  -> %s dividend: no data", code)
            rows_empty += 1
            # Write an empty marker file so the freshness check skips it
            # next time. This avoids re-fetching stocks with no dividend history.
            if not out_file.exists():
                _write_dividend_csv(out_file, [])
        else:
            parsed_rows = [
                _parse_dividend_row(r, code, name) for r in records
            ]
            # Dedupe by 除息交易日 — keep the first occurrence.
            seen_dates: set = set()
            deduped_rows: List[Dict[str, Any]] = []
            for r in parsed_rows:
                key = (r.get("除息交易日"), r.get("股权登记日"))
                if key in seen_dates:
                    continue
                seen_dates.add(key)
                deduped_rows.append(r)

            _write_dividend_csv(out_file, deduped_rows)
            rows_fetched += len(deduped_rows)
            stocks_done += 1

            logger.info(
                "  -> %s dividend: %d rows -> %s",
                code, len(deduped_rows), out_file.name,
            )

        # One successful dividend fetch completed — count it toward the
        # periodic batch cool-down (covers both empty and non-empty results,
        # since each is a real server request).
        downloads_since_pause += 1

        if (
            batch_pause_sec > 0
            and batch_size > 0
            and downloads_since_pause >= batch_size
        ):
            remaining = len(stocks) - (idx + 1)
            if remaining > 0:
                logger.info(
                    "  [batch pause] %d dividends downloaded; "
                    "sleeping %.0f min before continuing "
                    "(%d stocks remaining).",
                    downloads_since_pause,
                    batch_pause_sec / 60.0,
                    remaining,
                )
                time.sleep(batch_pause_sec)
            downloads_since_pause = 0

    logger.info(
        "Done SZSE dividend download. stocks_done=%d stocks_failed=%d "
        "rows_fetched=%d stocks_empty=%d archive_dir=%s",
        stocks_done, stocks_failed, rows_fetched, rows_empty, archive_dir,
    )
    return {
        "downloaded": stocks_done,
        "failed": stocks_failed,
        "rows_fetched": rows_fetched,
        "stocks_empty": rows_empty,
        "archive_dir": str(archive_dir),
        "total_stocks": len(stocks),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download SZSE per-stock dividend (分红转增信息) data from "
                    "cninfo (webapi.cninfo.com.cn p_sysapi1139). Anti-bot: "
                    "AES-128-CBC mcode token (regenerated per request) + "
                    "AntiBotProxy (fingerprint rotation, random param, "
                    "sleep jitter, host blocking, base sleep 90s). "
                    "Output: {code}_dividend_{YYYYMMDD}.csv under temps/szse_archive/. "
                    "Skips stocks with a local CSV whose mtime is within the "
                    "last 180 days or DB rows updated within the last 180 days."
    )
    ap.add_argument(
        "--no-etf-filter", action="store_true",
        help="Disable the ETF filter and fetch ALL SZSE stocks from "
             "stats.stock_identity instead.",
    )
    ap.add_argument(
        "--code", type=str, default=None,
        help="Single-stock mode: download dividend data for one stock "
             "(bare 6-digit code or suffixed). Overrides --no-etf-filter.",
    )
    ap.add_argument(
        "--force", action="store_true",
        help="Force download: ignore freshness checks (local CSV + DB "
             "last_updated) and re-download all stocks.",
    )
    ap.add_argument(
        "--batch-size", type=int, default=20,
        help="Number of successful dividend fetches between long cool-down "
             "pauses (default: 20).",
    )
    ap.add_argument(
        "--batch-pause-sec", type=float, default=70 * 60,
        help="Long cool-down pause in seconds applied every --batch-size "
             "downloads (default: 4200 = 70 min). Set to 0 to disable the "
             "batch pause entirely.",
    )
    args = ap.parse_args()
    print(download_szse_dividends(
        force=args.force,
        etf_filter=not args.no_etf_filter,
        code_filter=args.code,
        batch_size=args.batch_size,
        batch_pause_sec=args.batch_pause_sec,
    ))
