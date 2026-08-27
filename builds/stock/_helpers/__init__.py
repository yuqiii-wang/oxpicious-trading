"""Shared helpers for builds.stock — GPU-safe converters, CSV readers, PE processing."""
from builds.stock._helpers.helpers import (
    COL_MAP,
    SOURCE_FILE_SETS,
    _safe_to_datetime,
    _safe_to_numeric,
    _safe_columns,
    _file_has_data,
    _peek_csv_max_date,
    _read_one,
    discover_source_files,
    build_missing_rows,
    _to_db,
    _to_db_series,
    _compute_eps_vec,
    _nan_to_none,
    dates_as_date_list,
    records_from_frame,
)
from builds.stock._helpers.pe_processing import (
    PE_ESTIMATE_MAX_MONTHS,
    _read_sse_pe_files,
    _read_sse_archive_trend_files,
    fetch_pe_estimate_candidates,
    estimate_missing_pe_async,
)
from builds.stock._helpers.etf_membership import (
    ETF_WEIGHT_THRESHOLD,
    compute_is_in_index_or_etf_async,
)

__all__ = [
    # helpers
    "COL_MAP", "SOURCE_FILE_SETS",
    "_safe_to_datetime", "_safe_to_numeric", "_safe_columns",
    "_file_has_data", "_peek_csv_max_date", "_read_one",
    "discover_source_files", "build_missing_rows",
    "_to_db", "_to_db_series", "_compute_eps_vec",
    "dates_as_date_list", "records_from_frame", "_nan_to_none",
    # pe_processing
    "PE_ESTIMATE_MAX_MONTHS",
    "_read_sse_pe_files", "_read_sse_archive_trend_files",
    "fetch_pe_estimate_candidates", "estimate_missing_pe_async",
    # etf_membership
    "ETF_WEIGHT_THRESHOLD", "compute_is_in_index_or_etf_async",
]
