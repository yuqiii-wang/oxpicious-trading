from __future__ import annotations


import logging
import random
import re
import sys
import time

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

import numpy as np
import pandas as pd

from downloads._common import (
    DEFAULT_TIMEOUT,
    DEFAULT_START_DATE,
    DEFAULT_SHORT_SLEEP_SEC,
    AntiBotProxy,
    AntiBotConfig,
    setup_logger,
    resolve_out_dir,
    is_valid_file,
    scan_present_filenames,
    build_default_session,
    RunStats,
    is_trading_day,
    add_exchange_suffix,
    random_browser_profile,
)
from downloads._common.monthly import is_month_start, most_recent_trading_day


LIST_API_URL = "https://www.szse.cn/api/report/ShowReport/data"
DETAIL_BASE_URL = "https://reportdocs.static.szse.cn"
REFERER = "https://www.szse.cn/disclosure/fund/currency/index.html"
CATALOGID = "sgshqd"
PAGE_SIZE = 20
MD_MIN_BYTES = 200
SLEEP_SEC = DEFAULT_SHORT_SLEEP_SEC

RE_ENCODE_OPEN = re.compile(r"encode-open=['\"]([^'\"]+)['\"]")
RE_ETF_CODE_FROM_PATH = re.compile(r"/files/text/etf/ETF(\d{6})(\d{8})\.txt")
RE_ETF_TITLE = re.compile(r">([^<]+申购赎回清单\(\d{4}-\d{2}-\d{2}\))<")

RE_FUND_NAME = re.compile(r"基金名称[：:]\s*(\S+)")
RE_FUND_CODE = re.compile(r"基金代码[：:]\s*(\S+)")
RE_FUND_TYPE = re.compile(r"基金类型[：:]\s*(\S.*)")
RE_FUND_COMPANY = re.compile(r"基金管理公司名称[：:]\s*(\S.*)")

_RE_6DIGIT = re.compile(r"^\d{6}$")
_RE_INT = re.compile(r"^\d+$")
_CASH_FLAGS = ("允许", "必须", "禁止")

_RE_NAV = re.compile(r"基金份额净值[：:][ \t]*([\d.]+)[ \t]*元")
_RE_MIN_UNIT_NAV = re.compile(r"最小申购[、,]赎回单位资产净值[：:][ \t]*([\d.]+)[ \t]*元")
_RE_TARGET_IDX = re.compile(r"目标指数代码[：:][ \t]*(\S*)")

COMBINED_COLS = [
    "trade_date", "etf_code", "etf_name", "fund_type", "target_index",
    "nav_per_unit", "min_unit_nav",
    "stock_code", "stock_name", "shares", "cash_sub_flag", "market",
]


logger = setup_logger("szse_etf_composition")


@dataclass
class EtfCompositionItem:
    trade_date: date
    etf_code: str
    title: str
    detail_path: str
    detail_url: str
    md_filename: str


def first_business_day_of_month(year: int, month: int) -> date:
    d = date(year, month, 1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def generate_monthly_first_biz_dates(
    start_date: date,
    end_date: date,
) -> List[date]:
    dates: List[date] = []
    cur_year = end_date.year
    cur_month = end_date.month
    while True:
        d = first_business_day_of_month(cur_year, cur_month)
        if d < start_date:
            break
        if d <= end_date:
            dates.append(d)
        if cur_month == 1:
            cur_year -= 1
            cur_month = 12
        else:
            cur_month -= 1
        if cur_year < start_date.year:
            break
        if cur_year == start_date.year and cur_month < start_date.month:
            break
    return dates


def generate_hybrid_dates(
    start_date: date,
    end_date: date,
    quarterly_months: Tuple[int, ...] = (1, 4, 7, 10),
) -> List[date]:
    dates: List[date] = []

    cur_year = end_date.year
    cur_month = end_date.month

    last_month_year = cur_year
    last_month_month = cur_month

    while True:
        d = first_business_day_of_month(cur_year, cur_month)
        if d < start_date:
            break
        if d > end_date:
            pass
        else:
            is_last_month = (cur_year == last_month_year and cur_month == last_month_month)

            if is_last_month:
                dates.append(d)
            else:
                if cur_month in quarterly_months:
                    dates.append(d)

        if cur_month == 1:
            cur_year -= 1
            cur_month = 12
        else:
            cur_month -= 1

        if cur_year < start_date.year:
            break
        if cur_year == start_date.year and cur_month < start_date.month:
            break

    return dates


def build_list_headers(base_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = dict(base_headers) if base_headers else {}
    h["Referer"] = REFERER
    h["Accept"] = "application/json, text/javascript, */*; q=0.01"
    return h


def build_detail_headers(base_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = dict(base_headers) if base_headers else {}
    h["Referer"] = REFERER
    h["Accept"] = "text/plain,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    return h


def md_filename_for(trade_date: date, etf_code: str) -> str:
    ymd = trade_date.strftime("%Y%m%d")
    return f"szse_etf_comp_{ymd}_{etf_code}.md"


def parse_list_row(jjdm_html: str, trade_date: date) -> Optional[EtfCompositionItem]:
    m_path = RE_ENCODE_OPEN.search(jjdm_html)
    if not m_path:
        return None
    detail_path = m_path.group(1)

    m_code = RE_ETF_CODE_FROM_PATH.search(detail_path)
    if m_code:
        etf_code = m_code.group(1)
    else:
        m_alt = re.search(r"ETF(\d{6})", jjdm_html)
        etf_code = m_alt.group(1) if m_alt else "UNKNOWN"

    m_title = RE_ETF_TITLE.search(jjdm_html)
    title = m_title.group(1) if m_title else f"ETF{etf_code}申购赎回清单({trade_date.strftime('%Y-%m-%d')})"

    if detail_path.startswith("http"):
        detail_url = detail_path
    else:
        detail_url = DETAIL_BASE_URL + detail_path

    return EtfCompositionItem(
        trade_date=trade_date,
        etf_code=etf_code,
        title=title,
        detail_path=detail_path,
        detail_url=detail_url,
        md_filename=md_filename_for(trade_date, etf_code),
    )


def fetch_list_page(
    session: requests.Session,
    trade_date: date,
    page_no: int,
    base_headers: Dict[str, str],
    proxy: Optional[AntiBotProxy] = None,
) -> Tuple[int, int, List[EtfCompositionItem]]:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))
    
    date_str = trade_date.strftime("%Y-%m-%d")
    params: Dict[str, Any] = {
        "SHOWTYPE": "JSON",
        "CATALOGID": CATALOGID,
        "txtStart": date_str,
        "txtEnd": date_str,
        "PAGENO": page_no,
    }
    if page_no == 1:
        params["loading"] = "first"

    payload = None
    for attempt in range(1, 5):
        resp = proxy.get(
            session,
            LIST_API_URL,
            params=params,
            headers=build_list_headers(base_headers),
            timeout=DEFAULT_TIMEOUT,
            logger=logger,
            log_tag=f"[list {date_str} p{page_no}]",
        )
        if resp is None:
            if attempt == 4:
                logger.error("[list %s p%d] fetch error after %d attempts: request returned None", date_str, page_no, attempt)
                return 0, 0, []
            backoff = 2.0 * attempt
            logger.warning("[list %s p%d] attempt %d failed; retry in %.1fs", date_str, page_no, attempt, backoff)
            proxy.sleep(backoff)
            continue
        try:
            payload = resp.json()
            break
        except ValueError as e:
            if attempt == 4:
                logger.error("[list %s p%d] json parse error after %d attempts: %s", date_str, page_no, attempt, e)
                return 0, 0, []
            backoff = 2.0 * attempt
            logger.warning("[list %s p%d] attempt %d failed (json parse error); retry in %.1fs", date_str, page_no, attempt, backoff)
            time.sleep(backoff)

    if isinstance(payload, list) and payload:
        item0 = payload[0]
    elif isinstance(payload, dict):
        item0 = payload
    else:
        logger.error("[list %s p%d] unexpected payload type %s", date_str, page_no, type(payload).__name__)
        return 0, 0, []

    md = item0.get("metadata") or {}
    pagecount = int(md.get("pagecount") or 0)
    recordcount = int(md.get("recordcount") or 0)
    data_rows = item0.get("data") or []

    items: List[EtfCompositionItem] = []
    for row in data_rows:
        if not isinstance(row, dict):
            continue
        jjdm = row.get("jjdm", "")
        if not jjdm:
            continue
        parsed = parse_list_row(jjdm, trade_date)
        if parsed:
            items.append(parsed)
    return pagecount, recordcount, items


def fetch_all_etfs_for_date(
    session: requests.Session,
    trade_date: date,
    base_headers: Dict[str, str],
    proxy: Optional[AntiBotProxy] = None,
) -> List[EtfCompositionItem]:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))
    
    date_str = trade_date.strftime("%Y-%m-%d")
    pagecount, recordcount, first_items = fetch_list_page(session, trade_date, 1, base_headers, proxy)
    if pagecount <= 0 or recordcount <= 0:
        if first_items:
            logger.info("[list %s] p%d: got %d items (no metadata pages)", date_str, 1, len(first_items))
            return first_items
        logger.info("[list %s] no data (recordcount=0, pagecount=0)", date_str)
        return []

    all_items: List[EtfCompositionItem] = list(first_items)
    logger.info(
        "[list %s] p1/%d: %d items, total_records_expected=%d",
        date_str, pagecount, len(first_items), recordcount,
    )

    seen_codes: Set[str] = {it.etf_code for it in all_items}

    for pno in range(2, pagecount + 1):
        if proxy.is_blocked(LIST_API_URL):
            logger.warning("[list %s] szse.cn blocked, stopping pagination at page %d", date_str, pno)
            break
        _, _, page_items = fetch_list_page(session, trade_date, pno, base_headers, proxy)
        added = 0
        for it in page_items:
            if it.etf_code not in seen_codes:
                seen_codes.add(it.etf_code)
                all_items.append(it)
                added += 1
        logger.info("[list %s] p%d/%d: page_items=%d new=%d cumulative=%d",
                    date_str, pno, pagecount, len(page_items), added, len(all_items))
        proxy.sleep(random.uniform(5.0, 6.0))

    return all_items


def composition_to_markdown(item: EtfCompositionItem, raw_text: str) -> str:
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip()

    fund_name = ""
    fund_code = item.etf_code
    fund_type = ""
    fund_company = ""

    m = RE_FUND_NAME.search(text)
    if m:
        fund_name = m.group(1).strip()
    m = RE_FUND_CODE.search(text)
    if m:
        fund_code = m.group(1).strip()
    m = RE_FUND_TYPE.search(text)
    if m:
        fund_type = m.group(1).strip()
    m = RE_FUND_COMPANY.search(text)
    if m:
        fund_company = m.group(1).strip()

    lines: List[str] = []
    lines.append("---")
    lines.append(f"trade_date: {item.trade_date.strftime('%Y-%m-%d')}")
    lines.append(f"etf_code: {fund_code}")
    lines.append(f"etf_name: {fund_name}")
    lines.append(f"fund_type: {fund_type}")
    lines.append(f"fund_company: {fund_company}")
    lines.append(f"title: {item.title}")
    lines.append(f"detail_url: {item.detail_url}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {item.title}")
    lines.append("")
    lines.append(f"- Trade date: **{item.trade_date.strftime('%Y-%m-%d')}**")
    lines.append(f"- ETF code: **{fund_code}**")
    if fund_name:
        lines.append(f"- Fund name: {fund_name}")
    if fund_company:
        lines.append(f"- Fund company: {fund_company}")
    if fund_type:
        lines.append(f"- Fund type: {fund_type}")
    lines.append(f"- Source: {item.detail_url}")
    lines.append("")
    lines.append("## Raw composition (申购赎回清单)")
    lines.append("")
    lines.append("```text")
    lines.append(text)
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def download_composition(
    session: requests.Session,
    item: EtfCompositionItem,
    out_file: Path,
    base_headers: Dict[str, str],
    proxy: Optional[AntiBotProxy] = None,
) -> bool:
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=5.0))
    
    if is_valid_file(out_file, min_bytes=MD_MIN_BYTES):
        # Cached MD: ensure the per-file CSV exists too (backfill legacy MDs)
        csv_path = out_file.with_suffix(".csv")
        if not csv_path.exists():
            try:
                convert_md_to_csv(out_file, csv_path)
            except Exception as e:
                logger.warning("[conv %s %s] per-file CSV conversion failed: %s", item.etf_code, item.trade_date, e)
        return True

    resp = None
    for attempt in range(1, 5):
        resp = proxy.get(
            session,
            item.detail_url,
            headers=build_detail_headers(base_headers),
            timeout=DEFAULT_TIMEOUT,
            logger=logger,
            log_tag=f"[dl {item.etf_code} {item.trade_date}]",
        )
        if resp is None:
            if attempt == 4:
                logger.error("[dl %s %s] fetch error after %d attempts: request returned None", item.etf_code, item.trade_date, attempt)
                return False
            backoff = 2.0 * attempt
            logger.warning("[dl %s %s] attempt %d failed; retry in %.1fs", item.etf_code, item.trade_date, attempt, backoff)
            time.sleep(backoff)
            continue
        break

    if len(resp.content) < MD_MIN_BYTES:
        logger.warning("[dl %s %s] content too small (%d bytes)", item.etf_code, item.trade_date, len(resp.content))
        return False

    content = resp.text
    md_text = composition_to_markdown(item, content)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(md_text)

    # Write the per-file CSV alongside the MD so build scripts can read finished CSVs directly
    csv_path = out_file.with_suffix(".csv")
    try:
        convert_md_to_csv(out_file, csv_path)
    except Exception as e:
        logger.warning("[conv %s %s] per-file CSV conversion failed: %s", item.etf_code, item.trade_date, e)

    if is_valid_file(out_file, min_bytes=MD_MIN_BYTES):
        return True
    logger.warning("[dl %s %s] saved file too small after write", item.etf_code, item.trade_date)
    return False


def _parse_front_matter(text: str) -> Dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm: Dict[str, str] = {}
    for ln in lines[1:]:
        s = ln.strip()
        if s == "---":
            break
        if ":" in ln:
            k, _, v = ln.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def _extract_text_block(text: str) -> str:
    m = re.search(r"```text\s*\n(.*?)\n```", text, re.DOTALL)
    return m.group(1) if m else ""


def _parse_holding_row(line: str) -> Optional[Dict[str, Any]]:
    parts = re.split(r"\s{2,}", line.strip())
    parts = [p for p in parts if p != ""]
    if len(parts) < 3:
        return None
    code = parts[0]
    if not _RE_6DIGIT.match(code):
        return None
    market = parts[-1]
    if not market.endswith("市场"):
        return None
    name = parts[1]
    middle = parts[2:-1]
    shares_tok = next((t for t in middle if _RE_INT.match(t)), None)
    flag = next((t for t in middle if t in _CASH_FLAGS), None)
    shares = int(shares_tok) if shares_tok is not None else None
    return {
        "stock_code": code,
        "stock_name": name,
        "shares": shares,
        "cash_sub_flag": flag,
        "market": market,
    }


def parse_composition_md(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return None

    fm = _parse_front_matter(text)
    if not fm:
        return None

    block = _extract_text_block(text)
    if not block:
        return None

    nav = None
    m = _RE_NAV.search(block)
    if m:
        try:
            nav = float(m.group(1))
        except ValueError:
            nav = None
    min_nav = None
    m = _RE_MIN_UNIT_NAV.search(block)
    if m:
        try:
            min_nav = float(m.group(1))
        except ValueError:
            min_nav = None
    m = _RE_TARGET_IDX.search(block)
    target_index = m.group(1).strip() if m else ""

    holdings = []
    for ln in block.splitlines():
        row = _parse_holding_row(ln)
        if row is not None:
            holdings.append(row)

    if not holdings:
        return None

    return {
        "trade_date": fm.get("trade_date", ""),
        "etf_code": fm.get("etf_code", ""),
        "etf_name": fm.get("etf_name", ""),
        "fund_type": fm.get("fund_type", ""),
        "fund_company": fm.get("fund_company", ""),
        "target_index": target_index,
        "nav_per_unit": nav,
        "min_unit_nav": min_nav,
        "holdings": holdings,
    }


def convert_md_to_csv(md_path: Path, csv_path: Optional[Path] = None) -> bool:
    """Parse a single composition .md file and write a per-file CSV with COMBINED_COLS schema.

    The CSV is written next to the .md file (same stem, .csv suffix) so downstream
    build scripts can read the finished CSV directly without re-parsing the markdown.
    Returns True on success, False if parsing yielded no holdings.
    """
    if csv_path is None:
        csv_path = md_path.with_suffix(".csv")
    rec = parse_composition_md(md_path)
    if rec is None or not rec.get("holdings"):
        return False
    rows = []
    for h in rec["holdings"]:
        rows.append({
            "trade_date": rec["trade_date"],
            # Canonical suffixed code ("NNNNNN.SZ") so builds read the whole
            # code without any suffix surgery.
            "etf_code": add_exchange_suffix(rec["etf_code"], "深圳"),
            "etf_name": rec["etf_name"],
            "fund_type": rec["fund_type"],
            "target_index": rec["target_index"],
            "nav_per_unit": rec["nav_per_unit"],
            "min_unit_nav": rec["min_unit_nav"],
            "stock_code": add_exchange_suffix(h["stock_code"], h["market"]),
            "stock_name": h["stock_name"],
            "shares": h["shares"],
            "cash_sub_flag": h["cash_sub_flag"],
            "market": h["market"],
        })
    df = pd.DataFrame(rows, columns=COMBINED_COLS)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return True


def build_composition_csv(md_dir: Path, output_dir: Optional[Path] = None) -> Dict[str, Any]:
    if output_dir is None:
        project_root = Path(__file__).resolve().parent
        output_dir = project_root / "temp_data" / "analysis_output" / "szse_etf_composition"
    output_dir.mkdir(parents=True, exist_ok=True)
    per_etf_dir = output_dir / "per_etf"
    per_etf_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(md_dir.glob("szse_etf_comp_*.md"))
    csv_files = sorted(md_dir.glob("szse_etf_comp_*.csv"))
    logger.info("[build] scanning %s: %d .md files, %d per-file CSVs", md_dir, len(files), len(csv_files))

    counts = Counter()
    long_rows = []

    # Prefer per-file CSVs (much faster than re-parsing MDs); fall back to MDs
    if csv_files:
        for csv_path in csv_files:
            try:
                # keep_default_na=False preserves "" for empty cells (matches the
                # MD-parsing behaviour where missing fields were "" not NaN).
                df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
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
    else:
        # Fallback: parse MD files and write per-file CSVs as a side effect
        logger.info("[build] no per-file CSVs found, parsing %d .md files", len(files))
        for path in files:
            rec = parse_composition_md(path)
            if rec is None:
                counts["failed"] += 1
                continue
            counts["parsed"] += 1
            counts["holdings"] += len(rec["holdings"])
            convert_md_to_csv(path)
            for h in rec["holdings"]:
                long_rows.append({
                    "trade_date": rec["trade_date"],
                    "etf_code": add_exchange_suffix(rec["etf_code"], "深圳"),
                    "etf_name": rec["etf_name"],
                    "fund_type": rec["fund_type"],
                    "target_index": rec["target_index"],
                    "nav_per_unit": rec["nav_per_unit"],
                    "min_unit_nav": rec["min_unit_nav"],
                    "stock_code": add_exchange_suffix(h["stock_code"], h["market"]),
                    "stock_name": h["stock_name"],
                    "shares": h["shares"],
                    "cash_sub_flag": h["cash_sub_flag"],
                    "market": h["market"],
                })

    if not long_rows:
        logger.warning("[build] no holdings parsed from any .md/.csv file")
        return {"output_dir": str(output_dir), "parsed": 0, "failed": counts["failed"]}

    combined = pd.DataFrame(long_rows, columns=COMBINED_COLS)
    combined["trade_date"] = pd.to_datetime(combined["trade_date"], errors="coerce")
    combined = combined.sort_values(["etf_code", "trade_date", "stock_code"]).reset_index(drop=True)

    combined_path = output_dir / "composition_combined.csv"
    combined.to_csv(combined_path, index=False, encoding="utf-8-sig")
    logger.info("[build] saved composition_combined.csv (%d rows, %d ETFs, %d dates)",
                len(combined), combined["etf_code"].nunique(),
                combined["trade_date"].dt.strftime("%Y-%m-%d").nunique())

    n_written = 0
    for code, sub in combined.groupby("etf_code"):
        out = per_etf_dir / f"{code}.csv"
        sub.sort_values(["trade_date", "stock_code"]).to_csv(out, index=False, encoding="utf-8-sig")
        n_written += 1
    logger.info("[build] saved %d per-ETF files in %s", n_written, per_etf_dir)

    universe_rows = []
    for code, sub in combined.groupby("etf_code"):
        sub_sorted = sub.sort_values("trade_date")
        latest_date = sub_sorted["trade_date"].iloc[-1]
        latest = sub_sorted[sub_sorted["trade_date"] == latest_date]
        name = str(sub_sorted["etf_name"].dropna().iloc[0]) if len(sub_sorted) else ""
        ftype = str(sub_sorted["fund_type"].dropna().iloc[0]) if len(sub_sorted) else ""
        tidx = str(sub_sorted["target_index"].dropna().iloc[0]) if len(sub_sorted) else ""
        non_empty = sub_sorted.loc[sub_sorted["target_index"].astype(str).str.strip() != "", "target_index"]
        if len(non_empty):
            tidx = str(non_empty.iloc[-1])
        universe_rows.append({
            "etf_code": code,
            "etf_name": name,
            "fund_type": ftype,
            "target_index": tidx,
            "n_dates": int(sub_sorted["trade_date"].dt.strftime("%Y-%m-%d").nunique()),
            "latest_date": latest_date.strftime("%Y-%m-%d") if pd.notna(latest_date) else "",
            "n_holdings_latest": int(len(latest)),
            "n_equity_latest": int((latest["cash_sub_flag"] != "必须").sum()),
        })
    universe = pd.DataFrame(universe_rows).sort_values("etf_code").reset_index(drop=True)
    universe_path = output_dir / "composition_universe.csv"
    universe.to_csv(universe_path, index=False, encoding="utf-8-sig")
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


def download_szse_etf_composition(
    *,
    out_root: Optional[str] = None,
    start_date: Optional[str] = DEFAULT_START_DATE,
    end_date: Optional[str] = None,
    sleep_sec: float = SLEEP_SEC,
    max_dates: Optional[int] = None,
    skip_today: bool = True,
    convert_csv: bool = True,
    download_mode: str = "hybrid",
    force_month_start: bool = False,
) -> dict:
    out_dir = resolve_out_dir(str(Path(__file__).resolve()), "szse_etf_composition", out_root)

    _start = datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else datetime.strptime(DEFAULT_START_DATE, "%Y-%m-%d").date()
    _end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else date.today()

    # Month-start trigger: on the 1st of each month (or when forced), bypass
    # the cache for the most recent trading day and stamp files with today's
    # date so a fresh monthly snapshot flows to prod even if the actual data
    # is from the previous business day.
    today = date.today()
    month_start = force_month_start or is_month_start(today)
    dl_date: Optional[date] = None   # the trading day to download from the API
    stamp_date: Optional[date] = None  # the date to stamp in filenames/trade_date
    if month_start:
        dl_date = most_recent_trading_day(today)
        stamp_date = today
        logger.info(
            "Month-start refresh (today=%s, forced=%s): forcing download for "
            "trading day %s, stamping files with today's date %s.",
            today.isoformat(), force_month_start,
            dl_date.isoformat(), stamp_date.isoformat(),
        )

    if download_mode == "hybrid":
        target_dates = generate_hybrid_dates(_start, _end)
    elif download_mode == "monthly":
        target_dates = generate_monthly_first_biz_dates(_start, _end)
    elif download_mode == "quarterly":
        target_dates = generate_hybrid_dates(_start, _end)
        target_dates = [d for d in target_dates if d.month in (1, 4, 7, 10)]
    else:
        target_dates = generate_monthly_first_biz_dates(_start, _end)

    # On month-start, don't skip today (we need dl_date in the target set).
    if skip_today and not month_start:
        target_dates = [d for d in target_dates if d != today]
    if max_dates is not None and max_dates > 0:
        target_dates = target_dates[:max_dates]

    # On month-start, ensure dl_date is in target_dates (prepend if missing).
    if month_start and dl_date and dl_date not in target_dates:
        target_dates.insert(0, dl_date)

    if not target_dates:
        logger.warning("No target dates generated for %s -> %s (mode=%s)", _start, _end, download_mode)
        return {"out_dir": str(out_dir), "start_date": str(_start), "end_date": str(_end), "target_dates": 0}

    saved_filenames = scan_present_filenames(
        out_dir, glob_pattern="*.md", min_bytes=MD_MIN_BYTES,
    )

    cached_dates = set()
    for fname in saved_filenames:
        m = re.search(r"_(\d{8})_", fname)
        if m:
            try:
                d = datetime.strptime(m.group(1), "%Y%m%d").date()
                cached_dates.add(d)
            except ValueError:
                pass

    # On month-start, force re-download of dl_date by removing it from the
    # cached set (the API is still queried and files are re-downloaded).
    if month_start and dl_date:
        cached_dates.discard(dl_date)

    missing_dates = len(target_dates) - len(cached_dates & set(target_dates))
    cache_ratio = (len(cached_dates & set(target_dates))) / len(target_dates) if target_dates else 1.0

    auto_sleep = sleep_sec
    if cache_ratio < 0.3 or missing_dates > 10:
        auto_sleep = sleep_sec * 2.0
        logger.info(
            "Auto-cooldown: many dates missing (%d/%d cached, ratio=%.1f%%), increasing sleep from %.1fs to %.1fs",
            len(cached_dates & set(target_dates)), len(target_dates), cache_ratio * 100,
            sleep_sec, auto_sleep,
        )

    mode_desc = {
        "hybrid": "hybrid (quarterly history + monthly latest)",
        "monthly": "monthly",
        "quarterly": "quarterly",
    }.get(download_mode, download_mode)
    
    logger.info(
        "Starting SZSE ETF composition download: %s -> %s (%d dates, mode=%s: %s ... %s)",
        _start, _end, len(target_dates), mode_desc,
        target_dates[0].strftime("%Y-%m-%d"),
        target_dates[-1].strftime("%Y-%m-%d"),
    )

    session = build_default_session()
    stats = RunStats(skipped_cached=len(saved_filenames))
    
    # Create unified AntiBotProxy
    proxy_config = AntiBotConfig(
        base_sleep_sec=auto_sleep,
    )
    proxy = AntiBotProxy(proxy_config)

    dates_processed = 0
    try:
        if tqdm:
            dates_iter = tqdm(target_dates, desc="Processing dates", unit="date", leave=True)
        else:
            dates_iter = target_dates
        for d in dates_iter:
            if proxy.is_blocked(LIST_API_URL):
                logger.warning("  [host-blocked] szse.cn blocked, stopping download")
                break

            ymd = d.strftime("%Y%m%d")
            base_headers = random_browser_profile()
            if tqdm:
                dates_iter.set_postfix({"date": str(d)})
            logger.info("== Date %s (%d/%d) ==", d, dates_processed + 1, len(target_dates))

            if d in cached_dates:
                logger.info("  Date %s already cached, skipping list API call", d)
                dates_processed += 1
                continue

            items = fetch_all_etfs_for_date(session, d, base_headers, proxy)
            if not items:
                logger.info("  No ETF items found for %s, skipping", d)
                dates_processed += 1
                proxy.sleep(auto_sleep * 0.3)
                continue

            # On month-start, stamp items for dl_date with today's date so
            # files are named with today's date and the trade_date column in
            # the CSV reflects the new month — even though the actual PCF
            # data is from the most recent trading day (dl_date).
            if month_start and stamp_date and d == dl_date:
                for it in items:
                    it.trade_date = stamp_date
                    it.md_filename = md_filename_for(stamp_date, it.etf_code)
                logger.info("  [month-start] Stamped %d items with %s (data from %s)",
                            len(items), stamp_date.isoformat(), d.isoformat())

            logger.info("  Found %d ETFs for %s", len(items), d)

            date_downloaded = 0
            date_cached = 0
            date_failed = 0

            date_items_need_dl = 0
            for it in items:
                out_file = out_dir / it.md_filename
                if not (it.md_filename in saved_filenames or is_valid_file(out_file, min_bytes=MD_MIN_BYTES)):
                    date_items_need_dl += 1

            date_sleep = auto_sleep
            if date_items_need_dl > 50:
                date_sleep = auto_sleep * 1.5
                logger.info(
                    "  Auto-cooldown: %d items to download, increasing per-item sleep to %.1fs",
                    date_items_need_dl, date_sleep,
                )

            if tqdm:
                items_iter = tqdm(items, desc=f"Downloading ETFs for {d}", unit="etf", leave=False)
            else:
                items_iter = items
            for it in items_iter:
                if proxy.is_blocked(DETAIL_BASE_URL):
                    logger.warning("  [host-blocked] reportdocs.static.szse.cn blocked, skipping remaining items")
                    break

                out_file = out_dir / it.md_filename
                if it.md_filename in saved_filenames or is_valid_file(out_file, min_bytes=MD_MIN_BYTES):
                    # Cached MD: ensure the per-file CSV exists too (backfill legacy MDs)
                    csv_path = out_file.with_suffix(".csv")
                    if not csv_path.exists():
                        try:
                            convert_md_to_csv(out_file, csv_path)
                        except Exception as e:
                            logger.warning("[conv %s %s] per-file CSV conversion failed: %s", it.etf_code, it.trade_date, e)
                    stats.skipped_cached += 1
                    date_cached += 1
                    saved_filenames.add(it.md_filename)
                    continue

                ok = download_composition(session, it, out_file, base_headers, proxy)
                if ok:
                    stats.downloaded += 1
                    stats.files.append(str(out_file))
                    saved_filenames.add(it.md_filename)
                    date_downloaded += 1
                else:
                    stats.failed += 1
                    date_failed += 1
                proxy.sleep(date_sleep)

            logger.info(
                "  Date %s done: downloaded=%d cached=%d failed=%d (total expected=%d)",
                ymd, date_downloaded, date_cached, date_failed, len(items),
            )
            dates_processed += 1
            proxy.sleep(auto_sleep)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")

    summary = stats.to_dict(
        out_dir=str(out_dir),
        start_date=str(_start),
        end_date=str(_end),
        target_dates_total=len(target_dates),
        target_dates_processed=dates_processed,
    )

    if convert_csv:
        csv_result = build_composition_csv(out_dir)
        summary["csv"] = csv_result

    logger.info(
        "Done SZSE ETF composition. downloaded=%d skipped_cached=%d failed=%d empty=%d out=%s (%d dates processed/%d total)",
        stats.downloaded, stats.skipped_cached, stats.failed, stats.empty,
        out_dir, dates_processed, len(target_dates),
    )
    return summary


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Download SZSE ETF creation/redemption composition data and convert to CSV.")
    ap.add_argument("--start-date", type=str, default=DEFAULT_START_DATE,
                    help=f"Start date for backfill (default: {DEFAULT_START_DATE})")
    ap.add_argument("--end-date", type=str, default=None,
                    help="End date for backfill (default: today)")
    ap.add_argument("--out-root", type=str, default=None,
                    help="Alternative output root directory")
    ap.add_argument("--sleep-sec", type=float, default=SLEEP_SEC,
                    help=f"Base sleep seconds between requests (default: {SLEEP_SEC})")
    ap.add_argument("--max-dates", type=int, default=None,
                    help="Limit to N dates (dev/testing)")
    ap.add_argument("--skip-today", action="store_true", default=True,
                    help="Skip today's date (default: enabled)")
    ap.add_argument("--no-skip-today", action="store_true", default=False,
                    help="Include today's date")
    ap.add_argument("--convert-csv", action="store_true", default=True,
                    help="Convert downloaded .md files to CSV (default: enabled)")
    ap.add_argument("--no-convert-csv", action="store_true", default=False,
                    help="Skip CSV conversion")
    ap.add_argument("--download-mode", type=str, default="hybrid",
                    choices=["hybrid", "monthly", "quarterly"],
                    help="Download mode: hybrid (quarterly for history + monthly for latest month), monthly, or quarterly (default: hybrid)")
    ap.add_argument("--force-month-start", action="store_true", default=False,
                    help="Force month-start behavior: bypass cache for the most recent "
                         "trading day and stamp files with today's date. For testing the "
                         "monthly refresh flow on any day.")
    args = ap.parse_args()

    skip_today = args.skip_today and not args.no_skip_today
    convert_csv = args.convert_csv and not args.no_convert_csv

    result = download_szse_etf_composition(
        out_root=args.out_root,
        start_date=args.start_date,
        end_date=args.end_date,
        sleep_sec=args.sleep_sec,
        max_dates=args.max_dates,
        skip_today=skip_today,
        convert_csv=convert_csv,
        download_mode=args.download_mode,
        force_month_start=args.force_month_start,
    )
    print(result)
