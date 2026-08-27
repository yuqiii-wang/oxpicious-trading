"""Download Shanghai Stock Exchange (SSE) ETF creation/redemption composition (PCF).

Studies ``https://www.sse.com.cn/assortment/fund/list/etfinfo/basic/index.shtml?FUNDID=510010``
whose 公告申购赎回清单 tab exposes a download link to:

    https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do?fundCode={code}

The download returns an XML document (``SSEPortfolioCompositionFile``) with:
  * Header fields — FundInstrumentID, TradingDay (YYYYMMDD), NAV (基金份额净值),
    NAVperCU (最小申购赎回单位净值), PreCashComponent, EstimatedCashComponent,
    MaxCashRatio, CreationRedemptionUnit, RecordNumber, ...
  * ``ComponentList/Component`` entries — InstrumentID, InstrumentName, Quantity,
    SubstitutionFlag (1=允许 / 2=必须 / 3=禁止), CreationPremiumRate,
    RedemptionDiscountRate, UnderlyingSecurityID (101=上海 / 102=深圳)

Only the CURRENT trading day's PCF is available (no historical date parameter),
so this script fetches the latest snapshot for every SSE-listed ETF, mirroring
the SZSE counterpart (``download_szse_etf_composition.py``).

Download behavior:
  * Always downloads today's composition from SSE (no historical dates available).
  * Skip logic: if an ETF already has a cached snapshot from the current month,
    it will be skipped (use --no-skip-cached to force re-download).

Month-start refresh:
  On the 1st day of each month the cache is bypassed and every ETF is
  re-downloaded. The XML/CSV is stamped with TODAY's date (not the xls's
  TradingDay, which typically reflects the previous business day — e.g.
  running on 2026-08-01 yields a file dated 20260801 even though the XML
  reports 2026-07-31). This ensures a fresh monthly snapshot flows through
  to prod under the new month's date. Use --force-month-start to trigger
  this behavior on any day for testing.

Anti-bot: ``safe_get(anti_bot=True)`` rotates the browser fingerprint
(User-Agent / Sec-Ch-Ua / Sec-Fetch-* via ``merge_browser_profile``) on every
request, and ``random_sleep`` / ``random_sleep_range`` add jittered delays.

Output (COMBINED_COLS schema matches the SZSE per-file CSVs so
``build_szse_sse_etf_and_margin.py`` can consume both with a small glob change):
  ``temps/sse_etf_composition/sse_etf_comp_{YYYYMMDD}_{code}.xml``  (raw XML archive)
  ``temps/sse_etf_composition/sse_etf_comp_{YYYYMMDD}_{code}.csv``  (per-file finished CSV)
  ``temp_data/analysis_output/sse_etf_composition/composition_combined.csv``
  ``temp_data/analysis_output/sse_etf_composition/composition_universe.csv``

Usage:
  python download_sse_etf_composition.py
  python download_sse_etf_composition.py --etf-codes 510010,510050 --no-convert-csv
  python download_sse_etf_composition.py --skip-cached --sleep-sec 1.2
"""
from __future__ import annotations


import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

from downloads._common import (
    DEFAULT_SLEEP_SEC,
    DEFAULT_TIMEOUT,
    AntiBotProxy,
    AntiBotConfig,
    RunStats,
    add_exchange_suffix,
    build_default_session,
    build_headers_with_referer,
    is_valid_file,
    resolve_out_dir,
    setup_logger,
)
from downloads._common.monthly import is_month_start


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SSE_SUGGEST_DATA_URL = "https://www.sse.com.cn/js/common/ssesuggestfunddata.js"
SSE_REFERER = "https://www.sse.com.cn/assortment/fund/list/"

# PCF download endpoint — returns the current trading day's XML snapshot.
PCF_DOWNLOAD_URL = "https://query.sse.com.cn/etfDownload/downloadETF2Bulletin.do"

# ETF code prefixes (NO overlap with SZSE ETF prefixes 15/16).
SSE_ETF_PREFIXES: Tuple[str, ...] = ("510", "511", "512", "513", "515", "516", "518", "56")

XML_MIN_BYTES = 200
# Use the centralized anti-bot sleep interval from _download_commons (~20s per
# request). Override per-run with --sleep-sec for testing.
SLEEP_SEC = DEFAULT_SLEEP_SEC
CSV_ENCODING = "utf-8-sig"

# SubstitutionFlag (现金替代标志) numeric → SZSE-compatible Chinese label.
SUBSTITUTION_FLAG_MAP: Dict[str, str] = {
    "1": "允许",
    "2": "必须",
    "3": "禁止",
}

# UnderlyingSecurityID → (market label, exchange suffix).
UNDERLYING_MARKET_MAP: Dict[str, Tuple[str, str]] = {
    "101": ("上海证券交易所", "SS"),
    "102": ("深圳证券交易所", "SZ"),
}

# Output schema — identical to download_szse_etf_composition.COMBINED_COLS so the
# build script can read SSE per-file CSVs alongside the SZSE ones.
COMBINED_COLS: List[str] = [
    "trade_date", "etf_code", "etf_name", "fund_type", "target_index",
    "nav_per_unit", "min_unit_nav",
    "stock_code", "stock_name", "shares", "cash_sub_flag", "market",
]

# ssesuggestfunddata.js entries look like:
#   _t.push({val:"510050",val2:"上证50ETF华夏",val3:"sz50ETF hx"});
RE_FUND_SUGGEST = re.compile(
    r'_t\.push\(\{val:"(\d{6})",val2:"([^"]*)",val3:"([^"]*)"\}\)'
)
RE_TRADE_DATE = re.compile(r"sse_etf_comp_(\d{8})_\d{6}\.csv$")

logger = setup_logger("sse_etf_composition")

SSE_HEADERS = build_headers_with_referer(SSE_REFERER, extra={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})


# ---------------------------------------------------------------------------
# ETF list enumeration (scrape the server-rendered fund list page)
# ---------------------------------------------------------------------------

def _is_sse_etf_code(code: str) -> bool:
    s = str(code).strip()
    return len(s) == 6 and s.isdigit() and any(s.startswith(p) for p in SSE_ETF_PREFIXES)


def fetch_sse_etf_list(
    session: requests.Session,
    proxy: Optional[AntiBotProxy] = None,
) -> List[Dict[str, str]]:
    """Fetch the SSE fund suggest-data JS and extract SSE ETF codes + names.

    The fund list page (``/assortment/fund/list/``) is JS-rendered, but the
    search-autocomplete data at ``/js/common/ssesuggestfunddata.js`` is a
    static JS file listing every SSE-listed fund as::

        _t.push({val:"510050",val2:"上证50ETF华夏",val3:"sz50ETF hx"});

    Entries are filtered to SSE ETF code prefixes (510/511/512/513/515/516/518/56),
    excluding LOF/closed-end funds (501xxx etc.). Returns a list of
    ``{code, etf_name, fund_type}`` dicts (fund_type is "" — not in the source).
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))
    
    resp = proxy.get(
        session,
        SSE_SUGGEST_DATA_URL,
        headers=SSE_HEADERS,
        timeout=DEFAULT_TIMEOUT,
        logger=logger,
        log_tag="[etf-list]",
    )
    if resp is None:
        logger.error("[etf-list] failed to fetch SSE suggest fund data JS")
        return []

    etfs: List[Dict[str, str]] = []
    seen = set()
    for m in RE_FUND_SUGGEST.finditer(resp.text):
        code, name = m.group(1), m.group(2).strip()
        if code in seen or not _is_sse_etf_code(code):
            continue
        seen.add(code)
        etfs.append({"code": code, "etf_name": name, "fund_type": ""})

    logger.info("[etf-list] parsed %d SSE ETF codes from suggest fund data JS", len(etfs))
    return etfs


def _load_etf_codes_from_universe() -> List[Dict[str, str]]:
    """Fallback: read SSE ETF codes from the build script's etf_universe.csv."""
    project_root = Path(__file__).resolve().parent
    universe_csv = project_root / "temp_data" / "analysis_output" / "szse_sse_etf_margin" / "etf_universe.csv"
    if not universe_csv.exists():
        return []
    try:
        df = pd.read_csv(universe_csv, dtype=str, encoding=CSV_ENCODING, keep_default_na=False)
    except Exception as e:
        logger.warning("[etf-list] failed to read %s: %s", universe_csv, e)
        return []
    if "code" not in df.columns or "exchange" not in df.columns:
        return []
    sse_df = df[df["exchange"].astype(str).str.upper() == "SS"]
    out = []
    for _, r in sse_df.iterrows():
        code = str(r.get("code", "")).strip()
        if "." in code:
            code = code.split(".")[0]
        if _is_sse_etf_code(code):
            out.append({
                "code": code,
                "etf_name": str(r.get("name", "") or ""),
                "fund_type": "",
            })
    logger.info("[etf-list] loaded %d SSE ETF codes from %s", len(out), universe_csv.name)
    return out


# ---------------------------------------------------------------------------
# PCF XML download + parse
# ---------------------------------------------------------------------------

def _resolve_market(underlying_id: str, stock_code: str) -> Tuple[str, str]:
    """Return (market_label, exchange_suffix) for a component.

    Uses UnderlyingSecurityID when known; falls back to the stock code prefix
    for 6-digit A-share codes. Cross-border (non-6-digit) codes get no suffix.
    """
    if underlying_id in UNDERLYING_MARKET_MAP:
        return UNDERLYING_MARKET_MAP[underlying_id]
    code = str(stock_code).strip()
    if len(code) == 6 and code.isdigit():
        prefix = code[:3]
        if prefix in ("600", "601", "603", "605", "688"):
            return "上海证券交易所", "SS"
        if prefix in ("000", "001", "002", "003", "300", "301"):
            # ETF components with 000/001 prefixes are Shenzhen stocks.
            return "深圳证券交易所", "SZ"
    return "", ""


def parse_pcf_xml(xml_text: str) -> Optional[Dict[str, Any]]:
    """Parse an SSE PCF XML string into header fields + holdings rows.

    Returns None if the XML is malformed or contains no components.
    """
    text = xml_text.strip()
    if not text.startswith("<?xml") and "<SSEPortfolioCompositionFile" not in text:
        return None

    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        logger.warning("[parse] XML parse error: %s", e)
        return None

    header: Dict[str, str] = {}
    for child in root:
        if child.tag == "ComponentList":
            continue
        header[child.tag] = (child.text or "").strip()

    components: List[Dict[str, str]] = []
    comp_list = root.find("ComponentList")
    if comp_list is not None:
        for comp in comp_list.findall("Component"):
            row: Dict[str, str] = {}
            for child in comp:
                row[child.tag] = (child.text or "").strip()
            components.append(row)

    if not components:
        return None

    return {"header": header, "components": components}


def _pcf_to_combined_rows(
    parsed: Dict[str, Any],
    etf_code: str,
    etf_name: str,
    fund_type: str,
) -> List[Dict[str, Any]]:
    """Convert a parsed PCF dict into COMBINED_COLS rows for the per-file CSV."""
    header = parsed["header"]
    trading_day = header.get("TradingDay", "")
    try:
        trade_date = datetime.strptime(trading_day, "%Y%m%d").strftime("%Y-%m-%d") if trading_day else ""
    except ValueError:
        trade_date = ""

    try:
        nav_per_unit = float(header.get("NAV", "")) if header.get("NAV", "") not in ("", "-") else None
    except ValueError:
        nav_per_unit = None
    try:
        min_unit_nav = float(header.get("NAVperCU", "")) if header.get("NAVperCU", "") not in ("", "-") else None
    except ValueError:
        min_unit_nav = None

    rows: List[Dict[str, Any]] = []
    for comp in parsed["components"]:
        stock_code_raw = comp.get("InstrumentID", "").strip()
        stock_name = comp.get("InstrumentName", "").strip()
        underlying_id = comp.get("UnderlyingSecurityID", "").strip()
        market_label, suffix = _resolve_market(underlying_id, stock_code_raw)

        if suffix and "." not in stock_code_raw:
            stock_code = f"{stock_code_raw}.{suffix}"
        else:
            stock_code = stock_code_raw

        shares_tok = comp.get("Quantity", "").strip()
        try:
            shares = int(shares_tok) if shares_tok not in ("", "-") else None
        except ValueError:
            shares = None

        flag_num = comp.get("SubstitutionFlag", "").strip()
        cash_sub_flag = SUBSTITUTION_FLAG_MAP.get(flag_num, flag_num)

        rows.append({
            "trade_date": trade_date,
            # Canonical suffixed code ("NNNNNN.SS") so builds read the whole
            # code without any suffix surgery.
            "etf_code": add_exchange_suffix(etf_code, "上海"),
            "etf_name": etf_name,
            "fund_type": fund_type,
            "target_index": "",  # not published in the SSE PCF XML
            "nav_per_unit": nav_per_unit,
            "min_unit_nav": min_unit_nav,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "shares": shares,
            "cash_sub_flag": cash_sub_flag,
            "market": market_label,
        })
    return rows


def _write_per_file_csv(rows: List[Dict[str, Any]], csv_path: Path) -> None:
    df = pd.DataFrame(rows, columns=COMBINED_COLS)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False, encoding=CSV_ENCODING)


def convert_xml_to_csv(xml_path: Path, csv_path: Optional[Path] = None,
                       etf_name: str = "", fund_type: str = "") -> bool:
    """Parse a single composition .xml archive and write the per-file CSV.

    Returns True on success, False if parsing yielded no holdings.
    """
    if csv_path is None:
        csv_path = xml_path.with_suffix(".csv")
    try:
        xml_text = xml_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[conv %s] read error: %s", xml_path.name, e)
        return False

    parsed = parse_pcf_xml(xml_text)
    if parsed is None:
        return False

    m = re.search(r"sse_etf_comp_\d{8}_(\d{6})\.xml$", xml_path.name)
    etf_code = m.group(1) if m else parsed["header"].get("FundInstrumentID", "")
    rows = _pcf_to_combined_rows(parsed, etf_code, etf_name, fund_type)
    if not rows:
        return False
    _write_per_file_csv(rows, csv_path)
    return True


def _latest_cached_date_for_code(out_dir: Path, etf_code: str) -> Optional[date]:
    """Return the most recent TradingDay cached for *etf_code*, or None."""
    latest: Optional[date] = None
    for p in out_dir.glob(f"sse_etf_comp_*_{etf_code}.csv"):
        m = RE_TRADE_DATE.search(p.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if latest is None or d > latest:
            latest = d
    return latest


def download_composition(
    session: requests.Session,
    etf: Dict[str, str],
    out_dir: Path,
    proxy: AntiBotProxy,
    skip_cached: bool = True,
    sleep_sec: float = SLEEP_SEC,
    month_start: bool = False,
    today_date: Optional[date] = None,
) -> Tuple[bool, str]:
    """Download one ETF's PCF XML, save raw + per-file CSV.

    Returns (success, status) where status ∈ {"downloaded", "cached", "empty",
    "failed", "blocked"}.

    When *month_start* is True, the cache is bypassed and the output files are
    stamped with *today_date* (overriding the XML's TradingDay) so a fresh
    monthly snapshot flows to prod under the new month's date.
    """
    code = etf["code"]
    etf_name = etf.get("etf_name", "")
    fund_type = etf.get("fund_type", "")

    # Cache check (skipped during month-start refresh): skip if we already have
    # a snapshot from the current month. This mirrors the SZSE hybrid schedule —
    # one snapshot per ETF per month, with quarterly months (Jan/Apr/Jul/Oct)
    # accumulating as history.
    if skip_cached and not month_start:
        latest_cached = _latest_cached_date_for_code(out_dir, code)
        today = date.today()
        if (latest_cached is not None
                and latest_cached.year == today.year
                and latest_cached.month == today.month):
            # Backfill per-file CSV from XML if missing (legacy cache).
            xml_path = out_dir / f"sse_etf_comp_{latest_cached.strftime('%Y%m%d')}_{code}.xml"
            csv_path = out_dir / f"sse_etf_comp_{latest_cached.strftime('%Y%m%d')}_{code}.csv"
            if not is_valid_file(csv_path, min_bytes=64) and is_valid_file(xml_path, min_bytes=XML_MIN_BYTES):
                convert_xml_to_csv(xml_path, csv_path, etf_name, fund_type)
            return True, "cached"

    if proxy.is_blocked(PCF_DOWNLOAD_URL):
        return False, "blocked"

    resp = None
    for attempt in range(1, 5):
        resp = proxy.get(
            session,
            PCF_DOWNLOAD_URL,
            params={"fundCode": code},
            headers=SSE_HEADERS,
            timeout=DEFAULT_TIMEOUT,
            logger=logger,
            log_tag=f"[dl {code}]",
        )
        if resp is None:
            if attempt == 4:
                logger.error("[dl %s] fetch error after %d attempts", code, attempt)
                return False, "failed"
            backoff = 2.0 * attempt
            logger.warning("[dl %s] attempt %d failed; retry in %.1fs", code, attempt, backoff)
            proxy.sleep_range(backoff, backoff * 1.5)
            continue
        break

    content = resp.text.strip()
    if not content.startswith("<?xml") and "<SSEPortfolioCompositionFile" not in content:
        logger.warning("[dl %s] non-XML response (len=%d), skipping", code, len(content))
        return False, "empty"

    parsed = parse_pcf_xml(content)
    if parsed is None:
        logger.warning("[dl %s] XML parsed but no components, skipping", code)
        return False, "empty"

    trading_day = parsed["header"].get("TradingDay", "")
    if not (trading_day and trading_day.isdigit() and len(trading_day) == 8):
        logger.warning("[dl %s] invalid TradingDay '%s', skipping", code, trading_day)
        return False, "empty"

    # On month-start refresh, stamp the output with today's date (overriding
    # the XML's TradingDay, which is usually the previous business day) so a
    # fresh monthly snapshot flows to prod under the new month's date.
    if month_start and today_date is not None:
        trading_day = today_date.strftime("%Y%m%d")
        parsed["header"]["TradingDay"] = trading_day  # _pcf_to_combined_rows reads this

    base = f"sse_etf_comp_{trading_day}_{code}"
    xml_path = out_dir / f"{base}.xml"
    csv_path = out_dir / f"{base}.csv"

    out_dir.mkdir(parents=True, exist_ok=True)
    xml_path.write_bytes(resp.content)

    rows = _pcf_to_combined_rows(parsed, code, etf_name, fund_type)
    if not rows:
        logger.warning("[dl %s] no holdings rows built, skipping CSV", code)
        return False, "empty"

    _write_per_file_csv(rows, csv_path)
    logger.info("[dl %s] saved %s + %s (%d holdings, T=%s)",
                code, xml_path.name, csv_path.name, len(rows), trading_day)
    # Auto-sleep handled by proxy.get()/post()
    return True, "downloaded"


# ---------------------------------------------------------------------------
# Aggregation: combine all per-file CSVs into combined + universe CSVs
# ---------------------------------------------------------------------------

def build_composition_csv(md_dir: Path, output_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Aggregate all ``sse_etf_comp_*.csv`` per-file CSVs into combined + universe.

    Mirrors ``download_szse_etf_composition.build_composition_csv``.
    """
    if output_dir is None:
        project_root = Path(__file__).resolve().parent
        output_dir = project_root / "temp_data" / "analysis_output" / "sse_etf_composition"
    output_dir.mkdir(parents=True, exist_ok=True)
    per_etf_dir = output_dir / "per_etf"
    per_etf_dir.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(md_dir.glob("sse_etf_comp_*.csv"))
    logger.info("[build] scanning %s: %d per-file CSVs", md_dir, len(csv_files))

    counts: Dict[str, int] = {"parsed": 0, "failed": 0, "holdings": 0}
    long_rows: List[Dict[str, Any]] = []

    for csv_path in csv_files:
        try:
            df = pd.read_csv(csv_path, dtype=str, encoding=CSV_ENCODING, keep_default_na=False)
        except Exception as e:
            counts["failed"] += 1
            logger.warning("[build] failed to read %s: %s", csv_path.name, e)
            continue
        if df is None or len(df) == 0:
            counts["failed"] += 1
            continue
        counts["parsed"] += 1
        counts["holdings"] += len(df)
        long_rows.extend(df.to_dict("records"))

    if not long_rows:
        logger.warning("[build] no holdings parsed from any per-file CSV")
        return {"output_dir": str(output_dir), "parsed": 0, "failed": counts["failed"]}

    combined = pd.DataFrame(long_rows, columns=COMBINED_COLS)
    combined["trade_date"] = pd.to_datetime(combined["trade_date"], errors="coerce")
    for c in ("nav_per_unit", "min_unit_nav", "shares"):
        combined[c] = pd.to_numeric(combined[c], errors="coerce")
    combined = combined.sort_values(["etf_code", "trade_date", "stock_code"]).reset_index(drop=True)

    combined_path = output_dir / "composition_combined.csv"
    combined.to_csv(combined_path, index=False, encoding=CSV_ENCODING)
    logger.info("[build] saved composition_combined.csv (%d rows, %d ETFs, %d dates)",
                len(combined), combined["etf_code"].nunique(),
                combined["trade_date"].dt.strftime("%Y-%m-%d").nunique())

    n_written = 0
    for code, sub in combined.groupby("etf_code"):
        out = per_etf_dir / f"{code}.csv"
        sub.sort_values(["trade_date", "stock_code"]).to_csv(out, index=False, encoding=CSV_ENCODING)
        n_written += 1
    logger.info("[build] saved %d per-ETF files in %s", n_written, per_etf_dir)

    universe_rows = []
    for code, sub in combined.groupby("etf_code"):
        sub_sorted = sub.sort_values("trade_date")
        latest_date = sub_sorted["trade_date"].iloc[-1]
        latest = sub_sorted[sub_sorted["trade_date"] == latest_date]
        name = str(sub_sorted["etf_name"].dropna().iloc[0]) if len(sub_sorted) else ""
        ftype = str(sub_sorted["fund_type"].dropna().iloc[0]) if len(sub_sorted) else ""
        universe_rows.append({
            "etf_code": code,
            "etf_name": name,
            "fund_type": ftype,
            "target_index": "",
            "n_dates": int(sub_sorted["trade_date"].dt.strftime("%Y-%m-%d").nunique()),
            "latest_date": latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else "",
            "n_holdings_latest": int(len(latest)),
            "n_equity_latest": int((latest["cash_sub_flag"] != "必须").sum()),
        })
    universe = pd.DataFrame(universe_rows).sort_values("etf_code").reset_index(drop=True)
    universe_path = output_dir / "composition_universe.csv"
    universe.to_csv(universe_path, index=False, encoding=CSV_ENCODING)
    logger.info("[build] saved composition_universe.csv (%d ETFs)", len(universe))

    logger.info("[build] done: parsed=%d failed=%d total_holdings=%d",
                counts["parsed"], counts["failed"], counts["holdings"])
    return {
        "output_dir": str(output_dir),
        "parsed": counts["parsed"],
        "failed": counts["failed"],
        "total_holdings": counts["holdings"],
        "etfs": combined["etf_code"].nunique(),
        "dates": combined["trade_date"].dt.strftime("%Y-%m-%d").nunique(),
    }


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def download_sse_etf_composition(
    *,
    out_root: Optional[str] = None,
    etf_codes: Optional[List[str]] = None,
    sleep_sec: float = SLEEP_SEC,
    max_etfs: Optional[int] = None,
    skip_cached: bool = True,
    convert_csv: bool = True,
    force_month_start: bool = False,
) -> dict:
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "sse_etf_composition", out_root)
    today = date.today()

    # Month-start trigger: on the 1st of each month (or when forced), bypass
    # the cache and stamp output files with today's date so a fresh monthly
    # snapshot flows to prod even if the XML still reports the previous biz day.
    month_start = force_month_start or is_month_start(today)
    if month_start:
        skip_cached = False
        logger.info(
            "Month-start refresh (today=%s, forced=%s): bypassing cache and "
            "stamping files with today's date (overrides XML TradingDay).",
            today.isoformat(), force_month_start,
        )

    session = build_default_session()
    
    # Create unified AntiBotProxy with custom config
    proxy_config = AntiBotConfig(
        base_sleep_sec=sleep_sec,
        sleep_jitter=0.3,
    )
    proxy = AntiBotProxy(proxy_config)
    
    stats = RunStats()

    # --- Enumerate SSE ETFs ---
    etfs: List[Dict[str, str]] = []
    if etf_codes:
        for c in etf_codes:
            c = str(c).strip()
            if _is_sse_etf_code(c):
                etfs.append({"code": c, "etf_name": "", "fund_type": ""})
        logger.info("[main] using %d ETF codes from CLI override", len(etfs))
    else:
        etfs = fetch_sse_etf_list(session, proxy)
        if not etfs:
            logger.warning("[main] fund list page yielded no ETFs; trying etf_universe.csv fallback")
            etfs = _load_etf_codes_from_universe()
        if not etfs:
            logger.error("[main] no SSE ETF codes available; pass --etf-codes to specify manually")
            return {"out_dir": str(out_dir), "etfs": 0, "downloaded": 0}

    if max_etfs is not None and max_etfs > 0:
        etfs = etfs[:max_etfs]

    # --- Auto-cooldown: count how many ETFs actually need downloading ---
    if month_start:
        need_download = len(etfs)
    elif skip_cached:
        need_download = 0
        for etf in etfs:
            latest = _latest_cached_date_for_code(out_dir, etf["code"])
            if latest is None or latest.year != today.year or latest.month != today.month:
                need_download += 1
    else:
        need_download = len(etfs)

    auto_sleep = sleep_sec
    if need_download > 50:
        auto_sleep = sleep_sec * 1.5
        logger.info(
            "Auto-cooldown: %d ETFs to download, increasing per-item sleep from %.1fs to %.1fs",
            need_download, sleep_sec, auto_sleep,
        )

    logger.info(
        "Starting SSE ETF composition download: %d ETFs (skip_cached=%s, sleep=%.1fs, "
        "need_download=%d)",
        len(etfs), skip_cached, auto_sleep, need_download,
    )

    # --- Download each ETF's PCF ---
    downloaded = 0
    cached = 0
    empty = 0
    failed = 0
    processed = 0

    try:
        try:
            from tqdm import tqdm
            etfs_iter = tqdm(etfs, desc="SSE ETFs", unit="etf", leave=True)
        except ImportError:
            etfs_iter = etfs

        for etf in etfs_iter:
            if proxy.is_blocked(PCF_DOWNLOAD_URL):
                logger.warning("[host-blocked] query.sse.com.cn blocked, stopping")
                break

            _, status = download_composition(
                session, etf, out_dir, proxy,
                skip_cached=skip_cached, sleep_sec=auto_sleep,
                month_start=month_start, today_date=today,
            )
            if status == "downloaded":
                downloaded += 1
                stats.downloaded += 1
            elif status == "cached":
                cached += 1
                stats.skipped_cached += 1
            elif status == "empty":
                empty += 1
                stats.empty += 1
            else:
                failed += 1
                stats.failed += 1
            processed += 1
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        out_dir=str(out_dir),
        etfs_total=len(etfs),
        etfs_processed=processed,
        cached=cached,
        empty=empty,
    )

    # --- Aggregate per-file CSVs ---
    if convert_csv:
        csv_result = build_composition_csv(out_dir)
        summary["csv"] = csv_result

    logger.info(
        "Done SSE ETF composition. downloaded=%d cached=%d empty=%d failed=%d out=%s (%d/%d processed)",
        downloaded, cached, empty, failed, out_dir, processed, len(etfs),
    )
    return summary


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Download SSE ETF creation/redemption composition (PCF) data and convert to CSV.",
    )
    ap.add_argument("--etf-codes", type=str, default=None,
                    help="Comma-separated SSE ETF codes to download (default: auto-detect all)")
    ap.add_argument("--out-root", type=str, default=None,
                    help="Alternative output root directory")
    ap.add_argument("--sleep-sec", type=float, default=SLEEP_SEC,
                    help=f"Sleep seconds between requests (default: {SLEEP_SEC})")
    ap.add_argument("--max-etfs", type=int, default=None,
                    help="Limit to N ETFs (dev/testing)")
    ap.add_argument("--skip-cached", action="store_true", default=True,
                    help="Skip ETFs whose latest snapshot is already cached (default: enabled)")
    ap.add_argument("--no-skip-cached", action="store_true", default=False,
                    help="Re-download even if a cached snapshot exists")
    ap.add_argument("--force-month-start", action="store_true", default=False,
                    help="Force month-start behavior: bypass cache and stamp files with "
                         "today's date (overrides XML TradingDay). For testing the "
                         "monthly refresh flow on any day.")
    ap.add_argument("--convert-csv", action="store_true", default=True,
                    help="Aggregate per-file CSVs into combined + universe CSVs (default: enabled)")
    ap.add_argument("--no-convert-csv", action="store_true", default=False,
                    help="Skip CSV aggregation")
    args = ap.parse_args()

    etf_codes_arg = None
    if args.etf_codes:
        etf_codes_arg = [c.strip() for c in args.etf_codes.split(",") if c.strip()]

    result = download_sse_etf_composition(
        out_root=args.out_root,
        etf_codes=etf_codes_arg,
        sleep_sec=args.sleep_sec,
        max_etfs=args.max_etfs,
        skip_cached=args.skip_cached and not args.no_skip_cached,
        convert_csv=args.convert_csv and not args.no_convert_csv,
        force_month_start=args.force_month_start,
    )
    print(result)
