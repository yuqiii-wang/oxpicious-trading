"""Download Beijing Stock Exchange (BSE) daily price snapshot — CLI entry.

Download logic + BSE format conventions live in
``downloads/_common/exchanges/bjs.py``. This module only runs the
resource pre-check and exposes the command-line entry point.

The entry point is the :class:`BseStockPriceDownload` DownloadJob — run()
holds the former __main__ body verbatim (the exchanges.bjs shim call kept).
"""

from __future__ import annotations



from downloads._common import DownloadJob
from downloads._common.exchanges.bjs import download_bse_price

from _common.log_setup import setup_logging  # noqa: E402
logger = setup_logging("price")

SERVICE = "downloads.stock.bse.price"


class BseStockPriceDownload(DownloadJob):
    """DownloadJob for the BSE daily price snapshot — a thin delegate to the
    ``download_bse_price`` shim over ``BjsQuotationDownloader``
    (exchanges.bjs); run() is the former __main__ body verbatim."""

    def run(self) -> None:
        # Pacing (sleep/jitter/timeouts) resolves from the service's
        # external_request_config JSON row inside download_bse_price.
        logger.info(download_bse_price(service=SERVICE))


if __name__ == "__main__":
    BseStockPriceDownload().execute()
