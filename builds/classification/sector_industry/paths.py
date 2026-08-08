"""Path constants for the security-classification build.

All on-disk locations used by the classification package (CSV input dir,
the authoritative sec_classification.json cache, and the curated
sec_owners.json registry) are defined here so sub-modules import a single
source of truth.
"""
from __future__ import annotations

import os

from _common.build_commons import PROJECT_ROOT

CSV_DIR = os.path.join(PROJECT_ROOT, "temps", "csindex_linked_etf")
JSON_PATH = os.path.join(
    PROJECT_ROOT, "_common", "sec_statics", "sec_classification.json")
OWNERS_JSON_PATH = os.path.join(
    PROJECT_ROOT, "_common", "sec_statics", "sec_owners.json")
