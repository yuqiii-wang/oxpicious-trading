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

The entry point is the :class:`SseIndexTrendDownload` DownloadJob — the
former ``__main__`` body is ``run()`` and the argparse setup moved into
``add_arguments`` (byte-identical help); the fetch cycle stays the (shimmed)
``run_snapshot_download`` exchange entry (pacing resolves from this
service's external_request_config JSON row inside it).
"""
from __future__ import annotations


import argparse

from downloads._common import DownloadJob, service_urls
from downloads._common.exchanges.sse import run_snapshot_download

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("trend")

# Pacing (inter-page sleep, jitter, timeouts, toggles) resolves from this
# service's external_request_config JSON row inside run_snapshot_download.
SERVICE = "downloads.index.sse.trend"

# Endpoint URLs — the service's external_request_config JSON row (urls
# object) is the single source; a missing role raises at the point of use.
URLS = service_urls(SERVICE)


class SseIndexTrendDownload(DownloadJob):
    """DownloadJob for the SSE index snapshot fetch cycle — the former
    ``__main__`` body is ``run()`` (verbatim); the fetch cycle stays the
    shared ``run_snapshot_download`` exchange entry."""

    description = "Download SSE index TODAY's price snapshot (指数 tab)."

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--force", action="store_true",
                            help="Overwrite existing date CSV file.")

    def run(self) -> None:
        logger.info(run_snapshot_download(
            URLS["list_index"], "sse_trend_index", force=self.args.force,
            service=SERVICE,
        ))


if __name__ == "__main__":
    SseIndexTrendDownload().execute()
