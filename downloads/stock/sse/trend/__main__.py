"""Download SSE equity (股票) TODAY's price snapshot via the list endpoint.

Fetches yunhq.sse.com.cn:32042/v1/sh1/list/exchange/equity and writes
``sse_trend_stock_{YYYYMMDD}.csv`` under ``temps/sse_trend/``.

For ETF/fund snapshots, use ``downloads.etf.sse.trend`` instead.
For HISTORICAL per-stock data, use ``downloads.stock.sse.archive``.
"""
from __future__ import annotations


import argparse

from downloads._common.exchanges.sse import SSE_LIST_URL
from downloads._common.exchanges.sse import run_snapshot_download

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("trend")

SERVICE = "downloads.stock.sse.trend"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Download SSE equity TODAY's price snapshot (股票 tab)."
    )
    ap.add_argument("--force", action="store_true",
                    help="Overwrite existing date CSV file.")
    args = ap.parse_args()
    from _common.service_heartbeat import autoregister  # lazy: keeps pandas behind the cudf hook
    autoregister()  # heartbeat + run audit (monitor.*)
    # Inter-page pacing resolves from the service's external_request_config
    # JSON row inside run_snapshot_download.
    logger.info(run_snapshot_download(
        SSE_LIST_URL, "sse_trend_stock", force=args.force, service=SERVICE,
    ))
