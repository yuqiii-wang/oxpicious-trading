"""SSE ETF flow — fund endpoint → sse_etf_intraday CSV → etf_intraday_5min.

Polls the SSE list ``exchange/fund`` JSONP endpoint (same schema as the
equity tab, only the path suffix differs) for all Shanghai-listed ETFs/funds,
archives raw snapshots to ``temps/sse_etf_intraday/sse_etf_intraday_YYYYMMDD.csv``
and aggregates 5 one-minute samples into OHLCV bars for
``stats.etf_intraday_5min`` (FK parent ``stats.etf_identity``).

ETF bars carry a per-bar ``trading_shares`` column (derived by subtracting
cumulative volumes across samples) and store the code WITH exchange suffix
(e.g. ``510050.SS``). Mirrors the stock flow; the only difference is that
``etf_identity`` has no ``is_in_index_or_etf`` column, so that field is omitted
from the identity rows (handled in ``_model.aggregate_bars``).
"""
from __future__ import annotations

from downloads.stock.sse._common.list_endpoint import SSE_FUND_LIST_URL

from ._io import _prepopulate_finished_codes
from ._model import AssetStream


def build_etf_asset() -> AssetStream:
    """Construct the AssetStream for the SSE fund (基金) flow."""
    return AssetStream(
        name="etf",
        list_url=SSE_FUND_LIST_URL,
        identity_table="stats.etf_identity",
        intraday_table="stats.etf_intraday_5min",
        code_suffix="SS",
        has_volume=True,
        allowed_codes=None,
        csv_subdir="sse_etf_intraday",
        csv_prefix="sse_etf_intraday",
    )


def prepopulate_etf_finished_codes(conn, trade_date, finished_codes: set) -> None:
    """Pre-populate finished_codes with SSE ETFs that already have a 15:00 bar."""
    _prepopulate_finished_codes(
        conn, trade_date, finished_codes,
        table="stats.etf_intraday_5min", code_suffix_filter="SS",
    )
