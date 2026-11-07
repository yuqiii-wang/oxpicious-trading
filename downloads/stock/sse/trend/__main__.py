"""Download SSE equity (股票) TODAY's price snapshot via the list endpoint.

Fetches yunhq.sse.com.cn:32042/v1/sh1/list/exchange/equity and writes
``sse_trend_stock_{YYYYMMDD}.csv`` under ``temps/sse_trend/``.

For ETF/fund snapshots, use ``downloads.etf.sse.trend`` instead.
For HISTORICAL per-stock data, use ``downloads.stock.sse.archive``.

The entry point is the :class:`SseStockTrendDownload` DownloadJob — a thin
delegate to the shared ``run_snapshot_download`` fetch layer; run() holds
the former __main__ body verbatim.
"""
from __future__ import annotations


import argparse

from downloads._common import DownloadJob, service_urls
from downloads._common.exchanges.sse import run_snapshot_download

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("trend")

SERVICE = "downloads.stock.sse.trend"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)


class SseStockTrendDownload(DownloadJob):
    """DownloadJob for the SSE equity TODAY-snapshot entry — run() is the
    former __main__ body verbatim, delegating to the exchanges.sse
    ``run_snapshot_download`` helper."""

    description = "Download SSE equity TODAY's price snapshot (股票 tab)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--force", action="store_true",
                            help="Overwrite existing date CSV file.")

    def run(self) -> None:
        # Inter-page pacing resolves from the service's external_request_config
        # JSON row inside run_snapshot_download.
        logger.info(run_snapshot_download(
            URLS["list_equity"], "sse_trend_stock", force=self.args.force, service=SERVICE,
        ))


if __name__ == "__main__":
    SseStockTrendDownload().execute()
