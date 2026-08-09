"""Path constants for the sec_info build.

All on-disk locations used by this package:
  · SZSE_REPORTS_DIR — temps/szse_etf_reports/<code>/<code>_<YYYYQn>_*.csv
  · OWNERS_JSON_PATH — _common/sec_statics/sec_owners.json (curated registry)
"""
from __future__ import annotations

import os

from _common.build_commons import PROJECT_ROOT

SZSE_REPORTS_DIR = os.path.join(PROJECT_ROOT, "temps", "szse_etf_reports")
OWNERS_JSON_PATH = os.path.join(
    PROJECT_ROOT, "_common", "sec_statics", "sec_owners.json")
