"""Runtime sources + identity of live.live_signals (config).

The intraday / daily price sources per sec_type (the live bar and the
on-demand --date daily-close fallback), the probe order that resolves
a code's sec_type, the --signal-scheme vocabulary, and the
live.live_identity registration constants.
"""
from __future__ import annotations

# ---- Identity ---------------------------------------------------------------

PIPELINE_NAME = "live_signals"
PIPELINE_DESCRIPTION = (
    "Live breach check of the analysis_signals threshold set (the "
    "LIVE signal tier): fetch the code's latest intraday close "
    "(stats.*_intraday_5min; 404 when none) and compare every active "
    "analysis_signals.signal_strategies config's CURRENT value (per "
    "the declarative value-source map — intraday close, current RSI / "
    "spread, recomputed margin z) against the "
    "row's bar — static in the value's own space for every family "
    "EXCEPT mov_std, whose Bollinger band moves daily and is derived "
    "FRESH from the latest ma ± k·σ (fetch.resolve_threshold) — "
    "direction by the row's OWN action "
    "(sell breaches above, buy below) — the generic rule; no "
    "per-family logic. Triggered breaches are recorded in "
    "live.live_signals with the row's action and confidence."
)

# ---- Schemes ----------------------------------------------------------------

SIGNAL_SCHEMES = ("analysis", "strategy")

# ---- Intraday price source per sec_type (probe order) ------------------------

# Probe order: most common live-check targets first. A code normally
# exists in exactly ONE table — the sec_type is derived from the hit.
INTRADAY_TABLES = {
    "index": "stats.index_intraday_5min",
    "etf": "stats.etf_intraday_5min",
    "stock": "stats.stock_intraday_5min",
}
SEC_TYPE_PROBE_ORDER = ("index", "etf", "stock")

# ---- Daily close fallback per sec_type (the on-demand --date mode) ------------
#
# When --date D is given and the code has NO intraday bar on D (a date
# outside the intraday tables' retention, or one the intraday ingest never
# covered), the evaluation falls back to the code's OFFICIAL DAILY CLOSE on
# D from the basic_stats baseline — recorded at 15:00 with
# is_day_close_trigger = TRUE so the UI can distinguish the day-close
# observation from an intraday-bar breach.
DAILY_TABLES = {
    "index": "stats.index_basic_stats",
    "etf": "stats.etf_basic_stats",
    "stock": "stats.stock_basic_stats",
}

# The (hour, minute) recorded on day-close fallback rows
# (live.live_signals.time — the session close).
DAY_CLOSE_TIME = (15, 0)

__all__ = [
    "PIPELINE_NAME",
    "PIPELINE_DESCRIPTION",
    "SIGNAL_SCHEMES",
    "INTRADAY_TABLES",
    "SEC_TYPE_PROBE_ORDER",
    "DAILY_TABLES",
    "DAY_CLOSE_TIME",
]
