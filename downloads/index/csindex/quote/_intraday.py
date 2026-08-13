"""Intraday granular ticks (1-day data) fetch and save.

The SPA does not embed intraday ticks in HTML — the data is served exclusively
via the index-perf-oneday JSON endpoint (the data source behind the website's
intraday chart). We parse the JSON directly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import requests

from downloads._common.core import (
    MIN_VALID_BYTES,
    DEFAULT_SLEEP_SEC,
    AntiBotProxy,
    AntiBotConfig,
    is_error_html,
)

from ._config import (
    API_INDEX_PERF_ONEDAY,
    CSINDEX_HEADERS,
    CSINDEX_TIMEOUT,
    logger,
    detail_referer,
)


def fetch_intraday(
    session: requests.Session,
    index_code: str,
    proxy: Optional[AntiBotProxy] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch latest trading-day intraday ticks via index-perf-oneday.

    Returns ``intraDayHeader`` (snapshot) + ``intraDayPerfList`` (ticks at ~15s
    intervals). The endpoint always returns the **latest** trading day regardless
    of a ``tradeDate`` parameter.
    """
    if proxy is None:
        proxy = AntiBotProxy(AntiBotConfig(base_sleep_sec=DEFAULT_SLEEP_SEC))

    base_headers = dict(CSINDEX_HEADERS)
    base_headers["Referer"] = detail_referer(index_code)

    resp = proxy.get(
        session,
        API_INDEX_PERF_ONEDAY,
        params={"indexCode": index_code},
        headers=base_headers,
        timeout=CSINDEX_TIMEOUT,
        logger=logger,
        log_tag=f"  [intraday-fetch {index_code}]",
    )
    if resp is None:
        logger.warning("  [intraday-fetch] %s: request failed", index_code)
        return None

    ctype = resp.headers.get("Content-Type", "")
    if is_error_html(ctype, resp.content):
        logger.warning("  [intraday-fetch] %s: got error HTML response (blocked?)", index_code)
        proxy.record_error(API_INDEX_PERF_ONEDAY, 403, "error_html_detected")
        return None

    try:
        payload = resp.json()
    except ValueError as e:
        logger.warning("  [intraday-fetch] %s: json parse error: %s", index_code, e)
        return None

    if payload.get("code") != "200":
        logger.warning("  [intraday-fetch] %s: code=%s msg=%s", index_code, payload.get("code"), payload.get("msg"))
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    return data


def save_intraday(
    data: Dict[str, Any],
    index_code: str,
    index_name: str,
    out_dir: Path,
) -> Optional[Path]:
    """Parse intraday ticks and save to CSV. Returns None if no ticks available."""
    tick_list = data.get("intraDayPerfList") or []
    if not tick_list:
        logger.info("  [intraday] %s: no granular ticks available, skipping", index_code)
        return None

    header = data.get("intraDayHeader") or {}
    # tradeDate in oneday response uses hyphens (YYYY-MM-DD); normalize to YYYYMMDD
    trade_date_raw = (header.get("tradeDate") or "").strip()
    trade_date_clean = trade_date_raw.replace("-", "")
    if not trade_date_clean and tick_list:
        trade_date_clean = str(tick_list[0].get("tradeDate") or "").replace("-", "")
    if not trade_date_clean:
        return None

    # Skip if intraday file for this date already exists
    out_file = out_dir / f"{index_code}_intraday_{trade_date_clean}.csv"
    if out_file.exists() and out_file.stat().st_size >= MIN_VALID_BYTES:
        logger.info("  [intraday] %s: %s already cached, skipping", index_code, out_file.name)
        return out_file

    rows = []
    for tick in tick_list:
        rows.append({
            "date": str(tick.get("tradeDate") or "").replace("-", ""),
            "time": tick.get("tradeTime") or "",
            "current": tick.get("current"),
            "high": tick.get("high"),
            "low": tick.get("low"),
            "close": tick.get("close"),
            "change": tick.get("change"),
            "changePct": tick.get("changePct"),
        })

    df = pd.DataFrame(rows, columns=["date", "time", "current", "high", "low", "close", "change", "changePct"])
    df.to_csv(out_file, index=False, encoding="utf-8-sig")

    # Save header snapshot alongside (for reference: openToday, closePre, tradingVol, tradingValue)
    snap_file = out_dir / f"{index_code}_intraday_{trade_date_clean}_snapshot.json"
    try:
        snap_file.write_text(json.dumps(header, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass

    logger.info("  [intraday] saved %s (%d ticks, date=%s)", out_file.name, len(df), trade_date_clean)
    return out_file
