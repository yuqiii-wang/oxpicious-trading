"""downloads.futures.sgx_a50 — daily SGX FTSE China A50 futures downloader.

Fetches daily OHLCV bars for the SGX FTSE China A50 futures chain from
Eastmoney's global-futures kline API (market ``104``): the front-month
continuous series (``CN00Y``) plus every listed individual contract
(``CN<YY><month letter>``). Bars land in the shared futures tables
(``stats.futures_identity`` + ``stats.futures_basic_stats``, family
``CN%``) with a CSV audit copy under ``temps/sgx_a50_daily/``.

Run:
  python -m downloads.futures.sgx_a50
  python -m downloads.futures.sgx_a50 --codes CN00Y,CN26V
  python -m downloads.futures.sgx_a50 --force
"""
