"""CLI entry point for the gov.cn 政策解读 news downloader.

All download logic lives in :mod:`downloads.macro.gov.news` (``__init__.py``);
this module only wires argparse to :func:`main` so the package can be run as
``python -m downloads.macro.gov.news``.
"""

from downloads.macro.gov.news import main

if __name__ == "__main__":
    from _common.service_heartbeat import autoregister  # lazy: keeps pandas behind the cudf hook
    autoregister()  # heartbeat + run audit (monitor.*)
    main()
