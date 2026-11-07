"""CLI entry point for the gov.cn 政策解读 news downloader.

All download logic lives in :mod:`downloads.macro.gov.news` (``__init__.py``)
as the :class:`GovNewsDownload` DownloadJob; this module only wires the
package so it can be run as ``python -m downloads.macro.gov.news``.
"""

from downloads.macro.gov.news import GovNewsDownload

if __name__ == "__main__":
    GovNewsDownload().execute()
