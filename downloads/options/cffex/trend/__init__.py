"""downloads.options.cffex.trend — Download CFFEX daily options trend data.

This module downloads CFFEX options-specific data by:
  1. Checking stats.options_identity for the latest date (DB skip)
  2. Scanning existing _options.csv files for available dates (CSV skip)
  3. Backfilling missing dates from shared archive/trend CSVs
  4. Downloading remaining missing dates via Playwright browser automation
  5. Saving to temps/cffex_options_trend/YYYYMM/YYYYMMDD_options.csv

Usage:
  python -m downloads.options.cffex.trend
  python -m downloads.options.cffex.trend --start-date 2026-08-01 --end-date 2026-08-15
  python -m downloads.options.cffex.trend --force
  python -m downloads.options.cffex.trend --backfill
"""