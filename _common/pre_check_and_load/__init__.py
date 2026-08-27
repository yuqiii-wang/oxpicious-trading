"""pre_check_and_load -- missing-data detection for build & analyze scripts.

This package is the single entry point for determining which (date, code)
pairs still need to be downloaded (build scripts) or computed (analyze
scripts). It is always run at the start of every build_* and analyze_*
script to avoid redundant work.

Two submodules:

  - identity: check_identity / check_identity_async / check_identity_years
    Answer "which trading days in [start, end] are NOT yet present in this
    identity table?" Used by download scripts (DB-first download mode).

  - missing_dates: find_missing_dates / find_missing_keys (build scripts),
    find_missing_analysis_dates / filter_rows_to_missing_dates_async
    (analyze scripts), and fetch_codes_with_recent_data_async (active-universe
    filter).

Migrated from _common/db_commons.py and _common/build_commons.py.
"""
from _common.pre_check_and_load.identity import (
    check_identity,
    check_identity_async,
    check_identity_years,
)
from _common.pre_check_and_load.missing_dates import (
    RECENT_TRADING_DAYS,
    fetch_codes_with_recent_data_async,
    find_missing_analysis_dates,
    find_missing_dates,
    find_missing_keys,
    filter_rows_to_missing_dates_async,
)
from _common.pre_check_and_load._legacy import (
    get_existing_dates_from_db,
    get_existing_years_from_db,
)

__all__ = [
    # identity
    "check_identity",
    "check_identity_async",
    "check_identity_years",
    # missing_dates
    "find_missing_dates",
    "find_missing_keys",
    "find_missing_analysis_dates",
    "filter_rows_to_missing_dates_async",
    "fetch_codes_with_recent_data_async",
    "RECENT_TRADING_DAYS",
    # legacy DB-scan wrappers (deprecated)
    "get_existing_dates_from_db",
    "get_existing_years_from_db",
]
