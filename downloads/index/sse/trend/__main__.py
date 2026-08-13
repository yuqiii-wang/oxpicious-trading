"""Download SSE index (指数) TODAY's price snapshot via the list endpoint.

Fetches yunhq.sse.com.cn:32042/v1/sh1/list/exchange/index and writes
``sse_trend_index_{YYYYMMDD}.csv`` under ``temps/sse_trend/``.

SSE publishes ~200 indices via this endpoint (same JSONP schema as the
equity/fund tabs, only the path suffix differs). The snapshot contains
today's OHLC + last + prev_close + change + volume + amount for every
SSE-listed index. Volume/amount may be 0 for some indices.

For equity snapshots, use ``downloads.stock.sse.trend`` instead.
For ETF/fund snapshots, use ``downloads.etf.sse.trend`` instead.
For SZSE index trend data, use ``downloads.index.szse.trend`` instead.
"""
from __future__ import annotations

import argparse

from downloads.stock.sse._common.list_endpoint import SSE_INDEX_LIST_URL
from downloads.stock.sse._common.snapshot import run_snapshot_download


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download SSE index TODAY's price snapshot (指数 tab)."
    )
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing date CSV file.")
    args = ap.parse_args()
    print(run_snapshot_download(SSE_INDEX_LIST_URL, "sse_trend_index", force=args.force))
