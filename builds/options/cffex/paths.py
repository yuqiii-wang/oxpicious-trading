"""builds.options.cffex.paths — Source directory paths for CFFEX options CSV files.

Option CSV files are stored under:
  temps/cffex_archive/YYYYMM/YYYYMMDD_options.csv    (archive data)
  temps/cffex_options_trend/YYYYMM/YYYYMMDD_options.csv  (trend/downloaded data)
  temps/cffex_trend/YYYYMM/YYYYMMDD_options.csv     (futures trend, also has options)

Also shared with builds.futures:
  temps/cffex_archive/YYYYMM/YYYYMMDD_futures.csv   (futures archive)
"""
from __future__ import annotations

import os
import glob

from _common.build_commons import PROJECT_ROOT

# Root directory for CFFEX archive data (shared with futures)
CFFEX_ARCHIVE_DIR: str = os.path.join(PROJECT_ROOT, "temps", "cffex_archive")

# Root directory for CFFEX options trend data
CFFEX_OPTIONS_TREND_DIR: str = os.path.join(PROJECT_ROOT, "temps", "cffex_options_trend")

# Root directory for CFFEX futures trend data (also contains _options.csv files)
CFFEX_FUTURES_TREND_DIR: str = os.path.join(PROJECT_ROOT, "temps", "cffex_trend")

# Filename suffix: options CSVs are named YYYYMMDD_options.csv
OPTIONS_SUFFIX: str = "_options.csv"


def glob_options_files() -> list[str]:
    """Glob all *_options.csv files under archive, options trend, and futures trend.

    Searches:
      - temps/cffex_archive/  (archive data)
      - temps/cffex_options_trend/  (options trend / downloaded data)
      - temps/cffex_trend/  (futures trend, also has _options.csv)

    Returns sorted unique file paths.
    """
    result: list[str] = []
    seen: set[str] = set()

    for base_dir in [CFFEX_ARCHIVE_DIR, CFFEX_OPTIONS_TREND_DIR, CFFEX_FUTURES_TREND_DIR]:
        if not os.path.isdir(base_dir):
            continue
        for root, _dirs, files in os.walk(base_dir):
            for fname in files:
                if fname.endswith(OPTIONS_SUFFIX):
                    path = os.path.join(root, fname)
                    if path not in seen:
                        seen.add(path)
                        result.append(path)

    return sorted(result)


def ymd_from_options_filename(filepath: str) -> str | None:
    """Extract YYYYMMDD from an options CSV filename.

    Args:
        filepath: path like ".../202607/20260701_options.csv"

    Returns:
        "20260701" or None if not parseable.
    """
    basename = os.path.basename(str(filepath))
    # Remove the _options.csv suffix
    stem = basename.replace(OPTIONS_SUFFIX, "")
    if len(stem) == 8 and stem.isdigit():
        return stem
    return None