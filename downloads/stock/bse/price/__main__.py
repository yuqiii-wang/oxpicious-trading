"""Download Beijing Stock Exchange (BSE) daily price snapshot — CLI entry.

Download logic + BSE format conventions live in
``downloads/_common/exchanges/bjs.py``. This module only runs the
resource pre-check and exposes the command-line entry point.
"""

from __future__ import annotations



from downloads._common.exchanges.bjs import download_bse_price

if __name__ == "__main__":
    print(download_bse_price())
