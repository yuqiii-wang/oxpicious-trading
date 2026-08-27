"""Download SSE fund/ETF (基金) TODAY's price snapshot via the list endpoint.

Fetches yunhq.sse.com.cn:32042/v1/sh1/list/exchange/fund and writes
``sse_trend_etf_{YYYYMMDD}.csv`` under ``temps/sse_trend/``.

For equity snapshots, use ``downloads.stock.sse.trend`` instead.
"""
from __future__ import annotations


import argparse

from downloads._common.exchanges.sse import SSE_FUND_LIST_URL
from downloads._common.exchanges.sse import run_snapshot_download


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download SSE fund/ETF TODAY's price snapshot (基金 tab)."
    )
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing date CSV file.")
    args = ap.parse_args()
    print(run_snapshot_download(SSE_FUND_LIST_URL, "sse_trend_etf", force=args.force))
