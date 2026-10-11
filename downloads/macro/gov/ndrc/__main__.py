"""CLI entry point for the ndrc.gov.cn 新闻发布 news downloader.

All download logic lives in :mod:`downloads.macro.gov.ndrc` (``__init__.py``);
this module only wires the package so it can be run as
``python -m downloads.macro.gov.ndrc``.
"""

from downloads.macro.gov.ndrc import main

if __name__ == "__main__":
    from _common.service_heartbeat import autoregister  # lazy: keeps pandas behind the cudf hook
    autoregister()  # heartbeat + run audit (monitor.*)
    main()
