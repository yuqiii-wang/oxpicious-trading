from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import pandas as pd

from downloads._common.core import (
    MIN_VALID_BYTES,
    DEFAULT_START_DATE,
    DEFAULT_SLEEP_SEC,
    COMMON_BASE_HEADERS,
    AntiBotProxy,
    AntiBotConfig,
    setup_logger,
    resolve_out_dir,
    parse_date_window,
    is_valid_file,
    is_fresh_today,
    is_error_html,
    safe_write_bytes,
    build_default_session,
    convert_xlsx_to_csv,
    read_csv_preferred,
    RunStats,
    merge_browser_profile,
    load_classification_index_names,
)

# SZSE indices that must NOT be downloaded from csindex.com.cn
# (they are covered by download_szse_trend.py via East Money API)
CSINDEX_SKIP_CODES = {"399001", "399006", "399237"}

# ---------------------------------------------------------------------------
# csindex.com.cn API endpoints
#
# The website (https://www.csindex.com.cn/zh-CN/indices/index#/indices/family/detail?indexCode=000300)
# is a Single Page Application. Its axios client uses baseURL "/csindex-home".
# All data (chart, export, intraday, PE) is served as JSON/Excel via these endpoints.
# ---------------------------------------------------------------------------

CSINDEX_BASE = "https://www.csindex.com.cn"

#-- POST export: daily OHLCV + amount as Excel (body must be a JSON array)
API_EXPORT_PERF = CSINDEX_BASE + "/csindex-home/exportExcel/downloadindex-perf"
API_EXPORT_PERF_TESHU = CSINDEX_BASE + "/csindex-home/exportExcel/downloadindex-perf-teshu"

# GET historical PE (peg) series — supports long date ranges
API_INDEX_CSI_DS_PE = CSINDEX_BASE + "/csindex-home/perf/indexCsiDsPe"

# GET latest-day intraday granular ticks (~15s intervals throughout the trading day)
API_INDEX_PERF_ONEDAY = CSINDEX_BASE + "/csindex-home/perf/index-perf-oneday"

# Static indicator xls (PE1/PE2/dividend yield, ~1 month recent data) — supplemental
INDICATOR_XLS_TEMPLATE = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/uploads/file/autofile/indicator/{indexCode}indicator.xls"
)

DETAIL_REFERER = CSINDEX_BASE + "/zh-CN/indices/index#/indices/family/detail?indexCode={indexCode}"

UPDATE_WINDOW_DAYS = 35  # ~1 month plus weekend/holiday buffer
SLEEP_SEC = DEFAULT_SLEEP_SEC
CSINDEX_TIMEOUT: Tuple[int, int] = (15, 120)

CSINDEX_HEADERS: Dict[str, str] = dict(COMMON_BASE_HEADERS)
CSINDEX_HEADERS["Accept"] = "application/json, text/plain, */*"

EXPORT_HEADERS: Dict[str, str] = dict(CSINDEX_HEADERS)
EXPORT_HEADERS["Content-Type"] = "application/json"

logger = setup_logger("csindex_download")


def build_session() -> requests.Session:
    return build_default_session(merge_browser_profile(CSINDEX_HEADERS))


def _ymd(d: date) -> str:
    """Format date as YYYYMMDD (the API date format, no hyphens)."""
    return d.strftime("%Y%m%d")


def _detail_referer(index_code: str) -> str:
    return DETAIL_REFERER.format(indexCode=index_code)


# ---------------------------------------------------------------------------
# 1. Export Excel download (OHLCV + amount)
# ---------------------------------------------------------------------------

def download_export_excel(
    session: requests.Session,
    index_code: str,
    start_date: date,
    end_date: date,
    out_file: Path,
    proxy: Optional[AntiBotProxy] = None,
) -> bool:
    """Download daily OHLCV+amount history via the POST export Excel endpoint.

    The body must be a JSON **array** (a single object returns HTTP 500).
    Some "special" indices (e.g. 000010) require the ``-teshu`` variant.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))
    
    body = json.dumps([
        {"indexCode": index_code, "startDate": _ymd(start_date), "endDate": _ymd(end_date)}
    ])
    base_headers = dict(EXPORT_HEADERS)
    base_headers["Referer"] = _detail_referer(index_code)

    for url, label in ((API_EXPORT_PERF, "regular"), (API_EXPORT_PERF_TESHU, "teshu")):
        resp = proxy.post(
            session,
            url,
            params={"language": "CH"},
            data=body,
            headers=base_headers,
            timeout=CSINDEX_TIMEOUT,
            logger=logger,
            log_tag=f"  [export {label} {index_code}]",
        )
        if resp is None:
            continue

        ctype = resp.headers.get("Content-Type", "")
        content = resp.content

        # Successful export returns a binary Excel (zip) file
        is_xlsx = content[:4] == b"PK\x03\x04" or "excel" in ctype.lower() or "octet-stream" in ctype.lower()
        if is_xlsx and len(content) >= MIN_VALID_BYTES:
            tag = f"[export {label} {index_code} {_ymd(start_date)}~{_ymd(end_date)}]"
            if safe_write_bytes(out_file, content, min_bytes=MIN_VALID_BYTES, logger=logger, log_tag=tag):
                return True
            continue

        # Check for error HTML (anti-bot block page)
        if is_error_html(ctype, content):
            logger.warning("  [export-%s] %s got error HTML response (blocked?)", label, index_code)
            proxy.record_error(url, 403, "error_html_detected")
            continue

        # Otherwise it's likely a JSON error — try the teshu variant
        try:
            payload = resp.json()
            msg = payload.get("msg") or payload.get("message") or ""
            logger.debug("  [export-%s] %s returned JSON: code=%s msg=%s", label, index_code, payload.get("code"), msg)
        except (ValueError, AttributeError):
            logger.debug("  [export-%s] %s returned non-Excel, non-JSON (%d bytes)", label, index_code, len(content))
        continue

    logger.error("  [export-failed] %s %s~%s (both regular and teshu exhausted)", index_code, start_date, end_date)
    return False


# ---------------------------------------------------------------------------
# 2. PE (peg) historical series
# ---------------------------------------------------------------------------

def fetch_pe_series(
    session: requests.Session,
    index_code: str,
    start_date: date,
    end_date: date,
    proxy: Optional[AntiBotProxy] = None,
) -> List[Dict[str, Any]]:
    """Fetch historical PE (peg) series via the indexCsiDsPe endpoint.

    The ``peg`` field in csindex's API is a PE ratio variant (not the standard
    PEG ratio), with values in the 10-30 range typical of P/E. We treat it as PE.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))

    params = {
        "indexCode": index_code,
        "startDate": _ymd(start_date),
        "endDate": _ymd(end_date),
    }
    base_headers = dict(CSINDEX_HEADERS)
    base_headers["Referer"] = _detail_referer(index_code)

    resp = proxy.get(
        session,
        API_INDEX_CSI_DS_PE,
        params=params,
        headers=base_headers,
        timeout=CSINDEX_TIMEOUT,
        logger=logger,
        log_tag=f"  [pe-fetch {index_code}]",
    )
    if resp is None:
        logger.warning("  [pe-fetch] %s: request failed", index_code)
        return []

    ctype = resp.headers.get("Content-Type", "")
    if is_error_html(ctype, resp.content):
        logger.warning("  [pe-fetch] %s: got error HTML response (blocked?)", index_code)
        proxy.record_error(API_INDEX_CSI_DS_PE, 403, "error_html_detected")
        return []

    try:
        payload = resp.json()
    except ValueError as e:
        logger.warning("  [pe-fetch] %s: json parse error: %s", index_code, e)
        return []

    if payload.get("code") != "200":
        logger.warning("  [pe-fetch] %s: code=%s msg=%s", index_code, payload.get("code"), payload.get("msg"))
        return []

    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return data


def load_pe_cache(pe_cache_file: Path) -> Optional[List[Dict[str, Any]]]:
    """Load cached PE records from JSON file. Returns None if invalid."""
    if not is_valid_file(pe_cache_file, min_bytes=MIN_VALID_BYTES):
        return None
    try:
        with pe_cache_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        logger.warning("  [pe-cache] %s: unexpected JSON structure, ignoring cache", pe_cache_file.name)
        return None
    except (ValueError, OSError) as e:
        logger.warning("  [pe-cache] %s: load error: %s", pe_cache_file.name, e)
        return None


def save_pe_cache(pe_cache_file: Path, pe_records: List[Dict[str, Any]]) -> bool:
    """Persist PE records to JSON file for future skip."""
    try:
        with pe_cache_file.open("w", encoding="utf-8") as f:
            json.dump(pe_records, f, ensure_ascii=False)
        return True
    except OSError as e:
        logger.warning("  [pe-cache] %s: save error: %s", pe_cache_file.name, e)
        return False


# ---------------------------------------------------------------------------
# 3. Intraday granular ticks (1-day data)
# ---------------------------------------------------------------------------

def fetch_intraday(
    session: requests.Session,
    index_code: str,
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch latest trading-day intraday ticks via index-perf-oneday.

    Returns ``intraDayHeader`` (snapshot) + ``intraDayPerfList`` (ticks at ~15s
    intervals). The endpoint always returns the **latest** trading day regardless
    of a ``tradeDate`` parameter.

    NOTE: The user's instruction says "parse html to extract granular interval
    movements". The SPA does not embed intraday ticks in HTML — the data is
    served exclusively via this JSON endpoint (the data source behind the
    website's intraday chart). We parse the JSON directly.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))
    
    base_headers = dict(CSINDEX_HEADERS)
    base_headers["Referer"] = _detail_referer(index_code)

    resp = proxy.get(
        session,
        API_INDEX_PERF_ONEDAY,
        params={"indexCode": index_code},
        headers=base_headers,
        timeout=CSINDEX_TIMEOUT,
        logger=logger,
        log_tag=f"  [intraday-fetch {index_code}]",
    )
    if resp is None:
        logger.warning("  [intraday-fetch] %s: request failed", index_code)
        return None

    ctype = resp.headers.get("Content-Type", "")
    if is_error_html(ctype, resp.content):
        logger.warning("  [intraday-fetch] %s: got error HTML response (blocked?)", index_code)
        proxy.record_error(API_INDEX_PERF_ONEDAY, 403, "error_html_detected")
        return None

    try:
        payload = resp.json()
    except ValueError as e:
        logger.warning("  [intraday-fetch] %s: json parse error: %s", index_code, e)
        return None

    if payload.get("code") != "200":
        logger.warning("  [intraday-fetch] %s: code=%s msg=%s", index_code, payload.get("code"), payload.get("msg"))
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    return data


def save_intraday(
    data: Dict[str, Any],
    index_code: str,
    index_name: str,
    out_dir: Path,
) -> Optional[Path]:
    """Parse intraday ticks and save to CSV. Returns None if no ticks available."""
    tick_list = data.get("intraDayPerfList") or []
    if not tick_list:
        logger.info("  [intraday] %s: no granular ticks available, skipping", index_code)
        return None

    header = data.get("intraDayHeader") or {}
    # tradeDate in oneday response uses hyphens (YYYY-MM-DD); normalize to YYYYMMDD
    trade_date_raw = (header.get("tradeDate") or "").strip()
    trade_date_clean = trade_date_raw.replace("-", "")
    if not trade_date_clean and tick_list:
        trade_date_clean = str(tick_list[0].get("tradeDate") or "").replace("-", "")
    if not trade_date_clean:
        return None

    # Skip if intraday file for this date already exists
    out_file = out_dir / f"{index_code}_intraday_{trade_date_clean}.csv"
    if out_file.exists() and out_file.stat().st_size >= MIN_VALID_BYTES:
        logger.info("  [intraday] %s: %s already cached, skipping", index_code, out_file.name)
        return out_file

    rows = []
    for tick in tick_list:
        rows.append({
            "date": str(tick.get("tradeDate") or "").replace("-", ""),
            "time": tick.get("tradeTime") or "",
            "current": tick.get("current"),
            "high": tick.get("high"),
            "low": tick.get("low"),
            "close": tick.get("close"),
            "change": tick.get("change"),
            "changePct": tick.get("changePct"),
        })

    df = pd.DataFrame(rows, columns=["date", "time", "current", "high", "low", "close", "change", "changePct"])
    df.to_csv(out_file, index=False, encoding="utf-8-sig")

    # Save header snapshot alongside (for reference: openToday, closePre, tradingVol, tradingValue)
    snap_file = out_dir / f"{index_code}_intraday_{trade_date_clean}_snapshot.json"
    try:
        snap_file.write_text(json.dumps(header, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass

    logger.info("  [intraday] saved %s (%d ticks, date=%s)", out_file.name, len(df), trade_date_clean)
    return out_file


# ---------------------------------------------------------------------------
# 4. Merge: from2020 export + 1m export + PE -> history CSV
# ---------------------------------------------------------------------------

def _normalize_export_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize the bilingual column names from the export Excel to standard names.

    The export with ``language=CH`` has bilingual headers like ``日期 Date``,
    ``开盘价 Open``, ``成交量 Volume``, etc.
    """
    rename_map: Dict[Any, Any] = {}
    for col in df.columns:
        s = str(col)
        sl = s.lower()
        if "日期" in s or sl == "date":
            rename_map[col] = "date"
        elif "代码" in s and "code" in sl:
            rename_map[col] = "indexCode"
        elif "中文全称" in s or "chinese name" in sl:
            rename_map[col] = "indexNameCnAll"
        elif "中文简称" in s:
            rename_map[col] = "indexNameCn"
        elif "英文全称" in s or "english name" in sl:
            rename_map[col] = "indexNameEnAll"
        elif "英文简称" in s:
            rename_map[col] = "indexNameEn"
        elif "开盘" in s or sl == "open":
            rename_map[col] = "open"
        elif "最高" in s or sl == "high":
            rename_map[col] = "high"
        elif "最低" in s or sl == "low":
            rename_map[col] = "low"
        elif "收盘" in s or sl == "close":
            rename_map[col] = "close"
        elif "涨跌幅" in s or "change%" in sl or "changepct" in sl or "change(" in sl:
            rename_map[col] = "changePct"
        elif "涨跌" in s or sl == "change":
            rename_map[col] = "change"
        elif "成交量" in s or "volume" in sl:
            rename_map[col] = "volume"
        elif "成交金额" in s or "turnover" in sl or "amount" in sl:
            rename_map[col] = "amount"
        elif "样本" in s or "cons" in sl:
            rename_map[col] = "consNumber"
    return df.rename(columns=rename_map)


def _clean_date(val: Any) -> str:
    """Normalize a date value to YYYYMMDD string.

    Handles: "20240101", "20240101.0" (from numeric conversion),
    "2024-01-01" (hyphenated), Excel date serials (45292).
    """
    s = str(val).strip()
    if not s or s == "nan":
        return ""
    # Strip trailing ".0" from numeric conversion
    if s.endswith(".0"):
        s = s[:-2]
    # Remove date separators
    s = s.replace("-", "").replace("/", "")
    # Handle Excel date serial numbers (e.g., 45292 -> 2024-01-01)
    if s.isdigit() and len(s) <= 5:
        try:
            serial = int(s)
            if 30000 <= serial <= 80000:
                dt = datetime(1899, 12, 30) + timedelta(days=serial)
                return dt.strftime("%Y%m%d")
        except ValueError:
            pass
    return s


def build_history_csv(
    index_code: str,
    index_name: str,
    out_dir: Path,
    pe_records: List[Dict[str, Any]],
) -> Optional[Path]:
    """Merge from2020 export + 1m export + PE into a single daily history CSV.

    The 1m data overrides the from2020 data for overlapping dates (update/insert).
    PE (peg) is merged by date as a left join.
    """
    from2020_xlsx = out_dir / f"{index_code}_from2020.xlsx"
    onem_xlsx = out_dir / f"{index_code}_1m.xlsx"

    # Read with dtype=object to preserve original cell values
    df_from2020 = read_csv_preferred(from2020_xlsx, dtype=object, logger=logger, log_tag=f"[from2020 {index_code}]")
    df_1m = read_csv_preferred(onem_xlsx, dtype=object, logger=logger, log_tag=f"[1m {index_code}]")

    frames: List[pd.DataFrame] = []
    for df_raw in (df_from2020, df_1m):
        if df_raw is None or df_raw.empty:
            continue
        df = _normalize_export_columns(df_raw)
        if "date" in df.columns:
            df["date"] = df["date"].apply(_clean_date)
            df = df[df["date"].str.len() == 8]
        frames.append(df)

    if not frames:
        logger.warning("  [history] %s: no export data to build history", index_code)
        return None

    # Concatenate; 1m (last) overrides from2020 for overlapping dates
    df = pd.concat(frames, ignore_index=True)
    if "date" in df.columns:
        df = df.drop_duplicates(subset=["date"], keep="last")
        df = df.sort_values("date").reset_index(drop=True)

    # Zero-pad numeric indexCode to 6 digits (Excel stores it as number, stripping leading zeros)
    # Non-numeric codes like H30007 are kept as-is
    if "indexCode" in df.columns:
        def _fix_code(v: Any) -> str:
            s = str(v).strip().split(".")[0]
            if not s:
                return ""
            if s.isdigit():
                return s.zfill(6)
            return s
        df["indexCode"] = df["indexCode"].apply(_fix_code)

    # Merge PE (peg) by date
    if pe_records:
        df_pe = pd.DataFrame(pe_records)
        if "tradeDate" in df_pe.columns and "peg" in df_pe.columns:
            df_pe = df_pe[["tradeDate", "peg"]].copy()
            df_pe["tradeDate"] = df_pe["tradeDate"].astype(str).str.strip().replace("-", "", regex=False)
            df_pe = df_pe.rename(columns={"tradeDate": "date", "peg": "pe"})
            df_pe = df_pe.drop_duplicates(subset=["date"], keep="last")
            df = df.merge(df_pe, on="date", how="left")
        else:
            logger.warning("  [history] %s: PE records missing expected fields", index_code)

    # Ensure pe column exists even if PE fetch failed
    if "pe" not in df.columns:
        df["pe"] = None

    # Add index name
    df["indexName"] = index_name

    # Select and order final columns
    preferred_cols = [
        "date", "indexCode", "indexName",
        "open", "high", "low", "close",
        "volume", "amount", "change", "changePct",
        "pe", "consNumber",
    ]
    final_cols = [c for c in preferred_cols if c in df.columns]
    df = df[final_cols]

    out_file = out_dir / f"{index_code}_history.csv"
    df.to_csv(out_file, index=False, encoding="utf-8-sig")
    logger.info(
        "  [history] saved %s (%d rows, %s~%s, pe_coverage=%d/%d)",
        out_file.name,
        len(df),
        df["date"].iloc[0] if len(df) else "?",
        df["date"].iloc[-1] if len(df) else "?",
        df["pe"].notna().sum() if "pe" in df.columns else 0,
        len(df),
    )
    return out_file


# ---------------------------------------------------------------------------
# 5. Main orchestrator
# ---------------------------------------------------------------------------

def download_index(
    *,
    index_codes: Optional[List[str]] = None,
    out_root: Optional[str] = None,
    start_date: str = DEFAULT_START_DATE,
    update_days: int = UPDATE_WINDOW_DAYS,
    sleep_sec: float = SLEEP_SEC,
    skip_intraday: bool = False,
) -> dict:
    """Download iconic CSI index daily history (OHLCV + amount + PE).

    Flow per index:
      1. Download full-range daily history via export Excel, from ``start_date``
         (default 2020-01-01) to today (skip if already cached).
      2. Download 1-month daily history via export Excel (always, for incremental update).
      3. Fetch PE (peg) series for the full range (skip if cached and fresh today after 17:00).
      4. Merge full-range + 1m + PE into ``{indexCode}_history.csv``.
      5. Fetch intraday granular ticks for the latest trading day (skip if unavailable).
    """
    script_dir = Path(__file__).resolve().parent
    out_dir = Path(out_root) if out_root else script_dir / "temps" / "csindex"
    out_dir.mkdir(parents=True, exist_ok=True)

    if index_codes is None:
        index_codes = list(load_classification_index_names().keys())

    # Load index names from sec_classification.json (replaces _classification.py).
    _index_names = load_classification_index_names()

    _start, _end = parse_date_window(start_date=start_date)
    update_end = _end
    update_start = _end - timedelta(days=update_days)

    logger.info(
        "Starting csindex download: codes=%s window=%s->%s (start=%s) "
        "update=%s->%s out=%s",
        index_codes, _start, _end, start_date,
        update_start, update_end, out_dir,
    )

    session = build_session()
    stats = RunStats()
    
    # Create unified AntiBotProxy
    proxy_config = AntiBotConfig(
        base_sleep_sec=sleep_sec,
    )
    proxy = AntiBotProxy(proxy_config)

    try:
        for code in index_codes:
            name = _index_names.get(code, code)

            if code in CSINDEX_SKIP_CODES:
                logger.info("== Index %s (%s) — skipped (in CSINDEX_SKIP_CODES, handled by SZSE downloader) ==", code, name)
                stats.skipped_cached += 1
                continue

            logger.info("== Index %s (%s) ==", code, name)

            if proxy.is_blocked(CSINDEX_BASE):
                logger.warning("  [host-blocked] csindex.com.cn is blocked, skipping all tasks for %s", code)
                stats.failed += 4
                continue

            # --- Step 1: full-range export (skip if cached) ---
            from2020_file = out_dir / f"{code}_from2020.xlsx"
            from2020_csv_file = from2020_file.with_suffix(".csv")
            from2020_downloaded = False
            if is_valid_file(from2020_file, min_bytes=MIN_VALID_BYTES):
                logger.info("  [from2020] %s already cached, skipping download", code)
                stats.skipped_cached += 1
                if is_valid_file(from2020_csv_file, min_bytes=MIN_VALID_BYTES):
                    logger.info("  [from2020] %s already converted, skipping csv conversion", code)
                else:
                    convert_xlsx_to_csv(from2020_file, logger=logger, log_tag=f"[from2020 {code}]")
            else:
                ok = download_export_excel(session, code, _start, _end, from2020_file, proxy)
                from2020_downloaded = ok
                if ok:
                    stats.downloaded += 1
                    stats.files.append(str(from2020_file))
                else:
                    stats.failed += 1
            if from2020_downloaded:
                pass  # Auto-sleep handled by proxy.post() inside download_export_excel

            if proxy.is_blocked(CSINDEX_BASE):
                logger.warning("  [host-blocked] csindex.com.cn blocked after from2020 download, skipping remaining tasks for %s", code)
                stats.failed += 3
                continue

            # --- Step 2: 1-month export (skip if cached and fresh today after 17:00) ---
            onem_file = out_dir / f"{code}_1m.xlsx"
            onem_csv_file = onem_file.with_suffix(".csv")
            onem_downloaded = False
            if is_fresh_today(onem_file, min_bytes=MIN_VALID_BYTES, hour=17):
                logger.info("  [1m] %s already cached and fresh (updated after 17:00), skipping download", code)
                stats.skipped_cached += 1
                if is_valid_file(onem_csv_file, min_bytes=MIN_VALID_BYTES):
                    logger.info("  [1m] %s already converted, skipping csv conversion", code)
                else:
                    convert_xlsx_to_csv(onem_file, logger=logger, log_tag=f"[1m {code}]")
            else:
                ok = download_export_excel(session, code, update_start, update_end, onem_file, proxy)
                onem_downloaded = ok
                if ok:
                    stats.downloaded += 1
                    stats.files.append(str(onem_file))
                else:
                    stats.failed += 1
            if onem_downloaded:
                pass  # Auto-sleep handled by proxy.post() inside download_export_excel

            if proxy.is_blocked(CSINDEX_BASE):
                logger.warning("  [host-blocked] csindex.com.cn blocked after 1m download, skipping remaining tasks for %s", code)
                stats.failed += 2
                continue

            # --- Step 3: PE series (skip if cached and fresh today after 17:00) ---
            pe_cache_file = out_dir / f"{code}_pe.json"
            pe_records: List[Dict[str, Any]] = []
            if is_fresh_today(pe_cache_file, min_bytes=MIN_VALID_BYTES, hour=17):
                cached = load_pe_cache(pe_cache_file)
                if cached is not None:
                    pe_records = cached
                    logger.info("  [pe] %s: cached and fresh (%d records), skipping fetch", code, len(pe_records))
                    stats.skipped_cached += 1
                else:
                    logger.info("  [pe] %s: cache invalid, refetching", code)
            if not pe_records:
                pe_records = fetch_pe_series(session, code, _start, _end, proxy)
                if pe_records:
                    logger.info("  [pe] %s: %d records", code, len(pe_records))
                    if save_pe_cache(pe_cache_file, pe_records):
                        logger.info("  [pe] %s: cached to %s", code, pe_cache_file.name)
                    stats.downloaded += 1
                else:
                    logger.warning("  [pe] %s: no PE data returned", code)
                    stats.failed += 1
            # Auto-sleep handled by proxy.get() inside fetch_pe_series (only when fetched)

            # --- Step 4: Merge into history CSV ---
            history_file = build_history_csv(code, name, out_dir, pe_records)
            if history_file:
                stats.files.append(str(history_file))

            # --- Step 5: Intraday granular ticks (skip if unavailable) ---
            if not skip_intraday:
                intraday_data = fetch_intraday(session, code, proxy)
                if intraday_data is not None:
                    saved = save_intraday(intraday_data, code, name, out_dir)
                    if saved:
                        stats.files.append(str(saved))
                else:
                    logger.info("  [intraday] %s: 1-day data not available, skipping", code)
            # Auto-sleep handled by proxy.get() inside fetch_intraday (if called)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        out_dir=str(out_dir),
        index_codes=index_codes,
        start_date=str(_start),
        end_date=str(_end),
        update_days=update_days,
    )
    logger.info(
        "Done csindex download. downloaded=%d skipped(cached)=%d failed=%d out=%s",
        stats.downloaded, stats.skipped_cached, stats.failed, out_dir,
    )
    return summary


if __name__ == "__main__":
    print(download_index())
