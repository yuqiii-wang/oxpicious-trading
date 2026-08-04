"""SSE stock flow — equity endpoint → sse_intraday CSV → stock_intraday_5min.

Polls the SSE list ``exchange/equity`` JSONP endpoint for all Shanghai-listed
stocks, archives raw snapshots to ``temps/sse_intraday/sse_intraday_YYYYMMDD.csv``
and aggregates 5 one-minute samples into OHLCV bars for
``stats.stock_intraday_5min`` (FK parent ``stats.stock_identity``).

Stock bars carry a per-bar ``trading_shares`` column (derived by subtracting
cumulative volumes across samples) and store the code WITH exchange suffix
(e.g. ``600000.SS``).
"""
from __future__ import annotations

from downloads.stock.sse._common.list_endpoint import SSE_LIST_URL

from ._io import _prepopulate_finished_codes
from ._model import AssetStream


def build_stock_asset() -> AssetStream:
    """Construct the AssetStream for the SSE equity (股票) flow."""
    return AssetStream(
        name="stock",
        list_url=SSE_LIST_URL,
        identity_table="stats.stock_identity",
        intraday_table="stats.stock_intraday_5min",
        code_suffix="SS",
        has_volume=True,
        allowed_codes=None,
        csv_subdir="sse_intraday",
        csv_prefix="sse_intraday",
    )


def prepopulate_stock_finished_codes(conn, trade_date, finished_codes: set) -> None:
    """Pre-populate finished_codes with SSE stocks that already have a 15:00 bar."""
    _prepopulate_finished_codes(
        conn, trade_date, finished_codes,
        table="stats.stock_intraday_5min", code_suffix_filter="SS",
    )
