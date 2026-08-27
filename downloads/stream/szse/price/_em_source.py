"""SZSE sources C & D — East Money trends2 (via curl_cffi).

East Money's push2his/push2 hosts require TLS renegotiation that Python's
stdlib ssl rejects (``RemoteDisconnected``). ``curl_cffi`` (libcurl-backed,
``impersonate='chrome'``) handles the renegotiation reliably, so both EM
sources go through curl_cffi instead of the shared requests session.

  * Source C: ``push2his.eastmoney.com`` — 5-day 1-min bars (ndays=5, iscr=0).
    Mirrors akshare ``stock_zh_a_hist_min_em(period='1')``.
  * Source D: ``push2.eastmoney.com`` — 1-day pre-market bars (ndays=1, iscr=1).
    Mirrors akshare ``stock_zh_a_hist_pre_min_em``. A DIFFERENT host from C.

Both parse the same ``trends`` CSV list. The 09:30 open-snapshot point and
any pre-market points (D returns 09:15+) are dropped so 5-min windowing
matches source A (no spurious 09:30 bar).
"""
from __future__ import annotations

import time as _time
from datetime import datetime, time
from typing import List, Optional

from downloads._common import setup_logger

from ._akshare_source import MinuteSample

logger = setup_logger("stream_szse")

EM_PUSH2HIS_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
EM_PUSH2_URL = "https://push2.eastmoney.com/api/qt/stock/trends2/get"
EM_REFERER = "https://quote.eastmoney.com/"
EM_UT = "7eea3edcaed734bea9cbfc24409ed989"

# Regular-session minute bounds, matching the Sina (source A) convention:
# first morning bar 09:31, last 11:30; first afternoon 13:01, last 15:00.
# The 09:30 East Money open-snapshot point and any pre-market points (D
# returns 09:15+) are dropped so 5-min windowing lines up with source A
# (no spurious 09:30 bar).
_EM_MORNING_START = time(9, 31)
_EM_MORNING_END = time(11, 30)
_EM_AFTERNOON_START = time(13, 1)
_EM_AFTERNOON_END = time(15, 0)

_em_session = None


def _get_em_session():
    """Lazy-create a persistent curl_cffi.requests.Session for East Money.

    A Session reuses TLS connections (TLS session resumption) across requests,
    which reduces the chance of East Money closing the connection (curl error
    56). The ``impersonate='chrome'`` fingerprint is set once on the Session.
    """
    global _em_session
    if _em_session is None:
        try:
            from curl_cffi import requests as _cr
        except ImportError as e:
            raise ImportError(
                "curl_cffi is required for the East Money parallel sources "
                "(C/D) in stream_szse_price.py. Install with: pip install curl_cffi"
            ) from e
        _em_session = _cr.Session(impersonate="chrome")
    return _em_session


def _em_secid(bare_code: str) -> str:
    """East Money secid: market 0 for SZ/BJ, 1 for SH. All targets are SZSE."""
    market_code = 1 if bare_code.startswith("6") else 0
    return f"{market_code}.{bare_code}"


def _is_em_regular_session(t: time) -> bool:
    """True for minutes in the regular trading session (Sina convention)."""
    return (_EM_MORNING_START <= t <= _EM_MORNING_END) or \
           (_EM_AFTERNOON_START <= t <= _EM_AFTERNOON_END)


def _parse_em_trends(trends, bare_code: str, source_tag: str) -> Optional[List[MinuteSample]]:
    """Parse East Money trends2 ``trends`` CSV list into MinuteSamples.

    Each entry: "YYYY-MM-DD HH:MM,open,close,high,low,volume,amount,avg".
    Uses [0]=datetime, [2]=close (last price), [5]=volume. Drops the 09:30
    open-snapshot and pre-market points so 5-min windowing matches source A.
    """
    samples: List[MinuteSample] = []
    for item in trends:
        parts = item.split(",")
        if len(parts) < 6:
            continue
        time_str = parts[0]
        try:
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M")
        except ValueError:
            try:
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        if not _is_em_regular_session(dt.time()):
            continue
        try:
            price = float(parts[2])
        except (ValueError, TypeError):
            continue
        try:
            volume = float(parts[5]) if parts[5] != "" else 0.0
        except (ValueError, TypeError):
            volume = 0.0
        samples.append((dt, price, volume))
    if not samples:
        logger.warning("[%s %s] no regular-session samples parsed from %d trends",
                       source_tag, bare_code, len(trends))
    return samples or None


def _em_get_trends(url: str, params: dict, bare_code: str, source_tag: str,
                   retries: int = 2) -> Optional[list]:
    """GET an East Money trends2 endpoint via a persistent curl_cffi Session.

    Returns the parsed ``data.trends`` list, or None on failure. Uses a
    Session (TLS connection reuse) to reduce curl error 56 (connection closed
    abruptly). If the Session raises a connection error, it is recreated once
    in case the pooled connection went stale.
    """
    sess = _get_em_session()
    headers = {"Referer": EM_REFERER}
    data = None
    err = "no attempts"
    for attempt in range(retries + 1):
        try:
            r = sess.get(url, params=params, headers=headers, timeout=15)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
            # Recreate the Session once on connection errors (stale pool).
            global _em_session
            _em_session = None
            sess = _get_em_session()
            if attempt < retries:
                _time.sleep(1.5)
            continue
        if r.status_code != 200:
            err = f"HTTP {r.status_code}"
            if attempt < retries:
                _time.sleep(1.5)
            continue
        try:
            data = r.json()
        except ValueError as e:
            err = f"non-JSON: {e}"
            if attempt < retries:
                _time.sleep(1.5)
            continue
        break
    if data is None:
        logger.warning("[%s %s] trends2 fetch failed after retries: %s",
                       source_tag, bare_code, err)
        return None
    data_obj = data.get("data") if isinstance(data, dict) else None
    if not isinstance(data_obj, dict):
        logger.warning("[%s %s] missing 'data' object", source_tag, bare_code)
        return None
    trends = data_obj.get("trends")
    if not isinstance(trends, list) or not trends:
        logger.warning("[%s %s] missing or empty 'trends'", source_tag, bare_code)
        return None
    return trends


def fetch_em_push2his_minute(bare_code: str) -> Optional[List[MinuteSample]]:
    """Source C: East Money push2his trends2 (5-day 1-min bars, iscr=0).

    Mirrors akshare ``stock_zh_a_hist_min_em(period='1')`` — hits
    push2his.eastmoney.com with ndays=5. aggregate_5min keeps only the
    current trade_date's samples, so the 5-day span is fine.
    """
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ut": EM_UT,
        "ndays": "5",
        "iscr": "0",
        "secid": _em_secid(bare_code),
    }
    trends = _em_get_trends(EM_PUSH2HIS_URL, params, bare_code, "C")
    if trends is None:
        return None
    return _parse_em_trends(trends, bare_code, "C")


def fetch_em_push2_minute(bare_code: str) -> Optional[List[MinuteSample]]:
    """Source D: East Money push2 trends2 (1-day, iscr=1, with pre-market).

    Mirrors akshare ``stock_zh_a_hist_pre_min_em`` — hits push2.eastmoney.com
    (a DIFFERENT host from C) with ndays=1, iscr=1. Pre-market points
    (09:15-09:30) are dropped by _parse_em_trends.
    """
    params = {
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ndays": "1",
        "iscr": "1",
        "iscca": "0",
        "secid": _em_secid(bare_code),
    }
    trends = _em_get_trends(EM_PUSH2_URL, params, bare_code, "D")
    if trends is None:
        return None
    return _parse_em_trends(trends, bare_code, "D")
