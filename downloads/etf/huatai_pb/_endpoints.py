"""Shared endpoint constants + parsers for huatai-pb.com (华泰柏瑞基金) fund data.

Studied from ``https://www.huatai-pb.com/products/zhishu/510880/index.html``
(server-rendered page; logic in ``/js/product-index.js``, site-wide fund data
in ``/common/index.html.js``):

  NAV table (单位净值/累计净值, any date window, ALL rows in one request):
    GET /common-web/chart/fundnettable/getFundNetTableJson
        ?fundcode=510880&from=2000-01-01&to=2026-09-29&pages=1-5000
        &siteId=9496d23c648f4a72a79150c3f2ac669b
    → {"currentPage", "totalPage", "dataList": [{date, netvalue,
       totalnetvalue, dayinc (PERCENT, e.g. -0.505 = -0.51%), status,
       fundcode}, ...]} — ``pages=N-ROWS`` accepts huge row counts, so the
    full 成立以来 history (4813 rows for 510880, back to 2006-11-17) comes
    back in ONE request.

  Products index all-funds table (/products/index.html — server-rendered
  全部基金 table ``tableId_all`` > ``tbody.tableIdAallTbody``, ONE GET for
  the whole fund family): each ``<tr>`` carries attrs (fundcode, shortname,
  risk, weekinc/monthinc/seasoninc/halfinc/thisyearinc/f1yearinc/inc, ...)
  plus cells ``a.fund-name`` (基金名称), ``p.latest-net-worth`` +
  ``p.fund-date`` (最新净值 + 净值日期, "--" for not-yet-launched funds) and
  the FIRST ``p.cumulative-net-worth`` = 日涨跌 (signed percent, "-0.51%";
  the later same-class cells are 今年以来/近半年/近一年/成立以来, matching
  the thead column order). Money funds (货币型, tbodies HuobiAllTbody/
  HuobiAllbTbody) show 万份收益/7日年化 instead — no 最新净值/日涨跌 — and
  are not parsed. The page is UTF-8 but the response carries no charset,
  so requests decodes ``resp.text`` as ISO-8859-1 (mojibake) — decode
  ``resp.content`` explicitly.

  PCF 申购赎回清单 (exact trading-day snapshot; empty dayInfo on
  non-trading days; history back to inception):
    POST /etf-web/etf/index.json   (form: fundcode, beginDate=YYYY-MM-DD)
    → {"status": "success", "maxDate": "2026-09-29",
       "fundname": "红利ETF华泰柏瑞",
       "dayInfo": {nav, navpercu, cashcomponent, estimatecashcomponent,
                   maxcashratio, creationredemptionunit, recordnum,
                   underlyingindex, publish, ...},   # thousands separators!
       "stockList": [{stockcode, stockshort, number, tdbz, premiumrate,
                      discountrate, tdje, gpsc (XSHG/XSHE/XHKG — absent in
                      older records), tradingday (epoch ms), ...}]}
    Numbers carry thousands separators ("1,672,738.46") — strip commas
    before parsing.

  Fund profile 基金概况 (ALL funds in one GET): the site-wide
    ``/common/index.html.js`` defines ``var FundArr = [ {...}, ... ]`` with
    per-fund fields (fundcode, fundname, fundFullName, fundtype, managerName
    公司, trusteeName, brokername 基金经理, setupdate, performanceBenchmark,
    investmentTarget, levelofriskStr, ...).

  Dividends 分红信息: static HTML on each product page — ``<div
  id="fhxx">`` wraps ``<tbody id="fhTbody">`` rows (分红年度, 权益登记日,
  红利发放日/再投资日, 每10份基金份额分红（元）— per 10 SHARES, 1.430 =
  0.143 元/份). Rows carry class="hide" (client-side pagination) but ALL
  rows stay in the markup; a fund with no distributions renders an
  info-noData 暂无内容 block instead. 拆分记录 table exists but is
  display:none and empty for 510880.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from downloads._common import (
    AntiBotProxy,
    antibot_config_for,
    build_headers_with_referer,
)

SITE_BASE = "https://www.huatai-pb.com"
# Global var in /common/index.html.js: var siteId = "9496d23c..."
SITE_ID = "9496d23c648f4a72a79150c3f2ac669b"

PRODUCT_PAGE_URL = SITE_BASE + "/products/zhishu/{code}/index.html"
NAV_TABLE_URL = SITE_BASE + "/common-web/chart/fundnettable/getFundNetTableJson"
PCF_URL = SITE_BASE + "/etf-web/etf/index.json"
COMMON_JS_URL = SITE_BASE + "/common/index.html.js"
PRODUCTS_INDEX_URL = SITE_BASE + "/products/index.html"

# One page covers the full 成立以来 history (510880 has ~4.8k rows).
NAV_PAGE_SPEC = "1-5000"

# Curated watchlist — the "checkin" funds whose product pages are studied
# (products/zhishu/<code>/index.html). Lives in the INDEPENDENT config file
# ``downloads/etf/huatai_pb/config.json`` (checkin_funds array) — edit the
# file, not this module. The per-fund downloads (archive / dividends /
# composition) default to EXACTLY these codes; the daily trend downloader
# stays all-funds (products index page — one request for the whole family)
# regardless of this list.
_CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def _load_checkin_fund_codes() -> Tuple[str, ...]:
    """Checkin codes from config.json — the config file is the single
    source of truth, so a missing/malformed file raises (never silently
    falls back to an empty or hardcoded universe)."""
    try:
        cfg = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        funds = cfg["checkin_funds"]
        codes = tuple(
            str(f["code"]).strip()
            for f in funds
            if str(f.get("code", "")).strip()
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"huatai_pb config invalid ({_CONFIG_PATH}): {exc}") from exc
    if not codes:
        raise RuntimeError(
            f"huatai_pb config has no checkin_funds entries ({_CONFIG_PATH})")
    return codes


CHECKIN_FUND_CODES: Tuple[str, ...] = _load_checkin_fund_codes()

# Listed-ETF code prefixes (same families as the SSE/SZSE composition
# downloaders). Used to filter FundArr to exchange-listed ETFs and to
# suffix codes. The 561/562/563/589 blocks are DB-verified listed SSE ETF
# prefixes (stats.etf_identity, exchange-built) missing from the original
# exchange-downloader families.
SSE_ETF_PREFIXES: Tuple[str, ...] = (
    "510", "511", "512", "513", "515", "516", "517", "518",
    "560", "561", "562", "563", "588", "589",
)
SZSE_ETF_PREFIXES: Tuple[str, ...] = ("159",)

# PCF tdbz (替代标志) numeric → SZSE/SSE-compatible Chinese label (same
# vocabulary the szse_etf_comp/sse_etf_comp CSVs use for cash_sub_flag).
TDBZ_LABEL_MAP: Dict[str, str] = {
    "0": "禁止",
    "1": "允许",
    "2": "必须",
    "3": "深市或京市退补",
    "4": "深市或京市必须",
    "5": "非沪深市场退补",
    "6": "非沪深市场必须",
    "7": "港市退补",
    "8": "港市必须",
}

# stockList gpsc → (market label, exchange suffix).
GPSC_MARKET_MAP: Dict[str, Tuple[str, str]] = {
    "XSHG": ("上海证券交易所", "SS"),
    "XSHE": ("深圳证券交易所", "SZ"),
    "XHKG": ("香港市场", "HK"),
}

_HEADERS: Dict[str, str] = build_headers_with_referer(
    SITE_BASE + "/",
    extra={
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    },
)


def huatai_headers(referer_code: Optional[str] = None) -> Dict[str, str]:
    """Request headers with the product page as Referer."""
    if referer_code:
        return build_headers_with_referer(
            PRODUCT_PAGE_URL.format(code=referer_code),
            extra={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
    return _HEADERS


def build_proxy(service: str, sleep_sec: Optional[float] = None) -> AntiBotProxy:
    """AntiBotProxy for a huatai_pb downloader service.

    Pacing (base sleep, jitter, timeouts, anti-bot toggles) resolves from
    the service's external_request_config JSON row; a non-None
    *sleep_sec* overrides only the row's base sleep.
    """
    if sleep_sec is None:
        return AntiBotProxy(service=service)
    return AntiBotProxy(replace(
        antibot_config_for(service), base_sleep_sec=sleep_sec,
    ))


def is_listed_etf_code(code: str) -> bool:
    """True for 6-digit exchange-listed ETF codes (51x/56x/58x SS, 159 SZ)."""
    s = str(code).strip()
    return len(s) == 6 and s.isdigit() and (
        s.startswith(SSE_ETF_PREFIXES) or s.startswith(SZSE_ETF_PREFIXES)
    )


def etf_market_of(code: str) -> str:
    """'上海' for SSE-listed ETF codes, '深圳' for SZSE-listed ones."""
    s = str(code).strip()
    if s.startswith(SSE_ETF_PREFIXES):
        return "上海"
    return "深圳"


def parse_comma_number(token: Any) -> Optional[float]:
    """'1,672,738.46' → 1672738.46; ''/'--'/None → None (never raises)."""
    if token is None:
        return None
    s = str(token).strip().replace(",", "")
    if s in ("", "-", "--", "无"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def resolve_stock_market(gpsc: str, stock_code: str) -> Tuple[str, str]:
    """Return (market label, exchange suffix) for a PCF component.

    Uses the record's gpsc when present; older PCF records lack it, so fall
    back to the A-share code prefixes (mirrors the SSE PCF downloader).
    """
    if gpsc in GPSC_MARKET_MAP:
        return GPSC_MARKET_MAP[gpsc]
    code = str(stock_code).strip()
    if len(code) == 6 and code.isdigit():
        prefix = code[:3]
        if prefix in ("600", "601", "603", "605", "688"):
            return "上海证券交易所", "SS"
        if prefix in ("000", "001", "002", "003", "300", "301"):
            return "深圳证券交易所", "SZ"
    return "", ""


def fetch_nav_rows(
    session: requests.Session,
    proxy: AntiBotProxy,
    fund_code: str,
    date_from: str,
    date_to: str,
    logger: Any = None,
) -> List[Dict[str, Any]]:
    """Fetch NAV rows for one fund in [date_from, date_to] (ISO dates).

    Returns the raw dataList dicts (date desc), [] on failure/empty.
    """
    resp = proxy.get(
        session,
        NAV_TABLE_URL,
        params={
            "fundcode": fund_code,
            "from": date_from,
            "to": date_to,
            "pages": NAV_PAGE_SPEC,
            "siteId": SITE_ID,
        },
        headers=huatai_headers(fund_code),
        logger=logger,
        log_tag=f"[nav {fund_code}]",
    )
    if resp is None or resp.status_code != 200:
        return []
    try:
        payload = resp.json()
    except (ValueError, json.JSONDecodeError):
        return []
    rows = payload.get("dataList") or []
    return rows if isinstance(rows, list) else []


def fetch_pcf_payload(
    session: requests.Session,
    proxy: AntiBotProxy,
    fund_code: str,
    begin_date: str,
    logger: Any = None,
) -> Optional[Dict[str, Any]]:
    """POST the PCF query for one fund on one trading day (ISO date).

    Returns the parsed payload dict, or None on request/parse failure.
    A non-trading date yields a successful payload with an EMPTY dayInfo —
    callers must treat that as "no PCF for this date".
    """
    resp = proxy.post(
        session,
        PCF_URL,
        data={"fundcode": fund_code, "beginDate": begin_date},
        headers=huatai_headers(fund_code),
        logger=logger,
        log_tag=f"[pcf {fund_code} {begin_date}]",
    )
    if resp is None or resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return None
    return payload


# ---------------------------------------------------------------------------
# Products index all-funds table — /products/index.html
# ---------------------------------------------------------------------------

# The 全部基金 table's server-rendered tbody. Money funds (货币型) sit in
# separate tbodies (HuobiAllTbody/HuobiAllbTbody) with a different layout
# (万份收益/7日年化, no 最新净值/日涨跌 columns) and are not parsed.
PRODUCTS_INDEX_TBODY = 'tableIdAallTbody'

_RE_TBODY_OPEN = re.compile(
    r'<tbody\s+class="' + PRODUCTS_INDEX_TBODY + r'"\s*>')
_RE_TR_BLOCK = re.compile(r"<tr\b[^>]*>.*?</tr>", re.S)
_RE_FUNDCODE_ATTR = re.compile(r'\bfundcode="([^"]*)"')
_RE_FUND_NAME = re.compile(r'class="fund-name"[^>]*>\s*([^<]*?)\s*</a>')
_RE_LATEST_NAV = re.compile(
    r'class="latest-net-worth"[^>]*>\s*([^<]*?)\s*</p>')
_RE_NAV_DATE = re.compile(r'class="fund-date"[^>]*>\s*([^<]*?)\s*</p>')
# 日涨跌 is the FIRST cumulative-net-worth cell in a row: the thead puts it
# (col 3) before the return columns 今年以来/近半年/近一年/成立以来, whose
# cells share the same class.
_RE_DAY_PCT = re.compile(
    r'class="cumulative-net-worth[^"]*"[^>]*>\s*([^<]*?)\s*</p>')


def fetch_products_index_html(
    session: requests.Session,
    proxy: AntiBotProxy,
    logger: Any = None,
) -> Optional[str]:
    """GET the products index page (all-funds table) as decoded HTML.

    The response carries no charset, so requests would decode ``resp.text``
    as ISO-8859-1 (mojibake) — decode ``resp.content`` as UTF-8 explicitly.
    Returns None on request failure.
    """
    resp = proxy.get(
        session,
        PRODUCTS_INDEX_URL,
        headers=huatai_headers(),
        logger=logger,
        log_tag="[products-index]",
    )
    if resp is None or resp.status_code != 200:
        return None
    return resp.content.decode("utf-8", errors="replace")


def parse_products_index_nav_rows(html_text: str) -> List[Dict[str, Any]]:
    """Parse the 全部基金 tbody into one dict per fund:
    ``{"code", "name", "nav", "nav_date", "day_pct"}``.

    * nav / day_pct — floats, day_pct in PERCENT ("-0.51%" → -0.51);
      None when the page shows "--" (not-yet-launched funds).
    * nav_date — ISO 净值日期, '' for "--".
    Returns [] when the tbody is absent or empty.
    """
    m = _RE_TBODY_OPEN.search(html_text)
    if not m:
        return []
    end = html_text.find("</tbody>", m.end())
    if end < 0:
        return []

    rows: List[Dict[str, Any]] = []
    for tr in _RE_TR_BLOCK.findall(html_text[m.end():end]):
        code_m = _RE_FUNDCODE_ATTR.search(tr)
        name_m = _RE_FUND_NAME.search(tr)
        if not code_m or not name_m:
            continue
        nav_m = _RE_LATEST_NAV.search(tr)
        date_m = _RE_NAV_DATE.search(tr)
        pct_m = _RE_DAY_PCT.search(tr)
        nav_date = date_m.group(1).strip() if date_m else ""
        rows.append({
            "code": code_m.group(1).strip(),
            "name": name_m.group(1),
            "nav": parse_comma_number(nav_m.group(1)) if nav_m else None,
            "nav_date": "" if nav_date in ("-", "--") else nav_date,
            "day_pct": (parse_comma_number(pct_m.group(1).rstrip("%"))
                        if pct_m else None),
        })
    return rows


# ---------------------------------------------------------------------------
# FundArr parsing (fund intro) — /common/index.html.js
# ---------------------------------------------------------------------------

_RE_FUNDARR_START = re.compile(r"var\s+FundArr\s*=\s*\[")


def _slice_balanced(js_text: str, open_idx: int) -> Optional[Tuple[int, int]]:
    """Index pair of the balanced [...] starting at *open_idx* (string-aware)."""
    depth = 0
    in_str = False
    escaped = False
    quote = ""
    for i in range(open_idx, len(js_text)):
        ch = js_text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                in_str = False
            continue
        if ch in "\"'":
            in_str = True
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return open_idx, i
    return None


# Intro CSV fields — FundArr keys (kept verbatim; the JS is machine-generated
# JSON per entry, so json.loads works after balanced slicing).
INTRO_FIELDS: Dict[str, str] = {
    "fundcode": "code",
    "fundname": "name",
    "fundFullName": "full_name",
    "fundtype": "fund_type",
    "managerName": "company",
    "brokername": "manager",
    "trusteeName": "trustee",
    "setupdate": "setup_date",
    "levelofriskStr": "risk_level",
    "performanceBenchmark": "benchmark",
    "investmentTarget": "invest_target",
}


def parse_fund_arr(js_text: str) -> List[Dict[str, str]]:
    """Parse ``var FundArr = [...]`` from the site JS into a list of dicts.

    Returns [] when the array is absent or unparseable.
    """
    m = _RE_FUNDARR_START.search(js_text)
    if not m:
        return []
    span = _slice_balanced(js_text, m.end() - 1)
    if span is None:
        return []
    try:
        arr = json.loads(js_text[span[0]:span[1] + 1])
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(arr, list):
        return []
    out: List[Dict[str, str]] = []
    for entry in arr:
        if isinstance(entry, dict) and str(entry.get("fundcode", "")).strip():
            out.append({
                str(k): ("" if v is None else str(v))
                for k, v in entry.items()
            })
    return out


def read_cached_fund_arr(cache_path: Path) -> List[Dict[str, str]]:
    """Load a previously archived common/index.html.js and parse FundArr."""
    try:
        js_text = cache_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return parse_fund_arr(js_text)
