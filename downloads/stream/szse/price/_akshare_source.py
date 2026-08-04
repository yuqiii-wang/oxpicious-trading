"""SZSE source A — AkShare ``ak.stock_zh_a_minute`` (Sina 1-minute bars).

Primary source for SZSE stock intraday streaming. Fetches today's 1-minute
bars via AkShare (which wraps Sina Finance's minute endpoint). Returns a
list of ``(datetime, close, volume)`` samples.

AkShare is heavy (pandas/numpy/requests + V8), so it is lazy-imported at
first use. Only this source ever touches V8, so the old
``partition_address_space.cc(243)`` race cannot happen when the other
sources (East Money C/D, SZSE B) run in parallel.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from downloads._common.core import setup_logger

logger = setup_logger("stream_szse")

# A normalized 1-minute sample from either source.
#   dt     : datetime of the bar (e.g. 2025-07-23 09:31:00)
#   price  : last price for that minute
#   volume : per-minute volume (shares)
MinuteSample = Tuple[datetime, float, float]

_akshare = None


def _get_akshare():
    """Lazy-import akshare so the module loads even if akshare is absent."""
    global _akshare
    if _akshare is None:
        try:
            import akshare as _ak
        except ImportError as e:
            raise ImportError(
                "akshare is required for stream_szse_price.py primary source. "
                "Install with: pip install akshare"
            ) from e
        _akshare = _ak
    return _akshare


def fetch_akshare_minute(bare_code: str) -> Optional[List[MinuteSample]]:
    """Fetch today's 1-minute bars for one SZSE stock via AkShare.

    Returns a list of (datetime, close, volume) samples, or None when the
    request fails / is blocked (treated as a 4xx trigger for the fallback).
    """
    ak = _get_akshare()
    symbol = f"sz{bare_code}"
    try:
        df = ak.stock_zh_a_minute(symbol=symbol, period="1", adjust="")
    except Exception as e:
        logger.warning("[akshare %s] call failed: %s", symbol, e)
        return None
    if df is None or len(df) == 0:
        return None

    # Columns: day, open, high, low, close, volume
    samples: List[MinuteSample] = []
    for _, row in df.iterrows():
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
