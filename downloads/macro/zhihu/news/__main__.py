"""CLI entry point for the Zhihu news downloader.

All download logic lives in :mod:`downloads.macro.zhihu.news` (``__init__.py``);
this module only wires argparse to :func:`download_zhihu_news` so the package
can be run as ``python -m downloads.macro.zhihu.news``.
"""
from downloads.macro.zhihu.news import main

if __name__ == "__main__":
    main()
