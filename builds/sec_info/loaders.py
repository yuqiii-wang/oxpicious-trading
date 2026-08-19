"""CSV loaders for the SZSE ETF quarterly reports.

Parses the per-report CSV files emitted by the SZSE report extractor:

  identify.csv          — key/value pairs (fund statics + report header +
                          section content markers)
  asset_portfolio.csv   — asset-allocation MIX (equity / fixed income / cash /
                          derivatives / ...)  →  sec_reports *_amt / *_pct
  top10_holdings.csv    — top-10 stock holdings  →  stats.sec_composition

All parsers are pure (no DB / no I/O beyond reading the file) so they can be
unit-tested in isolation.  Numeric "-" cells become None (NULL).

Filename convention:  <code>_<YYYY>Q<n>_<type>.csv
  code   — 6-digit SZSE fund code (also the parent directory name)
  type   — identify | asset_portfolio | top10_holdings | industry_portfolio |
           bond_type_portfolio | top10_bonds | remaining_maturity
"""
from __future__ import annotations

import datetime
import os
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from _common.build_commons import parse_num


# ============================================================================
# Filename parsing
# ============================================================================
# <code>_<YYYY>Q<n>_<type>.csv  —  code is 6 digits, type is word chars.
_FILE_RE = re.compile(r"^(\d{6})_(\d{4})Q([1-4])_(\w+)\.csv$")


def parse_report_filename(filename: str) -> Optional[Tuple[str, int, int, str]]:
    """Extract (code, year, quarter, file_type) from a report CSV filename.

    Returns None when the filename doesn't match the <code>_<YYYY>Q<n>_<type>.csv
    pattern (e.g. stray files, the source PDF).
    """
    m = _FILE_RE.match(os.path.basename(filename))
    if not m:
        return None
    return m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)


# ============================================================================
# Report-period + Chinese-date parsing
# ============================================================================
_PERIOD_RE = re.compile(r"(\d{4})\s*年\s*第\s*(\d)\s*季度")
_DATE_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")

# Quarter-end dates: Q1→03-31, Q2→06-30, Q3→09-30, Q4→12-31.
_QUARTER_END = {1: "-03-31", 2: "-06-30", 3: "-09-30", 4: "-12-31"}


def parse_report_period(text: str) -> Optional[Tuple[int, int, datetime.date]]:
    """Parse 报告期 text (e.g. "2020年第1季度") → (year, quarter, quarter_end_date)."""
    if not text:
        return None
    m = _PERIOD_RE.search(str(text))
    if not m:
        return None
    year, quarter = int(m.group(1)), int(m.group(2))
    if quarter not in _QUARTER_END:
        return None
    try:
        return year, quarter, datetime.date.fromisoformat(f"{year}{_QUARTER_END[quarter]}")
    except ValueError:
        return None


def parse_chinese_date(text: str) -> Optional[datetime.date]:
    """Parse Chinese date text (e.g. "2009年10 月14日") → datetime.date."""
    if not text:
        return None
    m = _DATE_RE.search(str(text))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ============================================================================
# Section content marker parsing
# ============================================================================
# identify.csv section values look like "有内容(8行)" or "无内容".
_SECTION_RE = re.compile(r"有内容\s*\((\d+)行\)")


def parse_section_marker(text: str) -> Tuple[bool, Optional[int]]:
    """Parse a section-content marker → (has_content, n_rows).

    "有内容(8行)" → (True, 8); "无内容" → (False, None).
    """
    if not text:
        return False, None
    s = str(text).strip()
    if s.startswith("有内容"):
        m = _SECTION_RE.search(s)
        return True, (int(m.group(1)) if m else None)
    return False, None


def parse_shares(text: str) -> Tuple[Optional[float], str]:
    """Parse 报告期末基金份额总额 text → (numeric_shares, original_text).

    "189,089,089.46份" → (189089089.46, "189,089,089.46份").  Numeric is None
    when unparseable (original_text always preserved for audit).
    """
    raw = "" if text is None else str(text).strip()
    if not raw:
        return None, ""
    # Strip the 份 unit (and surrounding spaces) before numeric parsing.
    cleaned = raw.replace("份", "").strip()
    num = parse_num(cleaned, default=None)  # type: ignore[arg-type]
    return num, raw


# ============================================================================
# identify.csv loader
# ============================================================================
# identify.csv section keys → (has_flag_name, n_rows_name) in the sec_reports row.
_SECTION_KEYS: Dict[str, Tuple[str, str]] = {
    "投资组合报告-报告期末基金资产组合情况":         ("has_asset_portfolio",        "n_asset_portfolio_rows"),
    "投资组合报告-按行业分类的境内股票投资组合":       ("has_industry_portfolio",     "n_industry_portfolio_rows"),
    "投资组合报告-前十名股票投资明细":               ("has_top10_holdings",         "n_top10_holdings_rows"),
    "投资组合报告-按债券品种分类的债券投资组合":       ("has_bond_type_portfolio",    "n_bond_type_portfolio_rows"),
    "投资组合报告-前十名债券投资明细":               ("has_top10_bonds",            "n_top10_bonds_rows"),
    "投资组合报告-投资组合平均剩余期限分布比例":       ("has_remaining_maturity",     "n_remaining_maturity_rows"),
}


def load_identify(path: str) -> Optional[Dict[str, Any]]:
    """Load an identify.csv → dict of parsed fields.

    Returns None when the file is empty / unreadable / missing the report
    period (the minimum required field).  The dict carries BOTH the sec_info
    static fields and the sec_reports header fields; the caller splits them.
    """
    try:
        df = pd.read_csv(path, dtype=str, header=None, names=["key", "value"],
                         skiprows=1, engine="python", on_bad_lines="skip")
    except Exception:
        return None
    if df.empty:
        return None
    kv = dict(zip(
          df["key"].astype(str).str.strip(),
          df["value"].fillna("").astype(str).str.strip()
      ))

    period_text = kv.get("报告期", "")
    parsed_period = parse_report_period(period_text)
    if parsed_period is None:
        return None  # no valid report period → unusable
    year, quarter, report_date = parsed_period

    code = os.path.basename(os.path.dirname(path))
    fund_main_code = kv.get("基金主代码", "").strip()
    shares_num, shares_text = parse_shares(kv.get("报告期末基金份额总额", ""))

    out: Dict[str, Any] = {
        "code": code,
        "report_period": period_text,
        "report_year": year,
        "report_quarter": quarter,
        "report_date": report_date,
        # sec_info statics (latest wins — caller picks max report_date per code)
        "fund_main_code": fund_main_code or None,
        "name": kv.get("基金简称", ""),
        "exchange_abbreviation": kv.get("场内简称") or None,
        "operation_method": kv.get("基金运作方式") or None,
        "contract_effective_date": parse_chinese_date(kv.get("基金合同生效日", "")),
        "benchmark": kv.get("业绩比较基准") or None,
        "risk_return_characteristics": kv.get("风险收益特征") or None,
        "manager": kv.get("基金管理人") or None,
        "custodian": kv.get("基金托管人") or None,
        # sec_reports header
        "total_shares": shares_num,
        "total_shares_text": shares_text or None,
    }
    # Section content flags + row counts.
    for key, (has_name, n_name) in _SECTION_KEYS.items():
        has, n = parse_section_marker(kv.get(key, ""))
        out[has_name] = has
        out[n_name] = n
    return out


# ============================================================================
# asset_portfolio.csv loader
# ============================================================================
# 项目 text → (amt_col, pct_col) suffix pairs in sec_reports.
_ASSET_MIX_MAP: Dict[str, Tuple[str, str]] = {
    "权益投资":               ("equity_amt",        "equity_pct"),
    "固定收益投资":           ("fixed_income_amt",  "fixed_income_pct"),
    "贵金属投资":             ("precious_metal_amt", "precious_metal_pct"),
    "金融衍生品投资":         ("derivatives_amt",   "derivatives_pct"),
    "买入返售金融资产":       ("reverse_repo_amt",  "reverse_repo_pct"),
    "银行存款和结算备付金合计": ("bank_deposit_amt",  "bank_deposit_pct"),
    "合计":                   ("total_assets_amt",  "total_assets_pct"),
}
# "其他各项资产" (2020 reports) and "其他资产" (2026 reports) both → other_assets.
_OTHER_KEYS = ("其他各项资产", "其他资产")


def load_asset_portfolio(path: str) -> Dict[str, Optional[float]]:
    """Load asset_portfolio.csv → dict of {col: value} for the sec_reports MIX.

    Returns an empty dict when the file is empty / unreadable.  "-" cells become
    None.  Columns not present in the CSV are absent from the dict (caller
    leaves them NULL).
    """
    mix: Dict[str, Optional[float]] = {}
    try:
        df = pd.read_csv(path, dtype=str, engine="python", on_bad_lines="skip")
    except Exception:
        return mix
    if df.empty or df.shape[1] < 4:
        return mix
    # Columns are positional: [序号, 项目, 金额(元), 占比(%)] — names vary in
    # full-width vs half-width parens, so read by position.
    for _, r in df.iterrows():
        item = str(r.iloc[1]).strip() if pd.notna(r.iloc[1]) else ""
        if not item:
            continue
        amt_raw = r.iloc[2] if df.shape[1] > 2 else ""
        pct_raw = r.iloc[3] if df.shape[1] > 3 else ""
        amt = parse_num(amt_raw, default=None) if amt_raw not in ("-", "--", "", None) else None
        pct = parse_num(pct_raw, default=None) if pct_raw not in ("-", "--", "", None) else None
        if item in _ASSET_MIX_MAP:
            amt_col, pct_col = _ASSET_MIX_MAP[item]
            mix[amt_col] = amt
            mix[pct_col] = pct
        elif item in _OTHER_KEYS:
            mix["other_assets_amt"] = amt
            mix["other_assets_pct"] = pct
    return mix


# ============================================================================
# top10_holdings.csv loader
# ============================================================================
# Columns: 序号, 股票代码, 股票名称, 数量(股), 公允价值(元), 占比(％)
# Some reports append a 7th "remark" column (新股锁定 / 新发未上市) on locked-
# share rows — those rows DUPLICATE holdings already disclosed in the main /
# new-stock sections, so they are skipped (last col non-numeric → skip).
def load_top10_holdings(path: str) -> List[Dict[str, Any]]:
    """Load top10_holdings.csv → list of sec_composition row dicts.

    Each dict has: stock_code, stock_name, weight_pct (no code/snapshot_date/
    rank — those are added by the caller).  Rows are DEDUPED by stock_code
    (first occurrence wins; locked-share remark rows are skipped).  Returns []
    when the file is empty / unreadable.
    """
    try:
        df = pd.read_csv(path, dtype=str, engine="python", on_bad_lines="skip")
    except Exception:
        return []
    if df.empty or df.shape[1] < 6:
        return []
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    for _, r in df.iterrows():
        # stock_code is column 1 (positional), stock_name column 2.
        sc = str(r.iloc[1]).strip() if pd.notna(r.iloc[1]) else ""
        if not sc or not (sc.isdigit() and len(sc) == 6):
            continue
        if sc in seen:
            continue
        # weight = last column when numeric; non-numeric last col = remark row
        # (新股锁定 / 新发未上市) → skip (it duplicates a main/new-stock row).
        last_val = r.iloc[-1] if pd.notna(r.iloc[-1]) else ""
        weight = parse_num(last_val, default=None)
        if weight is None:
            continue
        sn = str(r.iloc[2]).strip() if pd.notna(r.iloc[2]) else ""
        seen.add(sc)
        rows.append({
            "stock_code": sc,
            "stock_name": sn,
            "weight_pct": float(weight),
        })
    return rows


# ============================================================================
# Report-directory scanner
# ============================================================================
def iter_report_files(reports_dir: str) -> List[Tuple[str, int, int, str, str]]:
    """Scan the reports dir → sorted list of (code, year, quarter, type, path).

    Walks each <reports_dir>/<code>/ subdirectory and matches the
    <code>_<YYYY>Q<n>_<type>.csv pattern.  Sorted by (code, year, quarter, type)
    so callers process reports in chronological order per fund.
    """
    out: List[Tuple[str, int, int, str, str]] = []
    if not os.path.isdir(reports_dir):
        return out
    for code in sorted(os.listdir(reports_dir)):
        cdir = os.path.join(reports_dir, code)
        if not os.path.isdir(cdir):
            continue
        for fn in sorted(os.listdir(cdir)):
            parsed = parse_report_filename(fn)
            if parsed is None:
                continue
            c, y, q, ftype = parsed
            out.append((c, y, q, ftype, os.path.join(cdir, fn)))
    out.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    return out
