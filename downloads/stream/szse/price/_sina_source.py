"""SZSE source A — Sina 1-minute bars (jsonp v2 kline API, direct fetch).

Primary source for SZSE stock intraday streaming. Fetches today's 1-minute
bars straight from Sina Finance's minute kline endpoint over the shared
AntiBotProxy. Returns a list of ``(datetime, close, volume)`` samples.

This replaces the original AkShare wrapper (``ak.stock_zh_a_minute``):
akshare fired 4-5 unaudited Sina requests per fetch (the minute kline
twice — a bare-UA attempt plus a callback-variant retry — then the FULL
daily history, the stock-structure HTML page and qfq factors for an
adjustment the streamer never used) and decoded through py_mini_racer's
V8, whose crash risk forced the lazy import and the trip-on-first-failure
breaker. The direct call is one GET per attempt through the proxy, with
the real HTTP status on every audit row (host quotes.sina.cn decodes to
ext_http_source ``sina``); parse-after-200 failures land in the service
log only, like sources C/D.

Class shape: the former module-level singletons (the ``_sina_proxy``
AntiBotProxy built at import, the lazily-created ``_session``) are instance
state of :class:`SinaSource`, and ``SINA_SOURCE = SinaSource()`` replaces
them as the module singleton — created at import exactly when the proxy was
created before, so the lifecycle (proxy at import, session lazy on first
fetch / pre-warm) is unchanged. ``_parse_sina_kline`` stays module-level
(pure parser).
"""
from __future__ import annotations

import json
import time as _time
from dataclasses import replace
from datetime import datetime
from typing import List, Optional, Tuple

import requests

from downloads._common import (
    AntiBotProxy,
    antibot_config_for,
    retry_policy_for,
    service_urls,
    setup_logger,
)

logger = setup_logger("stream_szse")

SERVICE = "downloads.stream.szse.price"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)

# The Sina minute kline endpoint (jsonp v2). The path segment between
# jsonp_v2.php and the method is the JS callback name — arbitrary ("var _="
# historically, via akshare); the query carries the real request.
SINA_MINUTE_URL = URLS["sina_minute_url"]

# A normalized 1-minute sample from any source.
#   dt     : datetime of the bar (e.g. 2025-07-23 09:31:00)
#   price  : last price for that minute
#   volume : per-minute volume (shares)
MinuteSample = Tuple[datetime, float, float]

# Source A retry policy — resolved once at import from the service's
# external_request_config JSON row (request_type "source_a_sina"); the old
# inline values (1 retry, 1.0s between attempts) are the fallback.
_SINA_MAX_RETRIES_JSON, _SINA_RETRY_BACKOFF_JSON = retry_policy_for(
    SERVICE, "source_a_sina",
)
SINA_MAX_RETRIES: int = (
    _SINA_MAX_RETRIES_JSON if _SINA_MAX_RETRIES_JSON is not None else 1
)
SINA_RETRY_BACKOFF_SEC: float = (
    _SINA_RETRY_BACKOFF_JSON if _SINA_RETRY_BACKOFF_JSON is not None else 1.0
)


class SinaSource:
    """Source A (Sina 1-min kline) fetch scope — owns the source's shared
    AntiBotProxy and its lazy persistent requests.Session.

    Every attempt goes through this proxy so the audit sees the fetches (real
    HTTP status; 4xx-tracked). Pacing (sleep) stays OFF — the worker dispatches
    per-stock fetches in threads under a hard per-fetch timeout and its own
    per-source cooldown (the source_a_sina row's 90s) governs; a proxy sleep
    here would eat the thread's FETCH_TIMEOUT_SEC budget. Host tracking stays
    off too: the worker layer already runs its own circuit breakers / source
    fallback, and a proxy-level block would silently change that policy.
    rotate_browser_profile / add_random_param stay row-driven (both true —
    fingerprint rotation plus a cache-buster against Sina's kline cache).
    """

    def __init__(self) -> None:
        self._proxy = AntiBotProxy(replace(
            antibot_config_for(SERVICE, "source_a_sina"),
            enable_sleep=False,
            enable_host_tracking=False,
        ))
        self._session: Optional[requests.Session] = None

    def _get_sina_session(self) -> requests.Session:
        """Lazy-create the persistent requests.Session for the Sina source.

        A Session reuses TLS connections across the round's fetches (one
        handshake per worker life instead of per stock).
        """
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def fetch_sina_minute(self, bare_code: str) -> Optional[List[MinuteSample]]:
        """Fetch today's 1-minute bars for one SZSE stock from Sina.

        Returns a list of (datetime, close, volume) samples, or None when the
        request fails / is blocked / yields nothing parseable (treated as a
        failure trigger for the fallback). Every HTTP attempt is audited by the
        proxy with its real status; parse-after-200 failures only log here.
        """
        symbol = f"sz{bare_code}"
        params = {"symbol": symbol, "scale": "1", "ma": "no", "datalen": "1970"}
        rows: Optional[list] = None
        err = "no attempts"
        for attempt in range(SINA_MAX_RETRIES + 1):
            r = self._proxy.get(
                self._get_sina_session(), SINA_MINUTE_URL, params=params,
                logger=logger, log_tag=f"[sina {bare_code}] ",
            )
            if r is None:
                err = "request failed (see audit)"
            else:
                parsed = _parse_sina_kline(r.text)
                if parsed is None:
                    err = (f"HTTP {r.status_code} body unparseable "
                           "(blocked page or format change?)")
                elif not parsed:
                    err = "empty kline list (unknown/delisted symbol?)"
                    break
                else:
                    rows = parsed
                    break
            if attempt < SINA_MAX_RETRIES:
                _time.sleep(SINA_RETRY_BACKOFF_SEC)
        if not rows:
            logger.warning("[sina %s] kline fetch failed after retries: %s",
                           bare_code, err)
            return None

        # Bar dicts: day, open, high, low, close, volume(, amount)
        samples: List[MinuteSample] = []
        for row in rows:
            day_val = row.get("day")
            close = row.get("close")
            vol = row.get("volume")
            if day_val is None or close is None:
                continue
            try:
                dt = datetime.strptime(str(day_val), "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                try:
                    dt = datetime.strptime(str(day_val), "%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    continue
            try:
                price = float(close)
            except (ValueError, TypeError):
                continue
            try:
                volume = float(vol) if vol is not None else 0.0
            except (ValueError, TypeError):
                volume = 0.0
            samples.append((dt, price, volume))
        return samples or None


# Module singleton — replaces the former module-level `_sina_proxy` /
# `_session` pair with the SAME construction scope (proxy built at import,
# session lazy).
SINA_SOURCE = SinaSource()


def _parse_sina_kline(text: str) -> Optional[list]:
    """Extract the JSON bar list from the jsonp_v2 shell.

    Success body: ``/*<script>location.href='//sina.com';</script>*/\\nvar
    _=([{...}, ...]);`` — split out the array exactly like the historical
    akshare parser. Returns None for any other shape (blocked page, format
    change), [] for a parseable empty list (unknown/delisted symbol).
    """
    try:
        payload = text.split("=(")[1].split(");")[0]
        data = json.loads(payload)
    except (IndexError, ValueError, TypeError):
        return None
    return data if isinstance(data, list) else None
