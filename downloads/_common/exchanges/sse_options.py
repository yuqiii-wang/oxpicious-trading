"""SSE ETF-options quote API — the backend behind /assortment/options/price/.

The 行情 tab of https://www.sse.com.cn/assortment/options/price/ (JS:
``search_productStockOptions_2021.js``) is powered by two gateways:

1. ``yunhq.sse.com.cn:32042`` — live quotes on the same JSONP gateway the
   equity/fund/index list endpoints use (see ``sse.py``):
     * ``/v1/sho/list/exchange/underlyingstock`` → the 5 option underlyings
       (510050, 510300, 510500, 588000, 588080)
     * ``/v1/sho/list/exchange/stockexpire`` → per-underlying live expiry
       months (``stockid,expiremonth`` with expiremonth = YYYYMM int)
     * ``/v1/sho/list/tstyle/{stockid}_{MM}`` → per-contract quotes for ONE
       (underlying, expiry-month) pair; the page refreshes it every 60 s.
       Beyond the 5 fields the page selects, ``open/high/low/volume/amount``
       are also supported (``position``/OI returns null — unavailable).
       ``volume`` (张) and ``amount`` (元) are DAY-CUMULATIVE: intraday bars
       must be derived by subtracting consecutive samples.
       NOTE the path month is 2-digit MM (``510050_09``), so it must always
       be derived from the CURRENT stockexpire list (``03`` currently means
       202703); after 15:00 the snapshot is the EOD state (day OHLC + day
       totals), which is what the daily EOD capture stores.
2. ``query.sse.com.cn/commonQuery.do`` — the 当日合约 listing (sqlId
   ``SSE_ZQPZ_YSP_GGQQZSXT_XXPL_DRHY_SEARCH_L``, same call the preinfo
   daily downloader makes): maps the tstyle ``contractid`` trading code
   (e.g. ``510050C2609M02650``) to the numeric 合约编码 security id
   (e.g. ``10011255``) used as ``stats.options_identity.contract_code``.

Shared by ``downloads.stream.sse.price`` (options asset) and the SSE options
daily build so the endpoint knowledge lives in one place.
"""
from __future__ import annotations

import re
import time as _time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from downloads._common.net import (
    DEFAULT_TIMEOUT,
    HostStatusTracker,
    setup_logger,
)
from downloads._common.exchanges.sse import (
    JSONP_CALLBACK,
    PAGE_SIZE,
    SSE_HEADERS,
    _num,
    _parse_jsonp,
)

logger = setup_logger("sse_options")

YUNHQ_BASE = "https://yunhq.sse.com.cn:32042"
SSE_OPTIONS_UNDERLYING_URL = f"{YUNHQ_BASE}/v1/sho/list/exchange/underlyingstock"
SSE_OPTIONS_EXPIRE_URL = f"{YUNHQ_BASE}/v1/sho/list/exchange/stockexpire"
SSE_OPTIONS_TSTYLE_URL = f"{YUNHQ_BASE}/v1/sho/list/tstyle"

# Field order of every tstyle row (see module docstring for units/semantics).
TSTYLE_SELECT_FIELDS = "contractid,last,chg_rate,presetpx,exepx,open,high,low,volume,amount"

# Raw-snapshot CSV archive schema (temps/sse_options_intraday/) — cumulative
# volume/amount stored raw, matching the sse_intraday convention.
OPTIONS_CSV_COLUMNS = [
    "update_time", "code", "name", "underlying", "month",
    "open", "high", "low", "last", "prev_settle", "chg_rate", "strike",
    "volume", "amount",
]

# 当日合约 listing (query.sse.com.cn) — contractid → numeric security id map.
SSE_OPTIONS_CONTRACT_URL = "https://query.sse.com.cn/commonQuery.do"
SSE_OPTIONS_CONTRACT_REFERER = "https://www.sse.com.cn/assortment/options/disclo/preinfo/"
SSE_OPTIONS_CONTRACT_SQL_ID = "SSE_ZQPZ_YSP_GGQQZSXT_XXPL_DRHY_SEARCH_L"

# Inter-request sleep while sweeping the ~20 code-month tstyle endpoints in
# one poll cycle (options pages tolerate this; the equity pagination uses a
# far larger 3 s sleep because it walks thousands of rows).
TSTYLE_INTER_REQUEST_SEC = 0.3

_RE_CONTRACTID = re.compile(
    r"^(?P<underlying>\d{6})(?P<cp>[CP])(?P<yymm>\d{4})(?P<series>[MA])(?P<strike>\d{4,5})$"
)


def parse_contractid(contractid: str) -> Optional[Dict[str, object]]:
    """Parse a tstyle contractid into structured components.

    ``510050C2609M02650`` → underlying 510050, CALL, expiry 2026-09,
    series M (unadjusted; A = adjusted, mirrors SZSE has_a_suffix),
    strike 2650 厘 = 2.650 yuan.
    """
    m = _RE_CONTRACTID.match(contractid.strip())
    if not m:
        return None
    strike_li = int(m.group("strike"))
    series = m.group("series")
    return {
        "underlying": m.group("underlying"),
        "option_type": "CALL" if m.group("cp") == "C" else "PUT",
        "yymm": m.group("yymm"),
        "has_a_suffix": series == "A",
        "strike_str": m.group("strike") + ("A" if series == "A" else ""),
        "strike_price": strike_li,  # 厘 (1/1000 yuan), same unit as options_strike
    }


def _update_datetime(payload: Dict[str, Any]) -> Optional[datetime]:
    """Build the snapshot update datetime from the API's date+time fields.

    Same convention as the equity list endpoint (``_fetch`` layer): the
    server's 更新时间, not the local clock. Returns None if unparseable.
    """
    date_raw = payload.get("date")
    if not date_raw:
        return None
    try:
        date_str = str(date_raw)
        time_str = str(payload.get("time") or "").zfill(6)
        return datetime.strptime(
            f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]} "
            f"{time_str[:2]}:{time_str[2:4]}:{time_str[4:6]}",
            "%Y-%m-%d %H:%M:%S",
        )
    except (ValueError, IndexError):
        return None


def _get_jsonp(
    session: requests.Session,
    url: str,
    params: Dict[str, str],
    referer: Optional[str] = None,
    host_tracker: Optional[HostStatusTracker] = None,
) -> Optional[Dict[str, Any]]:
    """GET one yunhq JSONP endpoint and return the parsed payload."""
    headers = dict(SSE_HEADERS)
    if referer:
        headers["Referer"] = referer
    try:
        resp = session.get(url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as e:
        logger.warning("SSE options request failed (%s): %s", url, e)
        return None
    if resp.status_code != 200:
        if host_tracker is not None:
            host_tracker.record_error(url, resp.status_code, resp.reason)
        logger.warning("SSE options HTTP %s on %s", resp.status_code, url)
        return None
    try:
        return _parse_jsonp(resp.text)
    except ValueError as e:
        logger.warning("SSE options JSONP parse failed (%s): %s", url, e)
        return None


def fetch_underlyings(
    session: requests.Session,
    host_tracker: Optional[HostStatusTracker] = None,
) -> List[str]:
    """Fetch the option underlying list (e.g. ['510050', '510300', …])."""
    payload = _get_jsonp(
        session, SSE_OPTIONS_UNDERLYING_URL,
        {"callback": JSONP_CALLBACK, "select": "stockid"},
        host_tracker=host_tracker,
    )
    if not payload:
        return []
    return [str(row[0]).strip() for row in (payload.get("list") or []) if row]


def fetch_expire_months(
    session: requests.Session,
    host_tracker: Optional[HostStatusTracker] = None,
) -> Dict[str, List[str]]:
    """Fetch live expiry months per underlying.

    Returns {stockid: [yymm, …]} with yymm like '2609'. The tstyle URL month
    is the last 2 chars ('09') — always re-derive from this live list.
    """
    payload = _get_jsonp(
        session, SSE_OPTIONS_EXPIRE_URL,
        {"callback": JSONP_CALLBACK, "select": "stockid,expiremonth"},
        host_tracker=host_tracker,
    )
    months: Dict[str, List[str]] = {}
    if not payload:
        return months
    for row in payload.get("list") or []:
        if not row or len(row) < 2 or row[1] in (None, ""):
            continue
        stockid = str(row[0]).strip()
        yymm = str(row[1])[2:]  # YYYYMM → YYMM
        months.setdefault(stockid, [])
        if yymm not in months[stockid]:
            months[stockid].append(yymm)
    return months


def _parse_tstyle_row(row: list) -> Optional[Dict[str, object]]:
    """Map one tstyle row to a record dict (order = TSTYLE_SELECT_FIELDS)."""
    if not row:
        return None
    contractid = str(row[0]).strip() if row[0] is not None else ""
    if not contractid:
        return None
    parsed = parse_contractid(contractid)
    if parsed is None:
        return None
    return {
        "code": contractid,
        "underlying": parsed["underlying"],
        "option_type": parsed["option_type"],
        "yymm": parsed["yymm"],
        "strike": parsed["strike_price"],
        "strike_str": parsed["strike_str"],
        "has_a_suffix": parsed["has_a_suffix"],
        "last": _num(row[1]) if len(row) > 1 else None,
        "chg_rate": _num(row[2]) if len(row) > 2 else None,
        "prev_settle": _num(row[3]) if len(row) > 3 else None,
        "exepx": _num(row[4]) if len(row) > 4 else None,
        "open": _num(row[5]) if len(row) > 5 else None,
        "high": _num(row[6]) if len(row) > 6 else None,
        "low": _num(row[7]) if len(row) > 7 else None,
        # day-cumulative 张 / 元 — subtract across samples for bars
        "volume": _num(row[8]) if len(row) > 8 else None,
        "amount": _num(row[9]) if len(row) > 9 else None,
    }


def fetch_options_snapshot(
    session: requests.Session,
    months: Dict[str, List[str]],
    host_tracker: Optional[HostStatusTracker] = None,
    inter_request_sec: float = TSTYLE_INTER_REQUEST_SEC,
) -> Tuple[Optional[datetime], Dict[str, dict]]:
    """Sweep every (underlying, expiry-month) tstyle endpoint into one snapshot.

    Returns (update_dt, {contractid: record}) — the same contract as the
    equity ``fetch_snapshot``. ``update_dt`` is the latest server update
    time seen across pages; a failed page logs a warning and is skipped
    (partial snapshot, mirroring the equity pagination behaviour).
    """
    snapshot: Dict[str, dict] = {}
    update_dt: Optional[datetime] = None
    n_pages = sum(len(v) for v in months.values())
    if n_pages == 0:
        return None, {}

    referer = "https://www.sse.com.cn/assortment/options/price/"
    done = 0
    for stockid, yymms in sorted(months.items()):
        for yymm in sorted(yymms):
            payload = _get_jsonp(
                session,
                f"{SSE_OPTIONS_TSTYLE_URL}/{stockid}_{yymm[2:]}",
                {
                    "callback": JSONP_CALLBACK,
                    "select": TSTYLE_SELECT_FIELDS,
                    "order": "contractid,ase",
                },
                referer=referer,
                host_tracker=host_tracker,
            )
            done += 1
            if not payload:
                continue
            dt = _update_datetime(payload)
            if dt is not None and (update_dt is None or dt > update_dt):
                update_dt = dt
            for row in payload.get("list") or []:
                rec = _parse_tstyle_row(row)
                if rec is not None:
                    snapshot[rec["code"]] = rec
            if done < n_pages and inter_request_sec > 0:
                _time.sleep(inter_request_sec)

    return update_dt, snapshot


def fetch_day_contract_map(
    session: requests.Session,
    page_size: int = PAGE_SIZE,
    max_attempts: int = 2,
) -> Dict[str, Tuple[str, str]]:
    """Fetch the current trading day's contract listing from query.sse.com.cn.

    Returns {contractid: (security_id, contract_name)} — e.g.
    ``{'510050C2609M02650': ('10011255', '50ETF购9月2650')}``. The numeric
    security id is the ``contract_code`` used across the stats.options_*
    tables. Paginated POST JSONP, same call as the preinfo daily downloader.

    Without this map bars cannot resolve numeric contract codes, so the
    whole pagination is retried once on failure/empty result (query.sse.com.cn
    occasionally read-times-out under the anti-bot throttle).
    """
    headers = dict(SSE_HEADERS)
    headers["Referer"] = SSE_OPTIONS_CONTRACT_REFERER
    headers["Accept"] = "*/*"

    for attempt in range(1, max_attempts + 1):
        mapping = _fetch_contract_map_pages(session, headers, page_size)
        if mapping:
            return mapping
        if attempt < max_attempts:
            logger.warning(
                "SSE options contract map attempt %d/%d empty — retrying in 3s",
                attempt, max_attempts,
            )
            _time.sleep(3.0)
    return {}


def _fetch_contract_map_pages(
    session: requests.Session,
    headers: Dict[str, str],
    page_size: int,
) -> Dict[str, Tuple[str, str]]:
    """One full paginated pass over the 当日合约 listing."""
    mapping: Dict[str, Tuple[str, str]] = {}
    page_no = 1
    while True:
        data = {
            "jsonCallBack": "jsonpCallback",
            "isPagination": "true",
            "expireDate": "",
            "securityId": "",
            "sqlId": SSE_OPTIONS_CONTRACT_SQL_ID,
            "pageHelp.pageSize": page_size,
            "pageHelp.pageNo": page_no,
            "pageHelp.beginPage": page_no,
            "pageHelp.endPage": page_no,
            "pageHelp.cacheSize": 1,
        }
        try:
            resp = session.post(
                SSE_OPTIONS_CONTRACT_URL, data=data, headers=headers,
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as e:
            logger.warning("SSE options contract map page %d failed: %s", page_no, e)
            break
        if resp.status_code != 200:
            logger.warning(
                "SSE options contract map page %d HTTP %s", page_no, resp.status_code
            )
            break
        try:
            payload = _parse_jsonp(resp.text)
        except ValueError as e:
            logger.warning("SSE options contract map JSONP parse failed: %s", e)
            break

        results = payload.get("result") or []
        if not results:
            break
        for item in results:
            contract_id = (item.get("CONTRACT_ID") or "").strip()
            security_id = (item.get("SECURITY_ID") or "").strip()
            symbol = (item.get("CONTRACT_SYMBOL") or "").strip()
            if contract_id and security_id:
                mapping[contract_id] = (security_id, symbol)

        total_pages = int((payload.get("pageHelp") or {}).get("pageCount", 1) or 1)
        if page_no >= total_pages:
            break
        page_no += 1
        _time.sleep(0.2)

    return mapping


__all__ = [
    "OPTIONS_CSV_COLUMNS",
    "SSE_OPTIONS_EXPIRE_URL",
    "SSE_OPTIONS_TSTYLE_URL",
    "SSE_OPTIONS_UNDERLYING_URL",
    "TSTYLE_SELECT_FIELDS",
    "fetch_day_contract_map",
    "fetch_expire_months",
    "fetch_options_snapshot",
    "fetch_underlyings",
    "parse_contractid",
]
