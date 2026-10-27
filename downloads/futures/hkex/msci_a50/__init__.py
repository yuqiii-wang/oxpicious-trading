"""downloads.futures.hkex.msci_a50 — HKEX MSCI China A 50 Connect (USD)
Index Futures daily bars (By Contract Month), from the exchange's Daily
Market Report archives. Bars land in the shared futures tables
(``stats.futures_identity`` + ``stats.futures_basic_stats``, family
``MCA%``) with a CSV audit copy under ``temps/hkex_mca_daily/``.

Run:
  python -m downloads.futures.hkex.msci_a50
  python -m downloads.futures.hkex.msci_a50 --start-date 2026-10-01
  python -m downloads.futures.hkex.msci_a50 --force
"""
