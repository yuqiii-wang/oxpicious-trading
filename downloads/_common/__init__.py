"""Shared download infrastructure — the public API for all downloaders.

Previously one monolithic ``core.py``; now split into focused modules:

  * ``net``      — HTTP layer: sessions/headers/browser profiles, anti-bot
                   proxy, host-blocking tracker, logger setup, safe_get/post
  * ``filescan`` — cached-file validity checks and filename date-key scans
  * ``codes``    — security-code canonicalization ("NNNNNN.XX" schema),
                   exchange/board/sec_type classification
  * ``io_csv``   — xlsx/csv persistence & reading (CSV-preferred reads,
                   byte-level code pre-filtering, xlsx→csv conversion)
  * ``plans``    — download plan builders (day/year/chunk) + run loop
  * ``exchanges``— per-exchange adapters: SZSE runner (xlsx ShowReport),
                   SSE list-endpoint snapshot downloader, BJS/BSE price

Trading-day calendar helpers are re-exported from the global
``_common._holidays_and_weekdays`` for backward compatibility with the old
``from downloads._common import is_trading_day`` pattern. DB access
lives in the global ``_common`` package (pre_check_and_load), never here.
"""
from __future__ import annotations

# --- trading-day calendar (compat re-exports from global _common) ----------
from _common._holidays_and_weekdays import (
    CN_ADJUSTED_WORKDAYS,
    CN_HOLIDAYS,
    business_days,
    count_weekdays,
    date_range_backward,
    date_range_forward,
    is_trading_day,
    last_business_day,
    next_business_day,
    parse_date_window,
)

# --- HTTP / anti-bot --------------------------------------------------------
from downloads._common.net import (
    BROWSER_PROFILES,
    COMMON_BASE_HEADERS,
    DEFAULT_ACCEPT,
    DEFAULT_ACCEPT_LANG,
    DEFAULT_SHORT_SLEEP_SEC,
    DEFAULT_SLEEP_SEC,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    LONG_SLEEP_INTERVAL,
    SUPER_LONG_SLEEP_INTERVAL,
    VERY_LONG_SLEEP_INTERVAL,
    AntiBotConfig,
    AntiBotProxy,
    HostStatus,
    HostStatusTracker,
    build_default_session,
    build_headers_with_referer,
    merge_browser_profile,
    random_browser_profile,
    random_sleep,
    random_sleep_range,
    safe_get,
    safe_post,
    setup_logger,
)

# --- file scanning & validity ----------------------------------------------
from downloads._common.filescan import (
    EMPTY_HTML_MAX_BYTES,
    MIN_VALID_BYTES,
    RE_CHUNKKEY_RANGE,
    RE_DATEKEY_YYYYMMDD,
    RE_DATEKEY_YYYYMMDD_DASH,
    RE_YEARKEY_YYYY,
    _extract_chunkkey,
    _extract_datekey,
    _extract_yearkey,
    is_error_html,
    is_fresh_today,
    is_valid_file,
    resolve_out_dir,
    scan_present_chunk_keys,
    scan_present_day_keys,
    scan_present_dates_with_pattern,
    scan_present_filenames,
    scan_present_year_keys,
    scan_valid_files,
)

# --- code canonicalization ---------------------------------------------------
from downloads._common.codes import (
    AMBIGUOUS_PREFIXES,
    SHANGHAI_BROADMARKET_INDEX_CODES,
    SHANGHAI_EXCLUSIVE_PREFIXES,
    SHENZHEN_EXCLUSIVE_PREFIXES,
    _normalize_raw_code,
    add_exchange_suffix,
    canonicalize_code_column,
    classify_board,
    classify_sec_type,
    clean_fund_share_class_names,
    filter_by_code,
    get_exchange_from_code,
    load_classification_index_names,
    load_classification_indices,
    normalize_code_column,
    strip_exchange_suffix,
)

# --- xlsx/csv io --------------------------------------------------------------
from downloads._common.io_csv import (
    RE_NUMERIC_PATTERN,
    clean_table_cell,
    clean_table_rows,
    convert_xlsx_to_csv,
    ensure_canonical_csv,
    normalize_dataframe_numbers,
    normalize_numeric_string,
    read_build_csv,
    read_csv_code_filtered_bytes,
    read_csv_gpu_safe,
    read_csv_preferred,
    safe_write_bytes,
)

# --- download plans -----------------------------------------------------------
from downloads._common.plans import (
    DEFAULT_START_DATE,
    EMPTY_MARKER_RETRY_DAYS,
    build_chunk_download_plan,
    build_day_download_plan,
    build_year_download_plan,
    run_plan_with_sleep,
    ChunkDownloadPlan,
    ChunkDownloadPlanItem,
    DayDownloadPlan,
    DayDownloadPlanItem,
    YearDownloadPlan,
    YearDownloadPlanItem,
    RunStats,
)

__all__ = [name for name in dir() if not name.startswith("_")]
