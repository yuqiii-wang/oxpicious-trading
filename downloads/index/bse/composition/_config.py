"""Shared constants and logger for the BSE index composition download.

The closeweight URL template and the fetch/parse/cache helpers live in
``downloads.index.csindex.composition`` (reused, never duplicated — the
two BSE indices publish through the same CSI autofile).
"""
from __future__ import annotations

from downloads._common import setup_logger, sleep_sec_for

# Service name for the external_request_config JSON row (pacing source).
SERVICE = "downloads.index.bse.composition"

# ---------------------------------------------------------------------------
# Anti-bot cadence
# ---------------------------------------------------------------------------
# Sleep between HTTP requests — resolved from the service's
# external_request_config JSON row (sleep_sec=60: same 403-prone
# oss-ch.csindex.com.cn bucket as csindex composition, but this pass is
# 2 requests per monthly refresh, not a ~500-index sweep). Overridable
# per-run with --sleep-sec.
SLEEP_SEC: float = sleep_sec_for(SERVICE)

# ---------------------------------------------------------------------------
# Shared logger
# ---------------------------------------------------------------------------
logger = setup_logger("bse_index_composition")
