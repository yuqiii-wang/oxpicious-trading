"""CLI entry point for the gov.cn 政策解读 article detail crawler.

All logic lives in :mod:`downloads.macro.gov.articles` (``__init__.py``) as
the :class:`GovArticlesDownload` DownloadJob; this module only wires the
package so it can be run as ``python -m downloads.macro.gov.articles``.
"""

from downloads.macro.gov.articles import GovArticlesDownload

if __name__ == "__main__":
    GovArticlesDownload().execute()
