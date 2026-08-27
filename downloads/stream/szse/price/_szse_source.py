"""SZSE source B — SZSE ``/api/market/ssjjhq/getTimeData`` (fallback + index).

Fallback source for SZSE stock intraday streaming (used when AkShare/EM
sources fail or hit their circuit-breaker). Also the sole source for SZSE
index intraday data.

The SZSE getTimeData API returns a DIFFERENT ``picupdata`` layout for indices
vs stocks:
  stock: [time, open, close(now), delta, deltaPct, volume, amount]  (7 fields)
  index: [time, price(now),   delta, deltaPct, volume, amount]      (6 fields)
Index picupdata has NO separate open field — field[1] is the current price.
The index_intraday_5min table also has NO volume/trading_shares column, so
volume is discarded during aggregation.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

import requests

from downloads._common import (
    HostStatusTracker,
    build_headers_with_referer,
    safe_get,
    setup_logger,
)

from ._akshare_source import MinuteSample

logger = setup_logger("stream_szse")

# SZSE trend-page minute API (the JSON backing the "分时" chart on
# https://www.szse.cn/market/trend/index.html?code=<code>).
SZSE_TIMEDATA_URL = "https://www.szse.cn/api/market/ssjjhq/getTimeData"
SZSE_REFERER = "https://www.szse.cn/market/trend/index.html"


def fetch_szse_minute(
    session: requests.Session,
    bare_code: str,
    host_tracker: HostStatusTracker,
) -> Optional[List[MinuteSample]]:
    """Fetch intraday minute samples for one SZSE stock from the SZSE trend API.

    Returns a list of (datetime, price, volume), or None on failure / block.

    The SZSE ssjjhq endpoint returns JSON describing the "分时" chart for one
    code. Response shape is handled defensively: a ``data`` list (or numeric-
    keyed dict) of points, each exposing a time/price/volume field under one
    of several common key names.
    """
    headers = build_headers_with_referer(SZSE_REFERER)
    params = {"marketId": "1", "code": bare_code}
    resp = safe_get(
        session,
        SZSE_TIMEDATA_URL,
        params=params,
        headers=headers,
        host_tracker=host_tracker,
        logger=logger,
        log_tag=f"[szse {bare_code}] ",
    )
    if resp is None:
        return None
    try:
        payload = resp.json()
    except ValueError:
        logger.warning("[szse %s] non-JSON response", bare_code)
        return None

    # SZSE API uses code="0" for success, "-1" for error.
    if isinstance(payload, dict):
        api_code = payload.get("code")
        if str(api_code) != "0":
            logger.warning("[szse %s] API code=%s msg=%s",
                           bare_code, api_code, payload.get("message"))
            return None
    return _parse_szse_picupdata(payload, bare_code)


def _parse_szse_picupdata(payload, bare_code: str) -> Optional[List[MinuteSample]]:
    """Parse the SZSE getTimeData ``data.picupdata`` array into minute samples.

    Each entry: ["09:30", "10.92", "10.92", "-0.06", "-0.55", 4045, 4417140.0]
    Fields: time, open, close(now), delta, deltaPct, volume, amount
    """
    data_obj = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data_obj, dict):
        logger.warning("[szse %s] missing 'data' object in response", bare_code)
        return None

    picupdata = data_obj.get("picupdata")
    if not isinstance(picupdata, list) or not picupdata:
        logger.warning("[szse %s] missing or empty 'picupdata'", bare_code)
        return None

    # Use marketTime (remote source logged date) for the date, not local clock.
    market_time = data_obj.get("marketTime", "")
    try:
        trade_date = datetime.strptime(market_time[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.warning("[szse %s] marketTime missing/unparseable (%r); "
                       "falling back to local date", bare_code, market_time)
        trade_date = datetime.now().date()

    samples: List[MinuteSample] = []
    for pt in picupdata:
        if not isinstance(pt, (list, tuple)) or len(pt) < 6:
            continue
        try:
            time_str = str(pt[0]).strip()       # "09:30"
            price = float(pt[2])                 # close/now price
            volume = float(pt[5])                # per-minute volume
        except (ValueError, TypeError, IndexError):
            continue
        try:
            t = datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            continue
        dt = datetime.combine(trade_date, t)
        samples.append((dt, price, volume))

    if not samples:
        logger.warning("[szse %s] no samples parsed from %d picupdata entries",
                       bare_code, len(picupdata))
    return samples or None


def fetch_szse_index_minute(
    session: requests.Session,
    bare_code: str,
    host_tracker: HostStatusTracker,
) -> Optional[List[MinuteSample]]:
    """Fetch intraday minute samples for one SZSE index via the SZSE trend API.

    Same endpoint as ``fetch_szse_minute`` but the picupdata layout for
    indices has 6 fields (no separate open). Returns (datetime, price, 0.0)
    samples — volume is set to 0 because index_intraday_5min has no volume
    column.
    """
    headers = build_headers_with_referer(SZSE_REFERER)
    params = {"marketId": "1", "code": bare_code}
    resp = safe_get(
        session,
        SZSE_TIMEDATA_URL,
        params=params,
        headers=headers,
        host_tracker=host_tracker,
        logger=logger,
        log_tag=f"[szse-idx {bare_code}] ",
    )
    if resp is None:
        return None
    try:
        payload = resp.json()
    except ValueError:
        logger.warning("[szse-idx %s] non-JSON response", bare_code)
        return None
    if isinstance(payload, dict):
        api_code = payload.get("code")
        if str(api_code) != "0":
            logger.warning("[szse-idx %s] API code=%s msg=%s",
                           bare_code, api_code, payload.get("message"))
            return None
    return _parse_szse_index_picupdata(payload, bare_code)


def _parse_szse_index_picupdata(payload, bare_code: str) -> Optional[List[MinuteSample]]:
    """Parse SZSE getTimeData ``data.picupdata`` for INDICES (6-field layout).

    Each entry: ["09:30", "13497.10", "-81.83", "-0.60", 6940999.0, 11200039455.88]
    Fields: time, price(now), delta, deltaPct, volume, amount
    """
    data_obj = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data_obj, dict):
        logger.warning("[szse-idx %s] missing 'data' object in response", bare_code)
        return None
    picupdata = data_obj.get("picupdata")
    if not isinstance(picupdata, list) or not picupdata:
        logger.warning("[szse-idx %s] missing or empty 'picupdata'", bare_code)
        return None

    # Use marketTime (remote source logged date) for the date, not local clock.
    market_time = data_obj.get("marketTime", "")
    try:
        trade_date = datetime.strptime(market_time[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        logger.warning("[szse-idx %s] marketTime missing/unparseable (%r); "
                       "falling back to local date", bare_code, market_time)
        trade_date = datetime.now().date()

    samples: List[MinuteSample] = []
    for pt in picupdata:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        try:
            time_str = str(pt[0]).strip()       # "09:30"
            price = float(pt[1])                 # current price (index: field 1)
        except (ValueError, TypeError, IndexError):
            continue
        try:
            t = datetime.strptime(time_str, "%H:%M").time()
        except ValueError:
            continue
        dt = datetime.combine(trade_date, t)
        samples.append((dt, price, 0.0))  # volume=0 (index table has no volume col)

    if not samples:
        logger.warning("[szse-idx %s] no samples parsed from %d picupdata entries",
                       bare_code, len(picupdata))
    return samples or None
