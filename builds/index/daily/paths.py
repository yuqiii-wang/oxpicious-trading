"""Path and constant definitions for the index baseline build.

All shared path constants are imported from builds._commons.paths.
Baseline-specific constants (the validation regex) remain here.
"""
from __future__ import annotations

import re

from builds._commons.paths import (
    CSINDEX_DIR,
    SZSE_ARCHIVE_DIR,
    SZSE_TREND_DIR,
    SSE_TREND_DIR,
    BSE_INDEX_ARCHIVE_DIR,
    CNINDEX_DIR,
    SZSE_INDEX_CODES,
)

# DB check constraint chk_index_identity_code_format: code must be 6 digits or
# H + 5 digits.  CSIndex publishes a few indices with non-conforming codes
# (e.g. CES100 中华港股通精选100) that would violate the constraint, so they
# are skipped during build.
VALID_CODE_RE = re.compile(r'^(\d{6}|H\d{5})$')

__all__ = [
    "CSINDEX_DIR", "SZSE_ARCHIVE_DIR", "SZSE_TREND_DIR", "SSE_TREND_DIR",
    "BSE_INDEX_ARCHIVE_DIR", "CNINDEX_DIR", "SZSE_INDEX_CODES",
    "VALID_CODE_RE",
]
