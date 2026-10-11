"""Universe / snapshot-grid constants (analysis.analysis_signals.config).

Ported from the retired analysis.analysis_forecasts config (grid.py +
wide/snapshots.py) when the forecast layer was removed 2026-10-05 —
the signals tier owns its snapshot grid now: one snapshot per completed
year-end plus the ROLLING LATEST key (the sec_type's latest available
RSI data date), each evaluated over a trailing WINDOW_YEARS window of
the RSI percentile (rsi pct) buckets it emits as strategies.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from _common._holidays_and_weekdays import recent_trading_day_cutoff

# ---- Universe --------------------------------------------------------------
# (The price-convention source maps live in analysis.mov_ave_spread.config
# — the parent analysis that owns the convention.)

SEC_TYPES = ("index", "etf", "stock")

# Identity table per sec_type — the active-universe recent-data
# pre-filter (fetch_codes_with_recent_data_async).
SEC_TYPE_IDENTITY_TABLE = {
    "etf":   "stats.etf_identity",
    "index": "stats.index_identity",
    "stock": "stats.stock_identity",
}

# ---- Annual snapshot grid ---------------------------------------------------

# One snapshot per completed YEAR-END plus the ROLLING LATEST snapshot
# — keyed at the sec_type's latest available RSI data date (NOT the
# year-end) — by default the last N_YEARS completed year-ends + the
# rolling latest, each computed over a trailing WINDOW_YEARS window.
N_YEARS = 10

# Snapshots (and codes) whose actual history is shorter than the
# nominal window emit over their ACTUAL window — min(actual period,
# WINDOW_YEARS): the universe gate only drops snapshots before the
# universe's first data, and a code joins a snapshot from its own
# first-data date. Truncated-window rows are therefore expected; their
# occurrence counts carry the actual coverage.
WINDOW_YEARS = 10

# The trailing calendar window recorded on every strategy row's params
# (a recorded build parameter): f"{WINDOW_YEARS}y" — each snapshot
# summarizes the window (stat_date - WINDOW_YEARS, stat_date].
LOOKBACK_PERIOD = f"{WINDOW_YEARS}y"


def _shift_years(d: date, years: int) -> date:
    """Calendar-year shift with Feb-29 clamping. Pure stdlib — no
    cudf.pandas proxy dispatch."""
    y = d.year + years
    try:
        return d.replace(year=y)
    except ValueError:  # Feb 29 in a non-leap target year
        return d.replace(year=y, day=28)


def window_lower(stat_date: date) -> date:
    """The inclusive start of ``stat_date``'s trailing window:
    stat_date − WINDOW_YEARS + 1 day (the window covers exactly
    WINDOW_YEARS of calendar dates). The strategy row's start_date."""
    return _shift_years(stat_date, -WINDOW_YEARS) + timedelta(days=1)


# ---- The mutable-scope realization rule (the incremental contract) ----------
#
# A COMPLETED year-end snapshot is ALL-HISTORY: once its longest
# forward horizon (20 trading days) has realized, its rows never change
# again. The default (incremental) run targets snapshots MISSING from
# signal_thresholds plus the MUTABLE SCOPE — ``mutable_dates``:
#
#   - the ROLLING LATEST snapshot — keyed at the sec_type's LATEST
#     AVAILABLE DATA DATE: its window ends AT the key, the key is
#     idempotent across weekends / holidays (no data → no new key), and
#     it is re-emitted on EVERY run while its forward windows grow;
#   - the newest COMPLETED year-end, ONLY while its 20-trading-day
#     forward windows are still unrealized (~5 weeks after its
#     year-end it freezes and is never touched again).
REALIZE_TRADING_DAYS = 20   # the longest forward horizon (FORWARD_HORIZONS max)
REALIZE_BUFFER = 5          # holiday / suspension margin on top of the horizon
MUTABLE_TRADING_DAYS = REALIZE_TRADING_DAYS + REALIZE_BUFFER


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


# ---- Snapshot specs ---------------------------------------------------------

@dataclass(frozen=True)
class StatSpec:
    """One target snapshot date: the grid key plus the inclusive
    trailing-window bounds."""
    stat_date: date
    lower: date  # inclusive window start
    upper: date  # inclusive window end (== stat_date; the ROLLING
                 # LATEST spec's upper is the latest available data
                 # date, not a year-end)


def build_stat_specs(
    n_years: int = N_YEARS,
    window_years: int = WINDOW_YEARS,
    *,
    latest_date: date,
) -> list[StatSpec]:
    """The last ``n_years`` COMPLETED year-ends (bounded to year-ends
    the data has actually reached: YE <= latest_date) PLUS the ROLLING
    LATEST spec — keyed at ``latest_date`` — ascending, oldest first.
    When ``latest_date`` itself is a completed year-end it IS that
    year-end's spec — one key, deduped, seamless New-Year handover.
    Window lower = window_lower(stat_date), so each window spans
    exactly ``window_years`` of calendar dates."""
    specs: list[StatSpec] = []
    y = latest_date.year - 1
    while len(specs) < n_years and y >= latest_date.year - n_years:
        ye = date(y, 12, 31)
        if ye > latest_date:
            y -= 1
            continue
        specs.append(StatSpec(ye, window_lower(ye), ye))
        y -= 1
    specs.reverse()
    if all(s.stat_date != latest_date for s in specs):
        specs.append(StatSpec(
            latest_date, window_lower(latest_date), latest_date,
        ))
    return specs
