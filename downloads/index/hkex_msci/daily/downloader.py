"""downloads.index.hkex_msci.daily.downloader — HKEX LabCI widget fetches
for the MSCI China A 50 Connect Index daily closes.

Two hosts, both plain-requests friendly (no curl_cffi needed — probed
2026-10-26):

  * www.hkex.com.hk product prices page — bot-lenient HTML render whose
    inline ``getToken`` stub carries a fresh per-render LabCI token.
  * www1.hkex.com.hk/hkexwidget/data/getchartdata2 — the quote-widget data
    API. Every request must be JSONP-shaped with ``token`` (as served,
    already URL-encoded), ``qid=<ms timestamp>`` and a jQuery-style
    callback name (``jQuery<digits>_<digits>``); a request without ``qid``
    is answered with a plain Tomcat 403, which is why ad-hoc scrapes of
    this endpoint fail.

Chart granularity is span/int-joint: span=6&int=4 serves ~6 months of
daily bars, span=7&int=6 ~2 years of weekly bars (the feed's retention).
Rows render as ``[ts_ms, open, high, low, close, volume, extra]``; rows
with a null close pad non-trading days and are dropped.

The weekly bars are dated by their own bar timestamp (the week's first
trading day) while the close value is that week's last close — the chart
overlay accepts the ±few-days dating skew; daily bars win on every date
they cover, so the skew only exists before the daily window.

The fetch layer is the :class:`HkexMsciDailyDownloader` class — the former
module-level proxy/session singletons are per-instance attributes
(``self.proxy`` / ``self._session``) and the functions that used them are
its methods, while the pure payload parser and merge helper stay
module-level functions.
"""

from __future__ import annotations

import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

from downloads._common import (
    AntiBotProxy,
    build_headers_with_referer,
    retry_policy_for,
    setup_logger,
)

from downloads.index.hkex_msci.daily.config import (
    CHART_RIC,
    CLOSE_COLUMN,
    DAILY_INT,
    DAILY_SPAN,
    PRICES_PAGE_URL,
    REFERER_URL,
    RE_TOKEN,
    SERVICE,
    WIDGET_DATA_BASE,
    WEEKLY_INT,
    WEEKLY_SPAN,
)

logger: logging.Logger = setup_logger("hkex_msci_index")

# Retry policy — resolved once at import from the service's
# external_request_config JSON row (max_retries, retry_backoff_sec); the
# code fallback (2 retries) only applies if the row omits them.
_MAX_RETRIES_JSON, _RETRY_BACKOFF_JSON = retry_policy_for(SERVICE)
MAX_RETRIES: int = _MAX_RETRIES_JSON if _MAX_RETRIES_JSON is not None else 2
RETRY_BACKOFF_SEC: float = (
    _RETRY_BACKOFF_JSON if _RETRY_BACKOFF_JSON is not None else 20.0
)

RE_TOKEN_COMPILED: re.Pattern = re.compile(RE_TOKEN)

# HKT — the widget's bar timestamps render to exchange-local dates.
_HKT = timezone(timedelta(hours=8))


def _float_or_none(raw: object) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _jsonp_callback() -> str:
    """A jQuery-style callback name — ``jQuery<digits>_<digits>``."""
    return f"jQuery{random.randint(10**17, 10**18 - 1)}_{random.randint(10**12, 10**13 - 1)}"


class HkexMsciDailyDownloader:
    """LabCI widget fetch layer (proxy/session-scoped class).

    Every request goes through the shared :class:`AntiBotProxy` and a lazy
    ``requests`` session — the former module-level singletons are now
    instance attributes (``self.proxy`` / ``self._session``) built in
    ``__init__``, and the functions that used them are methods with the
    same names and sequencing; the pure payload parser and merge helper
    below stay module-level functions."""

    def __init__(self) -> None:
        # Built on the service row: base sleep + jitter, timeouts, host tracking.
        self.proxy = AntiBotProxy(service=SERVICE)
        self._session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def fetch_token(self, sleep_sec: Optional[float] = None) -> str:
        """Render the product prices page and extract the per-render LabCI token.

        The token is served URL-encoded; it is used as-is in widget query
        strings (the page's own loader concatenates it verbatim).
        """
        headers = build_headers_with_referer(REFERER_URL)
        session = self._get_session()
        for attempt in range(1, MAX_RETRIES + 1):
            resp = self.proxy.get(session, PRICES_PAGE_URL, headers=headers,
                                  sleep_sec=sleep_sec, logger=logger)
            if resp is not None and resp.status_code == 200:
                match = RE_TOKEN_COMPILED.search(resp.text)
                if match:
                    return match.group(1)
                logger.warning("  prices page rendered but no getToken stub found "
                               "(attempt %d/%d)", attempt, MAX_RETRIES)
            else:
                logger.warning("  prices page <- HTTP %s (attempt %d/%d)",
                               resp.status_code if resp is not None else "suppressed",
                               attempt, MAX_RETRIES)
            if attempt < MAX_RETRIES:
                self.proxy.sleep()
        raise RuntimeError(
            "could not extract the LabCI token from the HKEX prices page — "
            "the inline getToken stub is missing or the page render failed"
        )

    def fetch_chart_rows(self, token: str, span: int, interval: int,
                         sleep_sec: Optional[float] = None) -> List[Dict[str, object]]:
        """One getchartdata2 JSONP call → non-null daily-chart rows.

        Returns rows ``{date, open, high, low, close}`` (open/high/low may be
        None for aggregate bars the caller chose not to map); empty list when
        the endpoint returns no usable bars.
        """
        session = self._get_session()
        for attempt in range(1, MAX_RETRIES + 1):
            qid = int(datetime.now(tz=_HKT).timestamp() * 1000)
            callback = _jsonp_callback()
            url = (f"{WIDGET_DATA_BASE}/getchartdata2"
                   f"?hchart=1&span={span}&int={interval}&ric={CHART_RIC}"
                   f"&token={token}&qid={qid}&callback={callback}&_={qid + 1}")
            headers = build_headers_with_referer(REFERER_URL)
            resp = self.proxy.get(session, url, headers=headers,
                                  sleep_sec=sleep_sec, logger=logger)
            if resp is not None and resp.status_code == 200:
                body = resp.text.strip()
                if body.startswith(callback) and body.endswith(")"):
                    payload = json.loads(body[len(callback) + 1: -1])
                    return parse_chart_payload(payload)
                logger.warning("  getchartdata2 <- non-JSONP body %r (attempt %d/%d)",
                               body[:80], attempt, MAX_RETRIES)
            else:
                logger.warning("  getchartdata2 <- HTTP %s (attempt %d/%d)",
                               resp.status_code if resp is not None else "suppressed",
                               attempt, MAX_RETRIES)
            if attempt < MAX_RETRIES:
                self.proxy.sleep()
        return []


def parse_chart_payload(payload: dict) -> List[Dict[str, object]]:
    """A getchartdata2 JSONP payload → non-null chart rows.

    Rows are ``[ts_ms, open, high, low, close, volume, extra]``; rows whose
    close is null pad non-trading days and are dropped.
    """
    data = (payload or {}).get("data") or {}
    raw_rows: List[list] = data.get("datalist") or []
    out: List[Dict[str, object]] = []
    for row in raw_rows:
        close = _float_or_none(row[CLOSE_COLUMN]) if len(row) > CLOSE_COLUMN else None
        if close is None:
            continue
        ts_ms = float(row[0])
        bar_date = datetime.fromtimestamp(ts_ms / 1000, tz=_HKT).date()
        out.append({
            "date": bar_date,
            "open": _float_or_none(row[1]) if len(row) > 1 else None,
            "high": _float_or_none(row[2]) if len(row) > 2 else None,
            "low": _float_or_none(row[3]) if len(row) > 3 else None,
            "close": close,
        })
    out.sort(key=lambda r: r["date"])
    logger.info("  %d usable bars (%s .. %s)", len(out),
                out[0]["date"] if out else "-",
                out[-1]["date"] if out else "-")
    return out


def merge_index_rows(daily_rows: List[Dict[str, object]],
                     weekly_rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Merge the daily and weekly windows — daily wins per date.

    Weekly bars keep their close but drop the weekly open/high/low (they
    are week spans, not day values); inside the daily window they are
    redundant anyway.
    """
    by_date: Dict[object, Dict[str, object]] = {}
    for row in weekly_rows:
        by_date[row["date"]] = {
            "date": row["date"],
            "open": None,
            "high": None,
            "low": None,
            "close": row["close"],
        }
    for row in daily_rows:
        by_date[row["date"]] = row
    return sorted(by_date.values(), key=lambda r: r["date"])
