"""Path and constant definitions for the index baseline build.

All on-disk locations and validation regexes used by this package:
  • CSINDEX_DIR    — temps/csindex/                  (CSIndex *_history.csv + *_1m.csv)
  • SZSE_ARCHIVE_DIR — temps/szse_archive/           (SZSE historical archive)
  • SZSE_TREND_DIR  — temps/szse_trend/              (SZSE recent trend)
  • SSE_TREND_DIR   — temps/sse_trend/               (SSE trend snapshots)
  • CNINDEX_DIR     — temps/cnindex_archive/         (CNINDEX history exports)
"""
from __future__ import annotations

import os
import re

from _common.build_commons import PROJECT_ROOT

CSINDEX_DIR = os.path.join(PROJECT_ROOT, "temps", "csindex")
SZSE_ARCHIVE_DIR = os.path.join(PROJECT_ROOT, "temps", "szse_archive")
SZSE_TREND_DIR = os.path.join(PROJECT_ROOT, "temps", "szse_trend")
SSE_TREND_DIR = os.path.join(PROJECT_ROOT, "temps", "sse_trend")
CNINDEX_DIR = os.path.join(PROJECT_ROOT, "temps", "cnindex_archive")

# SZSE broad-market benchmarks to load from szse_archive/szse_trend index CSVs.
# These supplement the CSIndex history files with SZSE-only indexes.
SZSE_INDEX_CODES = {"399001", "399006", "399237"}  # 深证成指, 创业板指, 运输指数

# Minimum shared-weight threshold for proxy index selection during close
# estimation.  If no index has > 60% composition overlap with the target,
# the missing close is carried forward from the previous trading day.
SHARED_WEIGHT_THRESHOLD = 60.0

# DB check constraint chk_index_identity_code_format: code must be 6 digits or
# H + 5 digits.  CSIndex publishes a few indices with non-conforming codes
# (e.g. CES100 中华港股通精选100) that would violate the constraint, so they
# are skipped during build.
VALID_CODE_RE = re.compile(r'^(\d{6}|H\d{5})$')
