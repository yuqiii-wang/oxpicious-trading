"""Universe / snapshot-grid constants (analyze.analysis_forecasts.config)."""

from __future__ import annotations

from datetime import date, timedelta

from _common._holidays_and_weekdays import recent_trading_day_cutoff

# ---- Universe --------------------------------------------------------------

SEC_TYPES = ("index", "etf", "stock")

# Identity table per sec_type — used by the recent-data pre-filter
# (fetch_codes_with_recent_data_async). Same mapping as the other analyze
# modules (mov_ave_spread).
SEC_TYPE_IDENTITY_TABLE = {
    "etf":   "stats.etf_identity",
    "index": "stats.index_identity",
    "stock": "stats.stock_identity",
}

# ---- Annual snapshot grid ---------------------------------------------------

# "10 y period, incremental annually" (the 2026-09-22 annual migration;
# rolling-latest re-key 2026-09-25): one snapshot per completed YEAR-END
# PLUS the ROLLING LATEST snapshot — keyed at the sec_type's latest
# available data date (NOT the year-end) — by default the last N_YEARS
# completed year-ends + the rolling latest (a full decade of annual
# snapshots), each computed over a trailing WINDOW_YEARS window.
N_YEARS = 10
# Snapshots (and codes) whose actual history is shorter than the
# nominal window emit over their ACTUAL window — min(actual period,
# WINDOW_YEARS) — instead of being gated out: the universe gate only
# drops snapshots before the universe's first data, and a code joins a
# snapshot from its own first-data date (see _dfengine._month_chunks).
# Truncated-window rows are therefore expected; their occurrence
# counts carry the actual coverage (the regime-weights shrink
# N/(N+10) reads the same actual counts).
WINDOW_YEARS = 10

# The trailing calendar window recorded on EVERY analysis_forecasts row
# (``lookback_period`` column, TEXT — a recorded build parameter, not a
# PK member): f"{WINDOW_YEARS}y", i.e. each snapshot summarizes the
# window (stat_date - WINDOW_YEARS, stat_date]. '10y' while
# WINDOW_YEARS = 10; changing WINDOW_YEARS re-derives this automatically
# but requires a --force rebuild (rows are not re-keyed by lookback).
LOOKBACK_PERIOD = f"{WINDOW_YEARS}y"

# ---- The mutable-scope realization rule (the incremental contract) ----------
#
# A COMPLETED year-end snapshot is ALL-HISTORY: once its longest forward
# horizon (20 trading days) has realized, its rows never change again.
# The default (incremental) run targets snapshots MISSING from the
# target tables plus the MUTABLE SCOPE — ``mutable_dates``:
#
#   - the ROLLING LATEST snapshot — keyed at the sec_type's LATEST
#     AVAILABLE DATA DATE, not pinned to the year-end (the old
#     "running year" concept): its window ends AT the key, so a key is
#     always complete for its data, and the key is idempotent across
#     weekends / holidays (no data → no new key). Refreshed (deleted +
#     recomputed) on EVERY run while its forward windows grow;
#   - the newest COMPLETED year-end, ONLY while its 20-trading-day
#     forward windows are still unrealized (a year-end snapshot written
#     right after New Year carries truncated 20d occurrence counts for
#     its late-December triggers — their forward windows were not
#     complete yet at write time). ~5 weeks after its year-end the
#     snapshot freezes and is never touched again by an incremental run.
#
# Both tiers (analysis_forecasts AND analysis_signals) resolve the SAME
# rule through ``mutable_dates`` over the PRESENT stat_dates: the
# rolling latest key is always the max present date, the newest
# completed year-end the max Dec-31 strictly below it. On Dec 31 the
# rolling latest key IS the current year's year-end — the same key the
# completed snapshot will carry (one key, seamless New-Year handover).
REALIZE_TRADING_DAYS = 20   # the longest forward horizon (FORWARD_HORIZONS max)
REALIZE_BUFFER = 5          # holiday / suspension margin on top of the horizon
MUTABLE_TRADING_DAYS = REALIZE_TRADING_DAYS + REALIZE_BUFFER


def _shift_years(d: date, years: int) -> date:
    """Calendar-year shift with Feb-29 clamping (pandas DateOffset
    semantics). Pure stdlib — no cudf.pandas proxy dispatch."""
    y = d.year + years
    try:
        return d.replace(year=y)
    except ValueError:  # Feb 29 in a non-leap target year
        return d.replace(year=y, day=28)


def window_lower(stat_date: date) -> date:
    """The inclusive start of ``stat_date``'s trailing window:
    stat_date − WINDOW_YEARS + 1 day (the window covers exactly
    WINDOW_YEARS of calendar dates). Shared by the forecast grid
    (build_stat_specs) and the signals tier's strategy period start
    (start_date) — one helper, no 5y/10y drift."""
    return _shift_years(stat_date, -WINDOW_YEARS) + timedelta(days=1)


def _is_year_end(d: date) -> bool:
    return d.month == 12 and d.day == 31


def mutable_dates(present: set[date], today: date) -> set[date]:
    """The MUTABLE stat_dates among the present snapshot keys (see the
    block above): the rolling latest (max present — always) plus the
    newest completed year-end while its forward windows are unrealized
    (fewer than MUTABLE_TRADING_DAYS trading days have elapsed since
    it — ``recent_trading_day_cutoff`` over the static CN calendar).
    Pure calendar math — no DB access. Empty present → empty scope."""
    if not present:
        return set()
    latest = max(present)
    scope = {latest}
    year_ends = [d for d in present if _is_year_end(d) and d < latest]
    if year_ends and recent_trading_day_cutoff(
        MUTABLE_TRADING_DAYS, today,
    ) <= max(year_ends):
        scope.add(max(year_ends))
    return scope
