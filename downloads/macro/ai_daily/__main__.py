"""CLI entry point for the daily AI market summary download.

All logic lives in :mod:`downloads.macro.ai_daily` (``__init__.py``);
this module only wires the package so it can be run as
``python -m downloads.macro.ai_daily``.
"""

from downloads.macro.ai_daily import main

if __name__ == "__main__":
    main()
