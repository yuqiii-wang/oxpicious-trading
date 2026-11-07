"""CLI entry point for the ndrc.gov.cn 新闻发布 news downloader.

All download logic lives in :mod:`downloads.macro.gov.ndrc` (``__init__.py``)
as the :class:`GovNdrcDownload` DownloadJob; this module only wires the
package so it can be run as ``python -m downloads.macro.gov.ndrc``.
"""

from downloads.macro.gov.ndrc import GovNdrcDownload

if __name__ == "__main__":
    GovNdrcDownload().execute()
