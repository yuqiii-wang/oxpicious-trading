"""downloads.futures.sgx_a50.downloader — Eastmoney kline fetches for SGX A50.

Also owns the family's identity-row shaping for ``stats.futures_identity``:
SGX A50 bars live in the shared futures tables, so every fetched row needs
an identity twin (product CN, XIN9 underlying, contract-month parse). The
continuous series ``CN00Y`` carries the "0000" contract month and 0
days-to-expiry; contracts get their SGX last-trading-day approximation
(2nd-last business day of the contract month, computed as the 2nd-last
weekday).

Eastmoney's ``push2his``/``push2`` hosts need curl_cffi TLS impersonation
(stdlib ssl chokes on their TLS renegotiation — same finding as
``downloads/stream/szse/price/_em_source.py``), and they IP-block request
bursts with hard connection drops. Every request therefore goes through the
shared :class:`AntiBotProxy` built on this service's config row (sleep with
jitter between requests, host-block detection with a 1800s cooldown), over a
persistent ``curl_cffi`` session.

The fetch layer is the :class:`SgxA50Downloader` class — the former
module-level proxy/session singletons are per-instance attributes
(``self.proxy`` / ``self._session``) and the functions that used them are
its methods, while the pure parsers and the identity-row shaper stay
module-level functions.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

from downloads._common import (
    AntiBotProxy,
    build_headers_with_referer,
    retry_policy_for,
    setup_logger,
)

from downloads.futures.sgx_a50.config import (
    A50_NAME_PREFIX,
    CLIST_PARAMS,
    CLIST_URL,
    CONTINUOUS_CODE,
    KLINE_COMMON_PARAMS,
    KLINE_URL,
    MARKET_FUTURES,
    MONTH_LETTERS,
    REFERER_URL,
)

logger: logging.Logger = setup_logger("sgx_a50")

SERVICE: str = "downloads.futures.sgx_a50.daily"

# Retry policy — resolved once at import from the service's
# external_request_config JSON row (max_retries, retry_backoff_sec); the
# code fallbacks (2 retries, 20s backoff) only apply if the row omits them.
_MAX_RETRIES_JSON, _RETRY_BACKOFF_JSON = retry_policy_for(SERVICE)
MAX_RETRIES: int = _MAX_RETRIES_JSON if _MAX_RETRIES_JSON is not None else 2
RETRY_BACKOFF_SEC: float = _RETRY_BACKOFF_JSON if _RETRY_BACKOFF_JSON is not None else 20.0


class A50Instrument(NamedTuple):
    """One row of the enumerated A50 chain."""
    code: str
    name: str
    is_continuous: bool


def _parse_float(raw: Optional[str]) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_volume(raw: Optional[str]) -> Optional[int]:
    value = _parse_float(raw)
    return int(value) if value is not None else None


class SgxA50Downloader:
    """Eastmoney kline fetch layer of the family (proxy/session-scoped
    class).

    Every request goes through the shared :class:`AntiBotProxy` over a
    persistent ``curl_cffi`` session — the former module-level singletons
    are now instance attributes (``self.proxy`` / ``self._session``) built
    in ``__init__``, and the functions that used them are methods with the
    same names and sequencing; the pure parsers above and the identity-row
    shaper below stay module-level functions."""

    def __init__(self) -> None:
        # Built on the service row: base sleep + jitter, timeouts, host tracking
        # (1800s block cooldown — this host demonstrably IP-blocks bursts).
        self.proxy = AntiBotProxy(service=SERVICE)
        self._session = None

    def _get_session(self):
        """Lazy-create the persistent curl_cffi session (TLS session resumption
        across requests lowers the chance of Eastmoney closing the connection).
        """
        if self._session is None:
            try:
                from curl_cffi import requests as curl_requests
            except ImportError as exc:
                raise ImportError(
                    "curl_cffi is required for the Eastmoney push2his kline API "
                    "(stdlib ssl fails its TLS renegotiation). "
                    "Install with: pip install curl_cffi"
                ) from exc
            self._session = curl_requests.Session(impersonate="chrome")
        return self._session

    def _request_json(self, url: str, params: Dict[str, str],
                      sleep_sec: Optional[float]) -> Optional[dict]:
        """One proxied GET returning parsed JSON, with the service retry policy.

        Returns None after the final failure (connection drop, non-JSON body,
        or the proxy's suppressed-4xx None).
        """
        headers = build_headers_with_referer(REFERER_URL)
        session = self._get_session()
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.proxy.get(session, url, params=params, headers=headers,
                                      sleep_sec=sleep_sec,
                                      request_kwargs={"impersonate": "chrome"},
                                      logger=logger)
                if resp is not None and resp.status_code == 200:
                    return resp.json()
                logger.warning("  %s <- HTTP %s (attempt %d/%d)",
                               url.split("?")[0],
                               resp.status_code if resp is not None else "suppressed",
                               attempt, MAX_RETRIES)
            except Exception as exc:
                logger.warning("  %s (attempt %d/%d): %s",
                               url.split("?")[0], attempt, MAX_RETRIES, exc)
            if attempt < MAX_RETRIES:
                self.proxy.sleep()  # host-block-aware cooldown instead of a bare sleep
        return None

    def fetch_daily_rows(self, code: str, beg: Optional[date] = None,
                         sleep_sec: Optional[float] = None) -> List[Dict[str, object]]:
        """Fetch daily bars for one code from Eastmoney.

        Args:
            code: A50 chain code — the continuous series or one contract
                (``CN00Y``, ``CN26V``, ...).
            beg: Optional left bound (YYYY-MM-DD). None = full history. The
                continuous series MUST be re-fetched with beg=None on every run
                (it re-splices without back-adjustment); contracts use
                beg = last DB date + 1 day for incremental tails.
            sleep_sec: Explicit pacing override for this request; None = the
                service row's base sleep.

        Returns:
            Raw bar rows (date as ``datetime.date``, prices as float, missing
            fields as None); empty list when the API has no bars in range
            (unknown code, no data since beg). Callers map them onto
            ``stats.futures_basic_stats`` and the identity twin.
        """
        params = dict(KLINE_COMMON_PARAMS)
        params["secid"] = f"{MARKET_FUTURES}.{code}"
        if beg is not None:
            params["beg"] = beg.strftime("%Y%m%d")

        payload = self._request_json(KLINE_URL, params, sleep_sec)
        data = (payload or {}).get("data") or {}
        klines: List[str] = data.get("klines") or []
        if not klines:
            return []

        rows: List[Dict[str, object]] = []
        for line in klines:
            f: List[Optional[str]] = line.split(",") + [None] * 10
            rows.append({
                "date": datetime.strptime(str(f[0]), "%Y-%m-%d").date(),
                "code": code,
                "open": _parse_float(f[1]),
                "high": _parse_float(f[3]),
                "low": _parse_float(f[4]),
                "close": _parse_float(f[2]),   # Eastmoney order: open, CLOSE, high, low
                "volume": _parse_volume(f[5]),
                "amount": _parse_float(f[6]),  # EM reports 0 for global futures;
                "amplitude": _parse_float(f[7]),  # kept for the CFFEX-format CSV,
                "change_pct": _parse_float(f[8]),  # stripped before the DB write
                "change": _parse_float(f[9]),
            })
        logger.info("  %s: %d bars (%s .. %s)", code, len(rows),
                    rows[0]["date"], rows[-1]["date"])
        return rows

    def enumerate_a50_codes(self, sleep_sec: Optional[float] = None) -> List[A50Instrument]:
        """Enumerate the A50 chain on the SGX global-futures board (market 104).

        Returns continuous series + listed contracts, sorted continuous-first
        then by code. Empty list when the enumeration request fails — callers
        fall back to the continuous code plus codes already in the DB.
        """
        payload = self._request_json(CLIST_URL, dict(CLIST_PARAMS), sleep_sec)
        data = (payload or {}).get("data") or {}
        diff = data.get("diff") or []
        rows: List[Dict] = list(diff.values()) if isinstance(diff, dict) else diff

        instruments: List[A50Instrument] = []
        for row in rows:
            name = str(row.get("f14", ""))
            if not name.startswith(A50_NAME_PREFIX):
                continue
            code = str(row.get("f12", ""))
            if not code:
                continue
            instruments.append(A50Instrument(
                code=code, name=name, is_continuous="连续" in name,
            ))

        instruments.sort(key=lambda inst: (not inst.is_continuous, inst.code))
        logger.info("Enumerated %d A50 instruments (%d continuous)",
                    len(instruments),
                    sum(1 for inst in instruments if inst.is_continuous))
        return instruments


def next_day(d: date) -> date:
    """The day after *d* — the incremental ``beg`` for contract tails."""
    return d + timedelta(days=1)


# ---------------------------------------------------------------------------
# Identity rows — the family's twin rows for stats.futures_identity
# ---------------------------------------------------------------------------

_SGX_NAME_CONTRACT: str = "富时中国A50指数期货"
_SGX_NAME_CONTINUOUS: str = "富时中国A50期指当月连续"
_SGX_UNDERLYING_CODE: str = "XIN9"
_SGX_UNDERLYING_NAME: str = "富时中国A50指数"

CONTRACT_TYPE_INDEX: int = 0  # static.label_dim 'contract_type' — same code as CFFEX index futures


def _second_last_weekday(year: int, month: int) -> date:
    """The 2nd-last weekday of a month — SGX A50's 2nd-last-business-day
    last trading day, approximated without a holiday calendar."""
    d = (date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)) - timedelta(days=1)
    weekdays: List[date] = []
    while len(weekdays) < 2:
        if d.weekday() < 5:
            weekdays.append(d)
        d -= timedelta(days=1)
    return weekdays[1]


def identity_rows_for(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """The deduplicated ``stats.futures_identity`` twins of fetched rows.

    Continuous code ``CN00Y`` → contract month "0000" / "0000-00" and 0
    days-to-expiry; contracts ``CN<YY><letter>`` → "YYMM" with days-to-expiry
    measured to the 2nd-last weekday of the contract month.
    """
    seen: Set[Tuple[str, date]] = set()
    out: List[Dict[str, object]] = []
    for row in rows:
        key = (str(row["code"]), row["date"])  # type: ignore[arg-type]
        if key in seen:
            continue
        seen.add(key)
        code, d = key
        if code == CONTINUOUS_CODE:
            month_yymm, year_month, expiry_days = "0000", "0000-00", 0
            name = _SGX_NAME_CONTINUOUS
        else:
            yy, letter = code[2:4], code[4]
            mm = MONTH_LETTERS.index(letter) + 1
            month_yymm = f"{yy}{mm:02d}"
            year_month = f"20{yy}-{mm:02d}"
            expiry_days = max(0, (_second_last_weekday(2000 + int(yy), mm) - d).days)
            name = _SGX_NAME_CONTRACT
        out.append({
            "date": d,
            "code": code,
            "product_code": "CN",
            "contract_month": month_yymm,
            "contract_year_month": year_month,
            "contract_type": CONTRACT_TYPE_INDEX,
            "name": name,
            "underlying_code": _SGX_UNDERLYING_CODE,
            "underlying_name": _SGX_UNDERLYING_NAME,
            "days_to_expiry": expiry_days,
        })
    return out


# Re-exported so callers can reference the default continuous code and the
# DB-facing write scope without importing config directly.
CONTINUOUS: str = CONTINUOUS_CODE
