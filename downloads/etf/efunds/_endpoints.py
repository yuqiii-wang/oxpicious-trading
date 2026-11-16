"""Shared endpoint constants + parsers for the E Fund fund-site family
(易方达 — efundcf.com.cn 财富 pages over www.efunds.com.cn /
api.efunds.com.cn backends).

Studied from ``https://www.efundcf.com.cn/lm/jjcs/`` (基金超市) and
``https://www.efundcf.com.cn/fund/110/110015.shtml`` /
``https://www.efundcf.com.cn/fund/159/159915.shtml`` (fund detail;
browser network capture 2026-11-14):

  Fund list (ALL funds in ONE GET — the whole E Fund family, the fund
  supermarket page loads exactly this):
    GET https://www.efunds.com.cn/fund/public/fundlist.json
    → {"funds": [...975...], "bfFunds": [...3 money-style...],
       "etfInfos": [...]} with per-fund fields: fundcode, fundname,
       fundSourceName (全称), fundType (混合型/股票型/ETF联接基金/债券型/
       货币型/基金中基金/其他类型), fundpinyin, setupdate (ISO),
       managerName (基金经理), netvalue (单位净值), rzd (日涨跌, PERCENT),
       tdate (净值日期 YYYYMMDD), qrnh (7日年化, money funds only),
       properties.riskLevel ("中高风险(R4)"), level1Type/level2Type.
    Money funds (货币型 + bfFunds 保证金) show 万份收益/7日年化 instead
    of NAV — excluded from the trend rows, same convention as the
    huatai products-index parse.

  Quarterly holdings composition 季报持仓 (exact quarter-end snapshot;
    empty assets/investList for dates before the fund's data begins):
    POST https://api.efunds.com.cn/xcowch/front/efund/assetinfo
         Content-Type: application/json
         body {"fundCode": "110015", "xDate": "2026-06-30"}
    → {"status": 1, "data": {"investReportDate", "searchDate",
       "isOfflineFund", "isIndexFund", "valueDisplay": "公允价值(元)",
       "assets": [{className, money, ratio, endDate, year, quarter}...],
       "investList": [{"investInfoData": [{symbol, name, norder,
         ratio ("0.0955" = 9.55%), formatratio ("9.55"), money
         (comma-grouped fair value), amount (shares), mktcode
         (XSHG/XSHE), isetf}...]}]}}
    — the TOP-10 stock holdings of the quarterly report. The fund name
    is NOT in the payload (callers carry it from the fundlist universe).

  Fund intro 基金概况 (per-fund page, SERVER-RENDERED — no XHR):
    GET https://www.efundcf.com.cn/fund/<first-3-digits>/<code>.shtml
    → <table class="baseinfo-table" data-level1desc="被动股票指数"
       data-level2desc="被动股票指数-ETF"> rows
       <td class="baseinfo-table-left">基金名称:</td>
       <td class="baseinfo-table-right">...</td>
    with rows 基金名称/基金简称/场内简称/基金代码/上市场所/基金类型/
    成立日期/基金管理人/基金经理 (anchor names)/基金托管人/基金规模/
    投资范围/标的指数名称/投资比例/投资目标/业绩比较基准/风险收益特征.
    The response is UTF-8; decode ``resp.content`` explicitly.

  (Full NAV history — consumed by downloads.etf.efunds.archive since
   2026-11-14: POST api.efunds.com.cn/xcowch/front/fund/nav {fundCode,
   pageNum, pageSize} — pageSize 5000 returns the whole 成立以来 history
   in one request, rows date-desc, probe 2026-11-14 in
   temp_scripts/probe_efunds_nav.py: {"status": 1, "data": {"total": 899,
   "data": [{navDate, shortName, netValue, totalNetValue, growthRate,
   ...}]}}; the export twin fund/nav/export streams XLSX.)

Anti-bot cooldown contract (the reason this family exists beyond a
huatai_pb copy): every loop that walks per-fund requests (composition
backfill/seasonal, intro) calls :func:`await_host_cooldown` BEFORE each
request — while the AntiBotProxy's host tracker has the host blocked
(403/429), the guard sleeps in slices until the tracker's own cooldown
auto-unblock fires, so no request is fired at a blocked host and the
host is never hammered through an active cooldown window. A proxy built
by :meth:`EFundsEndpoints.build_proxy` always resolves the service row's
``host_block_cooldown_sec`` (service= is passed even when the base sleep
is overridden, unlike the huatai_pb copy).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from database.sec_statics.active_funds_store import require_active_etf_funds
from downloads._common import (
    AntiBotProxy,
    antibot_config_for,
    build_headers_with_referer,
    service_urls,
)

# Endpoint roles — each efunds service's external_request_config JSON row
# carries the URLs it requests under these names; the fetch helpers below
# resolve them from the CALLING service's row, so a missing role raises at
# the point of use:
#   site_base   https://www.efundcf.com.cn            (fallback referer base)
#   fundlist    https://www.efunds.com.cn/fund/public/fundlist.json
#   assetinfo   https://api.efunds.com.cn/xcowch/front/efund/assetinfo
#   nav         https://api.efunds.com.cn/xcowch/front/fund/nav
#               (full-history archive — the downloads.etf.efunds.archive row)
#   fund_page   https://www.efundcf.com.cn/fund/{p3}/{code}.shtml
#               (p3 = first 3 digits of the code — /fund/159/159915.shtml)

# Money-fund fundType on the fundlist (shows 万份收益/7日年化, no NAV).
MONEY_FUND_TYPE = "货币型"

# Listed-ETF code prefixes (same families as the huatai_pb / SSE / SZSE
# composition downloaders). The composition + intro universe = fundlist
# entries matching these prefixes, FURTHER restricted to the curated
# ACTIVE ETF registry below (2026-11-14 — only actively-managed ETFs get
# per-fund data; the whole-family trend stays all-funds). 581 is the SSE
# 主动管理型ETF block (first batch 2026-10 — 581050 品质均衡主动管理ETF),
# 158 the SZSE twin range (158900+ — none of this issuer's funds yet,
# kept for is_listed_etf_code parity).
SSE_ETF_PREFIXES: Tuple[str, ...] = (
    "510", "511", "512", "513", "515", "516", "517", "518",
    "560", "561", "562", "563", "581", "588", "589",
)
SZSE_ETF_PREFIXES: Tuple[str, ...] = ("158", "159")

# Registry owner of this family — the per-fund services' default universe
# is the curated ACTIVE ETF registry (venue=etf entries of
# database/sec_statics/active_funds/e_fund.json), NOT the whole listed-ETF
# fundlist set. A missing/empty registry raises (never silently falls back
# to an all-funds crawl). The daily trend stays all-funds regardless.
ACTIVE_FUNDS_OWNER_ID = "e_fund"


def active_etf_universe_codes() -> Tuple[str, ...]:
    """Default per-fund universe — the owner's curated active ETF codes."""
    return tuple(sorted(
        f.code for f in require_active_etf_funds(ACTIVE_FUNDS_OWNER_ID)))


def filter_universe_to_active(
    universe: List[Dict[str, str]],
    logger: Optional[logging.Logger] = None,
) -> List[Dict[str, str]]:
    """Intersect a fundlist universe with the curated active-ETF codes.

    Warns when a registry code is absent from the fundlist universe (a
    fund newer than fundlist.json) so the gap is visible in service logs.
    """
    wanted = set(active_etf_universe_codes())
    out = [f for f in universe if f["code"] in wanted]
    missing = wanted - {f["code"] for f in out}
    if missing and logger is not None:
        logger.warning("[universe] %d active-ETF registry codes absent from "
                       "the fundlist universe: %s",
                       len(missing), ",".join(sorted(missing)))
    return out

# investList mktcode → (market label, exchange suffix) — the same shape
# the huatai PCF gpsc map uses, for suffixed stock codes in the CSVs.
MKTCODE_MARKET_MAP: Dict[str, Tuple[str, str]] = {
    "XSHG": ("上海证券交易所", "SS"),
    "XSHE": ("深圳证券交易所", "SZ"),
    "XHKG": ("香港市场", "HK"),
}

# Cooldown guard defaults — two full 600s tracker windows plus margin.
COOLDOWN_SLICE_SEC: float = 30.0
COOLDOWN_MAX_WAIT_SEC: float = 1260.0


def is_listed_etf_code(code: str) -> bool:
    """True for 6-digit exchange-listed ETF codes (51x/56x/58x SS, 159 SZ)."""
    s = str(code).strip()
    return len(s) == 6 and s.isdigit() and (
        s.startswith(SSE_ETF_PREFIXES) or s.startswith(SZSE_ETF_PREFIXES)
    )


def parse_comma_number(token: Any) -> Optional[float]:
    """'1,674,835,129.15' → 1674835129.15; ''/'--'/None → None (never raises)."""
    if token is None:
        return None
    s = str(token).strip().replace(",", "")
    if s in ("", "-", "--", "无"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# (month, day) of the four quarter-end report dates.
QUARTER_END_DAYS: Tuple[Tuple[int, int], ...] = (
    (3, 31), (6, 30), (9, 30), (12, 31),
)


def quarter_ends(start: date, end: date) -> List[date]:
    """Quarter-end dates (03-31/06-30/09-30/12-31) in [start, end], ascending."""
    out: List[date] = []
    for year in range(start.year, end.year + 1):
        for month, day in QUARTER_END_DAYS:
            d = date(year, month, day)
            if start <= d <= end:
                out.append(d)
    return out


def latest_quarter_end(on_or_before: date) -> date:
    """The newest quarter-end date ≤ *on_or_before* (any date ≥ 1901-01-01)."""
    for year in (on_or_before.year, on_or_before.year - 1):
        for month, day in reversed(QUARTER_END_DAYS):
            d = date(year, month, day)
            if d <= on_or_before:
                return d
    raise ValueError(f"no quarter-end before {on_or_before}")


def await_host_cooldown(
    proxy: AntiBotProxy,
    url: str,
    logger: Optional[logging.Logger] = None,
    *,
    slice_sec: float = COOLDOWN_SLICE_SEC,
    max_wait_sec: float = COOLDOWN_MAX_WAIT_SEC,
) -> bool:
    """Block until the host of *url* is unblocked, sleeping in slices.

    The AntiBotProxy host tracker auto-unblocks once its cooldown window
    (host_block_cooldown_sec of the service's external_request_config
    row) has elapsed — this guard only waits that out, re-polling
    ``proxy.is_blocked`` every *slice_sec*. True when the host is
    unblocked (safe to request); False when still blocked after
    *max_wait_sec* (the caller aborts the run and a re-run resumes from
    the cached files).
    """
    waited = 0.0
    while proxy.is_blocked(url):
        if waited >= max_wait_sec:
            if logger:
                logger.warning(
                    "[cooldown] host of %s still blocked after %.0fs — "
                    "aborting this run (re-run resumes from cached files)",
                    url, waited)
            return False
        if logger and waited == 0.0:
            logger.info(
                "[cooldown] host of %s blocked — waiting out the cooldown "
                "(slices of %.0fs, up to %.0fs)", url, slice_sec, max_wait_sec)
        time.sleep(slice_sec)
        waited += slice_sec
    return True


class EFundsEndpoints:
    """Endpoint access layer of the E Fund (efundcf/efunds) downloaders
    (stateless, the HuataiPbEndpoints pattern).

    ``efunds_headers`` / ``build_proxy`` resolve the per-service request
    config and ``fetch_fundlist`` / ``fetch_assetinfo`` /
    ``fetch_fund_intro_html`` hit the endpoints. ``session`` / ``proxy`` /
    ``service`` stay per-call arguments (each downloader passes its own
    service row), so the class holds no config.
    """

    def efunds_headers(
        self, service: str, fund_code: Optional[str] = None,
    ) -> Dict[str, str]:
        """Request headers with the fund page (or site base) as Referer."""
        urls = service_urls(service)
        referer = (
            urls["fund_page"].format(p3=str(fund_code)[:3], code=fund_code)
            if fund_code else urls["site_base"] + "/"
        )
        return build_headers_with_referer(
            referer,
            extra={
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

    def build_proxy(
        self, service: str, sleep_sec: Optional[float] = None,
    ) -> AntiBotProxy:
        """AntiBotProxy for an efunds downloader service.

        Pacing (base sleep, jitter, timeouts, anti-bot toggles) resolves
        from the service's external_request_config JSON row; a non-None
        *sleep_sec* overrides only the row's base sleep. ``service=`` is
        passed in BOTH branches so the proxy's host tracker resolves the
        row's ``host_block_cooldown_sec`` even with the sleep override
        (the huatai_pb copy loses it on the override branch — fixed here
        per the efunds cooldown contract).
        """
        if sleep_sec is None:
            return AntiBotProxy(service=service)
        return AntiBotProxy(
            replace(antibot_config_for(service), base_sleep_sec=sleep_sec),
            service=service,
        )

    def fetch_fundlist(
        self,
        session: requests.Session,
        proxy: AntiBotProxy,
        service: str,
        logger: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """GET fundlist.json (the whole E Fund fund family in one request).

        Returns the parsed payload dict, or None on request/parse failure.
        """
        resp = proxy.get(
            session,
            service_urls(service)["fundlist"],
            headers=self.efunds_headers(service),
            logger=logger,
            log_tag="[fundlist]",
        )
        if resp is None or resp.status_code != 200:
            return None
        try:
            payload = resp.json()
        except (ValueError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def fetch_assetinfo(
        self,
        session: requests.Session,
        proxy: AntiBotProxy,
        fund_code: str,
        x_date: str,
        service: str,
        logger: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """POST the quarterly asset-info query for one fund at one
        quarter-end date (ISO).

        Returns the parsed payload dict, or None on request/parse
        failure. A date with no published report returns a successful
        payload with EMPTY assets/investList — callers must treat that
        as "no report for this quarter".
        """
        resp = proxy.post(
            session,
            service_urls(service)["assetinfo"],
            data=json.dumps({"fundCode": fund_code, "xDate": x_date}),
            headers={**self.efunds_headers(service, fund_code),
                     "Content-Type": "application/json; charset=utf-8"},
            logger=logger,
            log_tag=f"[assetinfo {fund_code} {x_date}]",
        )
        if resp is None or resp.status_code != 200:
            return None
        try:
            payload = resp.json()
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("status") != 1:
            return None
        return payload

    def fetch_nav_history(
        self,
        session: requests.Session,
        proxy: AntiBotProxy,
        fund_code: str,
        service: str,
        logger: Any = None,
        page_size: int = 5000,
    ) -> Optional[Dict[str, Any]]:
        """POST the full-history NAV query for one fund — the 成立以来
        archive consumed by downloads.etf.efunds.archive.

        One request at pageSize 5000 covers the whole history (563030:
        899 rows, 2023-02-17 → today, date-desc — probe 2026-11-14). The
        row fields (navDate/shortName/netValue/totalNetValue/growthRate)
        are mapped by the archive module's ``nav_rows_to_frame``.

        Returns the parsed payload dict, or None on request/parse
        failure / non-success status.
        """
        resp = proxy.post(
            session,
            service_urls(service)["nav"],
            data=json.dumps({"fundCode": fund_code, "pageNum": 1,
                             "pageSize": page_size}),
            headers={**self.efunds_headers(service, fund_code),
                     "Content-Type": "application/json; charset=utf-8"},
            logger=logger,
            log_tag=f"[nav {fund_code}]",
        )
        if resp is None or resp.status_code != 200:
            return None
        try:
            payload = resp.json()
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("status") != 1:
            return None
        return payload

    def fetch_fund_intro_html(
        self,
        session: requests.Session,
        proxy: AntiBotProxy,
        fund_code: str,
        service: str,
        logger: Any = None,
    ) -> Optional[str]:
        """GET the per-fund detail page (server-rendered 基本信息) as
        decoded HTML.

        The page is UTF-8; ``resp.content`` is decoded explicitly
        (requests would guess the charset from the response headers).
        Returns None on request failure.
        """
        urls = service_urls(service)
        url = urls["fund_page"].format(p3=str(fund_code)[:3], code=fund_code)
        resp = proxy.get(
            session,
            url,
            headers=self.efunds_headers(service, fund_code),
            logger=logger,
            log_tag=f"[intro {fund_code}]",
        )
        if resp is None or resp.status_code != 200:
            return None
        return resp.content.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Fundlist parsing — trend rows + the listed-ETF universe
# ---------------------------------------------------------------------------


def _fundlist_tdate_to_iso(tdate: Any) -> str:
    """"20261113" → "2026-11-13"; anything else → ""."""
    s = str(tdate or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return ""


def parse_fundlist_nav_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """fundlist ``funds`` → one dict per non-money fund:
    ``{"code", "name", "nav", "nav_date", "day_pct"}``.

    * nav / day_pct — floats, day_pct in PERCENT ("-0.80" → -0.80);
      None when the entry carries no value.
    * nav_date — ISO 净值日期 (tdate), '' for missing.
    Money funds (货币型, 万份收益/7日年化 instead of NAV) are excluded —
    the huatai products-index convention. ``bfFunds`` (保证金 money
    funds) are not parsed at all.
    """
    rows: List[Dict[str, Any]] = []
    for fund in payload.get("funds") or []:
        code = str(fund.get("fundcode", "") or "").strip()
        if not code:
            continue
        if str(fund.get("fundType", "") or "").strip() == MONEY_FUND_TYPE:
            continue
        rows.append({
            "code": code,
            "name": str(fund.get("fundname", "") or "").strip(),
            "nav": parse_comma_number(fund.get("netvalue")),
            "nav_date": _fundlist_tdate_to_iso(fund.get("tdate")),
            "day_pct": parse_comma_number(fund.get("rzd")),
        })
    return rows


def parse_fundlist_etf_universe(
    payload: Dict[str, Any],
) -> List[Dict[str, str]]:
    """fundlist ``funds`` → the listed-ETF universe (the composition +
    intro scope), one dict per fund: ``{"code", "name", "setup_date"}``
    sorted by code, deduped.
    """
    by_code: Dict[str, Dict[str, str]] = {}
    for fund in payload.get("funds") or []:
        code = str(fund.get("fundcode", "") or "").strip()
        if not code or not is_listed_etf_code(code):
            continue
        by_code[code] = {
            "code": code,
            "name": str(fund.get("fundname", "") or "").strip(),
            "setup_date": str(fund.get("setupdate", "") or "").strip(),
        }
    return [by_code[c] for c in sorted(by_code)]


def parse_fundlist_intro_fallbacks(
    payload: Dict[str, Any],
) -> Dict[str, Dict[str, str]]:
    """fundlist ``funds`` → code → {name, full_name, fund_type, manager,
    setup_date, risk_level} — the intro CSV fallbacks for fields the
    per-fund page may not render (risk_level comes ONLY from here:
    ``properties.riskLevel``)."""
    out: Dict[str, Dict[str, str]] = {}
    for fund in payload.get("funds") or []:
        code = str(fund.get("fundcode", "") or "").strip()
        if not code:
            continue
        props = fund.get("properties") or {}
        out[code] = {
            "name": str(fund.get("fundname", "") or "").strip(),
            "full_name": str(fund.get("fundSourceName", "") or "").strip(),
            "fund_type": str(fund.get("fundType", "") or "").strip(),
            "manager": str(fund.get("managerName", "") or "").strip(),
            "setup_date": str(fund.get("setupdate", "") or "").strip(),
            "risk_level": str(props.get("riskLevel", "") or "").strip(),
        }
    return out


# ---------------------------------------------------------------------------
# Asset-info parsing (quarterly composition) — xcowch/front/efund/assetinfo
# ---------------------------------------------------------------------------


def _parse_stock_ratio(stock: Dict[str, Any]) -> Optional[float]:
    """ratio "0.0955" (fraction) → 9.55; formatratio "9.55" preferred."""
    pct = parse_comma_number(stock.get("formatratio"))
    if pct is not None:
        return pct
    frac = parse_comma_number(stock.get("ratio"))
    if frac is None:
        return None
    return frac * 100.0 if abs(frac) <= 1.0 else frac


def assetinfo_to_rows(
    payload: Dict[str, Any],
) -> Tuple[str, List[Dict[str, Any]]]:
    """assetinfo payload → (report_date, per-stock row dicts).

    Row schema (COMP_COLS minus the fund columns the payload lacks):
    ``rank, stock_code, stock_name, weight_pct, fair_value_yuan, shares,
    market`` — stock codes suffixed via mktcode (XSHG→.SS/XSHE→.SZ).
    Returns ("", []) when the response carries no report (pre-inception
    quarter / not yet published) or no holdings.
    """
    data = payload.get("data") or {}
    stocks: List[Dict[str, Any]] = []
    for block in data.get("investList") or []:
        block_stocks = block.get("investInfoData") or []
        if isinstance(block_stocks, list):
            stocks.extend(s for s in block_stocks if isinstance(s, dict))
    if not stocks:
        return "", []

    report_date = str(data.get("investReportDate")
                      or data.get("searchDate") or "").strip()
    try:
        datetime.strptime(report_date, "%Y-%m-%d")
    except ValueError:
        return "", []

    rows: List[Dict[str, Any]] = []
    for stock in stocks:
        symbol = str(stock.get("symbol", "") or "").strip()
        if not symbol:
            continue
        market_label, suffix = MKTCODE_MARKET_MAP.get(
            str(stock.get("mktcode", "") or "").strip(), ("", ""))
        norder = str(stock.get("norder", "") or "").strip()
        try:
            rank = int(norder) if norder else None
        except ValueError:
            rank = None
        rows.append({
            "rank": rank,
            "stock_code": f"{symbol}.{suffix}" if suffix else symbol,
            "stock_name": str(stock.get("name", "") or "").strip(),
            "weight_pct": _parse_stock_ratio(stock),
            "fair_value_yuan": parse_comma_number(stock.get("money")),
            "shares": parse_comma_number(stock.get("amount")),
            "market": market_label,
        })
    if not rows:
        return "", []
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"]))
    return report_date, rows


# ---------------------------------------------------------------------------
# Fund intro parsing — the baseinfo-table of the per-fund .shtml page
# ---------------------------------------------------------------------------

# baseinfo-table row label → intro CSV column (order = the huatai
# INTRO_FIELDS vocabulary plus the E Fund extras).
INTRO_ROW_FIELDS: Dict[str, str] = {
    "基金名称": "full_name",
    "基金简称": "name",
    "场内简称": "listed_name",
    "上市场所": "exchange",
    "基金类型": "fund_type",
    "成立日期": "setup_date",
    "基金管理人": "company",
    "基金经理": "manager",
    "基金托管人": "trustee",
    "基金规模": "scale",
    "投资范围": "invest_scope",
    "标的指数名称": "target_index",
    "投资比例": "invest_ratio",
    "投资目标": "invest_target",
    "业绩比较基准": "benchmark",
    "风险收益特征": "risk_feature",
}

_RE_BASEINFO_TABLE = re.compile(
    r'<table\s+class="baseinfo-table"\s+([^>]*)>(.*?)</table>', re.S)
_RE_TABLE_ATTR = re.compile(r'(data-level[12]desc)="([^"]*)"')
_RE_BASEINFO_ROW = re.compile(
    r'<td\s+class="baseinfo-table-left">\s*([^<]*?)\s*:?\s*</td>\s*'
    r'<td\s+class="baseinfo-table-right[^"]*"[^>]*>(.*?)</td>', re.S)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_WS = re.compile(r"\s+")


def _clean_cell(html_fragment: str) -> str:
    """Strip tags, collapse whitespace, drop empty anchor separators."""
    text = _RE_TAG.sub("", html_fragment)
    parts = [p.strip() for p in text.split(",")]
    return ",".join(p for p in parts if p)


def parse_intro_html(html_text: str) -> Dict[str, str]:
    """Parse the baseinfo-table of a fund detail page into an intro dict.

    Returns the INTRO_ROW_FIELDS columns (empty-string when the row is
    absent) plus ``category`` / ``sub_category`` from the table's
    data-level1desc/data-level2desc attributes. {} when the table is
    absent (bad page / fund not found).
    """
    m = _RE_BASEINFO_TABLE.search(html_text)
    if not m:
        return {}
    attrs_html, body = m.group(1), m.group(2)
    out: Dict[str, str] = {col: "" for col in INTRO_ROW_FIELDS.values()}
    for row_m in _RE_BASEINFO_ROW.finditer(body):
        # row labels carry a trailing colon — ASCII (基金名称:) or
        # full-width (标的指数名称：) depending on the row
        label = _RE_WS.sub("", row_m.group(1)).rstrip(":：")
        col = INTRO_ROW_FIELDS.get(label)
        if col:
            out[col] = _clean_cell(row_m.group(2))
    for attr_m in _RE_TABLE_ATTR.finditer(attrs_html):
        if attr_m.group(1) == "data-level1desc":
            out["category"] = attr_m.group(2).strip()
        elif attr_m.group(1) == "data-level2desc":
            out["sub_category"] = attr_m.group(2).strip()
    return out
