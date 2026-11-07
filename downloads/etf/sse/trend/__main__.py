"""Download SSE fund/ETF (基金) TODAY's price snapshot via the list endpoint.

Fetches yunhq.sse.com.cn:32042/v1/sh1/list/exchange/fund and writes
``sse_trend_etf_{YYYYMMDD}.csv`` under ``temps/sse_trend/``.

For equity snapshots, use ``downloads.stock.sse.trend`` instead.

The entry point is the :class:`SseEtfTrendDownload` DownloadJob — the
former ``__main__`` body is its ``run()`` and the fetch layer is the
stateless ``SseSnapshotDownloader`` instance held in ``__init__`` (the
former ``run_snapshot_download`` shim call, signature mapped 1:1).
"""
from __future__ import annotations


import argparse

from downloads._common import DownloadJob, service_urls
from downloads._common.exchanges.sse import SseSnapshotDownloader

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("trend")

SERVICE = "downloads.etf.sse.trend"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)
SSE_FUND_LIST_URL = URLS["list_fund"]


class SseEtfTrendDownload(DownloadJob):
    """DownloadJob for the SSE ETF/fund snapshot fetch cycle — the former
    ``__main__`` body is ``run()`` verbatim and the argparse declarations
    moved to ``add_arguments``; the fetch layer is the stateless
    SseSnapshotDownloader instance."""

    description = "Download SSE fund/ETF TODAY's price snapshot (基金 tab)."

    def __init__(self) -> None:
        super().__init__()
        self.downloader = SseSnapshotDownloader()

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--force", action="store_true",
                        help="Overwrite existing date CSV file.")

    def run(self) -> None:
        logger.info(self.downloader.run_snapshot_download(
            SSE_FUND_LIST_URL, "sse_trend_etf",
            force=self.args.force, service=SERVICE))


if __name__ == "__main__":
    SseEtfTrendDownload().execute()
