"""builds.futures.paths — Source directory paths for CFFEX archive CSV files.

CFFEX archive files are stored under:
  temps/cffex_archive/YYYYMM/YYYYMMDD_futures.csv

The daily trend downloader (downloads.futures.cffex.trend) writes the same
file layout under temps/cffex_trend/ for the current month — the build
globs BOTH directories so the daily files reach the DB before the month's
archive ZIP exists.

Each monthly subdirectory contains:
  - YYYYMMDD_futures.csv  : futures contracts for that day
  - YYYYMMDD_options.csv  : options contracts for that day (separate build)
  - YYYYMMDD_1.csv        : raw combined archive file (used for download)
"""
from __future__ import annotations

import os

from _common.build_commons import PROJECT_ROOT

# Root directory for CFFEX archive data
CFFEX_ARCHIVE_DIR: str = os.path.join(PROJECT_ROOT, "temps", "cffex_archive")

# Daily trend files (downloads.futures.cffex.trend) — same file layout as
# the archive; the build treats both directories as one source
CFFEX_TREND_DIR: str = os.path.join(PROJECT_ROOT, "temps", "cffex_trend")

# Glob pattern for futures CSV files
# Files are named like: 20260701_futures.csv
FUTURES_CSV_PATTERN: str = "*_futures.csv"

# Filename prefix used for YMD extraction
# e.g. from "20260701_futures.csv" we extract "20260701"
FUTURES_FILE_PREFIX: str = ""  # prefix is the date itself; ymd_from_filename handles it