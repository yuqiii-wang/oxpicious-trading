"""CLI entry point for the Zhihu news downloader.

All download logic lives in :mod:`downloads.macro.zhihu.news`
(``__init__.py``) as the :class:`ZhihuNewsDownload` DownloadJob; this module
only wires the package so it can be run as
``python -m downloads.macro.zhihu.news``.
"""

from downloads.macro.zhihu.news import ZhihuNewsDownload

if __name__ == "__main__":
    ZhihuNewsDownload().execute()
