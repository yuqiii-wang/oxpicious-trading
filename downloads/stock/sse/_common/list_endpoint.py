"""Shared constants & helpers for the SSE list endpoint (yunhq.sse.com.cn).

The SSE ``list/exchange/{equity|fund|index}`` JSONP endpoint powers three
downloaders under ``downloads/stock/sse/``:

  * ``archive/__main__.py`` — historical per-stock dayk + PE backfill (uses the
    list endpoint only to enumerate target codes via ``LIST_SELECT_FIELDS``).
  * ``trend/__main__.py``    — today's end-of-day snapshot (equity + fund tabs)
    using the full real-time field set ``STREAM_SELECT_FIELDS``.
  * ``price/__main__.py``    — intraday 5-minute streaming (reuses the same
    full field set and pagination constants as ``trend``).

Extracting these symbols here breaks the historical cross-script import chain
(``stream_sse_price`` -> ``download_sse_trend`` -> ``download_sse_archive``)
so each leaf is independently importable.
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from downloads._common.core import (
    DEFAULT_TIMEOUT,
    build_headers_with_referer,
)


# --- Endpoint URLs ---------------------------------------------------------
SSE_LIST_URL = "https://yunhq.sse.com.cn:32042/v1/sh1/list/exchange/equity"
# Fund (ETF/LOF) tab — same JSONP schema as the equity endpoint, only the
# path suffix differs (/exchange/fund vs /exchange/equity).
SSE_FUND_LIST_URL = SSE_LIST_URL.replace("/equity", "/fund")
SSE_REFERER = "https://www.sse.com.cn/market/price/trends/"

# --- JSONP / pagination ----------------------------------------------------
JSONP_CALLBACK = "jQuery1"
PAGE_SIZE = 1000

# Minimal field set used by the archive enumerator (code+name only).
LIST_SELECT_FIELDS = "code,name"
# Full real-time field set for the list endpoint. The streaming/today-snapshot
# paths need last/volume/open/... — unlike the archive's list fetcher which
# only selects code+name (the OHLCV comes from the dayk endpoint there).
STREAM_SELECT_FIELDS = "code,name,open,high,low,last,prev_close,change,volume,amount"

# Inter-page sleep for list-endpoint pagination (lightweight, no anti-bot
# cadence needed — same as stream_sse_price).
INTER_PAGE_SLEEP_SEC = 3.0

SSE_HEADERS = build_headers_with_referer(SSE_REFERER, extra={"Accept": "*/*"})

# --- Output schema (mirrors szse_trend_stock CSV) --------------------------
COLUMNS: List[str] = [
    "交易日期",
    "证券代码",
    "证券简称",
    "前收",
    "开盘",
    "最高",
    "最低",
    "今收",
    "涨跌幅（%）",
    "成交量(万股)",
    "成交金额(万元)",
    "市盈率",
]
CSV_ENCODING = "utf-8-sig"


_RE_JSONP = re.compile(rf"^{re.escape(JSONP_CALLBACK)}\((.*)\);?\s*$", re.S)


# ---------------------------------------------------------------------------
# Shared helpers (JSONP, number coercion)
# ---------------------------------------------------------------------------
def _parse_jsonp(text: str) -> Dict[str, Any]:
    """Strip the JSONP callback wrapper and parse the inner JSON."""
    m = _RE_JSONP.match(text)
    if m:
        return json.loads(m.group(1))
    # Fallback: grab the first balanced (...) group.
    start = text.find("(")
    end = text.rfind(")")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start + 1 : end])
    raise ValueError("Cannot parse JSONP response from SSE endpoint")


def _num(val: Any) -> Optional[float]:
    """Coerce a JSON value to float, returning None when not numeric."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _fmt_num(val: Any) -> Any:
    """Render a numeric cell: blank for None, otherwise the raw number."""
    if val is None:
        return ""
    return val


def _write_rows(
    out_file: Path,
    rows: List[Dict[str, Any]],
    *,
    write_header: bool,
) -> None:
    """Write rows to CSV file."""
    mode = "a" if out_file.exists() and not write_header else "w"
    with open(out_file, mode, encoding=CSV_ENCODING, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for r in rows:
            writer.writerow(r)


__all__ = [
    "SSE_LIST_URL",
    "SSE_FUND_LIST_URL",
    "SSE_REFERER",
    "JSONP_CALLBACK",
    "PAGE_SIZE",
    "LIST_SELECT_FIELDS",
    "STREAM_SELECT_FIELDS",
    "INTER_PAGE_SLEEP_SEC",
    "SSE_HEADERS",
    "COLUMNS",
    "CSV_ENCODING",
    "_RE_JSONP",
    "_parse_jsonp",
    "_num",
    "_fmt_num",
    "_write_rows",
    "DEFAULT_TIMEOUT",
]
