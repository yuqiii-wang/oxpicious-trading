"""Directory paths and ETF code-prefix constants for builds.etf.

All path constants are imported from builds._commons.paths for consistency
across all build modules.
"""
from builds._commons.paths import (
    SZSE_ARCHIVE_DIR,
    SZSE_TREND_DIR,
    SSE_TREND_DIR,
    SZSE_MARGIN_DIR,
    SSE_MARGIN_DIR,
    COMP_DIR,
    SZSE_ETF_PREFIXES,
    SSE_ETF_PREFIXES,
)

__all__ = [
    "SZSE_ARCHIVE_DIR", "SZSE_TREND_DIR", "SSE_TREND_DIR",
    "SZSE_MARGIN_DIR", "SSE_MARGIN_DIR", "COMP_DIR",
    "SZSE_ETF_PREFIXES", "SSE_ETF_PREFIXES",
]
