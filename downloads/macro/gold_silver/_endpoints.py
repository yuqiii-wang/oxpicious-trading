"""Endpoint access layer + parsers for the macro gold/silver downloader
(上海黄金交易所 SGE spot bars + sina 外盘期货 COMEX daily klines).

Studied from the akshare source (2026-11-21 — ``spot_sge.py`` /
``futures_foreign.py``, the same endpoints akshare wraps):

  SGE daily spot bars (FULL history per instrument, ONE POST):
    POST https://www.sge.com.cn/graph/Dailyhq
         Content-Type: application/x-www-form-urlencoded; charset=UTF-8
         X-Requested-With: XMLHttpRequest
         Referer: https://www.sge.com.cn/sjzx/mrhq
         body instid=Au99.99        (also Ag99.99, ...)
    → {"time": [["2024-01-02", open, close, low, high], ...]} — the row
      order is (date, open, close, LOW, HIGH) — close precedes low.
      Values arrive as strings; blank tokens are possible.

  sina 外盘期货 daily kline (FULL history per symbol, ONE GET):
    GET https://stock2.finance.sina.com.cn/futures/api/jsonp.php/
        var%20_S{Y}_{M}_{D}=/GlobalFuturesService.getGlobalFuturesDailyKLine
        ?symbol=GC&_=...&source=web
    → jsonp text wrapping a JSON array of
      {"date": "2024-01-02", "open", "high", "low", "close", "volume"}
      objects (volume "" for old rows) — symbol GC = COMEX gold main
      continuous, SI = COMEX silver main continuous
      (https://finance.sina.com.cn/money/future/hf.html).

Anti-bot: the fetch helpers route through the shared AntiBotProxy with
the service's external_request_config row pacing (4 requests per run).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from datetime import date
from typing import Any, Dict, List, Optional

import requests

from downloads._common import (
    AntiBotProxy,
    antibot_config_for,
    build_headers_with_referer,
    service_urls,
)

# Instrument universe — code order (code, csv_stem, source):
#   SGE  Au99.99 / Ag99.99 — onshore 人民币金价/银价 (CNY/克, CNY/kg)
#   sina GC / SI           — COMEX gold/silver main continuous (USD/ozt)
SGE_INSTRUMENTS: tuple[Dict[str, str], ...] = (
    {"code": "Au99.99", "csv_stem": "sge_au99.99", "source": "sge"},
    {"code": "Ag99.99", "csv_stem": "sge_ag99.99", "source": "sge"},
)
SINA_SYMBOLS: tuple[Dict[str, str], ...] = (
    {"code": "GC", "csv_stem": "comex_gc", "source": "sina"},
    {"code": "SI", "csv_stem": "comex_si", "source": "sina"},
)

# Uniform CSV schema the builds.macro pipeline reads (volume stays empty
# for the SGE spot bars — the source publishes none).
CSV_COLUMNS = ["date", "open", "high", "low", "close", "volume"]

_RE_JSON_ARRAY = re.compile(r"\[.*\]", re.S)

# The jsonp wrapper assigns the array: `var _S{...}=[...]` (an optional
# CDATA comment may precede it — anchor on the assignment, not the first
# bracket).
_RE_ASSIGN_ANCHOR = "=["


def parse_comma_number(token: Any) -> Optional[float]:
    """'"613.5"' → 613.5; ''/'--'/None → None (never raises)."""
    if token is None:
        return None
    s = str(token).strip().replace(",", "")
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_iso_date(token: Any) -> str:
    """Row date token → 'YYYY-MM-DD' ('' when unparseable — never raises)."""
    s = str(token or "").strip()
    if len(s) > 10:
        s = s[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return ""


def sge_rows_to_csv_rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """SGE Dailyhq payload ``time`` rows → uniform CSV rows.

    The wire row order is (date, open, close, LOW, HIGH); blanks map to
    None. Rows sort ascending by date and dedupe (the later row wins).
    """
    raw = payload.get("time") or []
    rows: Dict[str, Dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 5:
            continue
        iso = _to_iso_date(item[0])
        if not iso:
            continue
        rows[iso] = {
            "date": iso,
            "open": parse_comma_number(item[1]),
            "high": parse_comma_number(item[4]),
            "low": parse_comma_number(item[3]),
            "close": parse_comma_number(item[2]),
            "volume": None,
        }
    return [rows[d] for d in sorted(rows)]


def sina_kline_text_to_csv_rows(text: str) -> List[Dict[str, Any]]:
    """jsonp kline text → uniform CSV rows (same sort/dedupe contract)."""
    anchor = text.find(_RE_ASSIGN_ANCHOR)
    end = text.rfind("]")
    if anchor >= 0 and end > anchor:
        body = text[anchor + 1:end + 1]
    else:
        match = _RE_JSON_ARRAY.search(text)
        if not match:
            return []
        body = match.group(0)
    try:
        payload = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    rows: Dict[str, Dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        iso = _to_iso_date(item.get("date"))
        if not iso:
            continue
        rows[iso] = {
            "date": iso,
            "open": parse_comma_number(item.get("open")),
            "high": parse_comma_number(item.get("high")),
            "low": parse_comma_number(item.get("low")),
            "close": parse_comma_number(item.get("close")),
            "volume": parse_comma_number(item.get("volume")),
        }
    return [rows[d] for d in sorted(rows)]


class GoldSilverEndpoints:
    """Endpoint access layer of the macro gold/silver downloader
    (stateless, the EFundsEndpoints pattern — ``session`` / ``proxy`` /
    ``service`` stay per-call arguments)."""

    def build_proxy(
        self, service: str, sleep_sec: Optional[float] = None,
    ) -> AntiBotProxy:
        """AntiBotProxy over the service's external_request_config row
        (a non-None *sleep_sec* overrides only the base sleep)."""
        if sleep_sec is None:
            return AntiBotProxy(service=service)
        return AntiBotProxy(
            replace(antibot_config_for(service), base_sleep_sec=sleep_sec),
            service=service,
        )

    def fetch_sge_daily(
        self,
        session: requests.Session,
        proxy: AntiBotProxy,
        inst_id: str,
        service: str,
        logger: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """POST the SGE daily spot history for one instrument.

        Returns the parsed payload dict, or None on request/parse failure.
        """
        urls = service_urls(service)
        headers = build_headers_with_referer(
            urls["sge_quote_page"],
            extra={
                "Accept": "text/html, */*; q=0.01",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://www.sge.com.cn",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        resp = proxy.post(
            session,
            urls["sge_dailyhq"],
            data={"instid": inst_id},
            headers=headers,
            logger=logger,
            log_tag=f"[sge {inst_id}]",
        )
        if resp is None or resp.status_code != 200:
            return None
        try:
            payload = resp.json()
        except (ValueError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def fetch_sina_kline(
        self,
        session: requests.Session,
        proxy: AntiBotProxy,
        symbol: str,
        service: str,
        logger: Any = None,
    ) -> Optional[str]:
        """GET the sina 外盘期货 daily kline jsonp text for one symbol.

        Returns the raw response text (parsed by
        :func:`sina_kline_text_to_csv_rows`), or None on request failure.
        """
        today = f"{date.today():%Y_%m_%d}"
        urls = service_urls(service)
        headers = build_headers_with_referer(
            urls["sina_future_page"],
            extra={
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )
        resp = proxy.get(
            session,
            urls["sina_global_kline"].format(today=today),
            params={"symbol": symbol, "_": today, "source": "web"},
            headers=headers,
            logger=logger,
            log_tag=f"[sina {symbol}]",
        )
        if resp is None or resp.status_code != 200:
            return None
        return resp.text
