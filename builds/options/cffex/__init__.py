"""builds.options.cffex — Build CFFEX index options data → database.

Reads per-day CFFEX options CSV files from temps/cffex_archive/ and
temps/cffex_options_trend/, parses contracts, computes moneyness/Greeks,
and inserts into 7 split options_* tables under the same schema as SZSE options.

Usage:
  python -m builds.options.cffex
  python -m builds.options.cffex --start-date 2026-07-01 --end-date 2026-07-31
  python -m builds.options.cffex --force
"""