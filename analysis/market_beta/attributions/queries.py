"""Read-only probe queries for the industry-attributions step.

Guard/preview counts, rolling-column backfill detection, and downstream
missing-date detection. All functions are read-only (no writes).

The migrated read-side SELECTs live in sql views (rendered via
_common.sql_views, by attributions.runner / fetch_incremental_lookback_date):
  analysis/market_beta/attributions/queries/count_source
  analysis/market_beta/attributions/queries/preview_dimensions
  analysis/market_beta/attributions/queries/preview_member_dimensions
  analysis/market_beta/attributions/queries/count_member_indices
  analysis/market_beta/attributions/queries/fetch_lookback_date
The rolling-backfill EXISTS probe (SELECT 1-style, against this step's own
target table) and the missing-date detection (missing-date check against
this step's own target table) stay inline below.
"""
from __future__ import annotations

import datetime
from typing import Set

from _common.sql_views import render_sql_view


# ---------------------------------------------------------------------------
#  Guard + preview queries
# ---------------------------------------------------------------------------
# Rendered from sql views at the call sites (attributions.runner):
#   count_source                 — analysis/market_beta/attributions/
#                                  queries/count_source
#   preview_dimensions           — analysis/market_beta/attributions/
#                                  queries/preview_dimensions
#   preview_member_dimensions    — analysis/market_beta/attributions/
#                                  queries/preview_member_dimensions
#   count_member_indices         — analysis/market_beta/attributions/
#                                  queries/count_member_indices


# ---------------------------------------------------------------------------
#  Rolling-column backfill detection
#
#  needs_rolling_backfill() lets callers detect rows that SHOULD have
#  rolling price data (broad-market benchmark, benchmark_shared_weight > 0)
#  but have a NULL rolling_255d/120d column — e.g. after an ALTER TABLE ADD
#  COLUMN or an interrupted pre-transaction run. The fix is a full recompute
#  (run_attributions with force=True): the merged INSERT recomputes ALL
#  columns, including the rolling prices, for every date.
# ---------------------------------------------------------------------------

_ROLLING_BACKFILL_CHECK_SQL = """
    SELECT EXISTS (
        SELECT 1
        FROM analysis.industry_attributions
        WHERE attribution_type = 'trading_amt'
          AND benchmark_code IN (
            SELECT code FROM stats.sec_index_tags
            WHERE is_broad_market = TRUE
        )
        AND (
            -- 255d is the long-established column; NULL here means a prior
            -- force run never completed (or the table was ALTER'd with a
            -- schema change). Triggers a FULL backfill.
            benchmark_non_this_industry_rolling_255days_price IS NULL
            -- 120d is the newest column (added with the
            -- industry_hypes_and_drains feature). After ALTER TABLE ADD
            -- COLUMN, ALL existing rows have NULL 120d — the incremental
            -- UPDATE's date filter would only fix target dates, leaving
            -- historical dates NULL. Detect this and trigger a FULL
            -- backfill so 120d is populated for every historical date.
            OR benchmark_non_this_industry_rolling_120days_price IS NULL
        )
        AND benchmark_shared_weight > 0
    ) AS needs_backfill
"""


async def needs_rolling_backfill(conn) -> bool:
    """Check if broad-market rows have NULL rolling price columns.

    Returns True when existing rows SHOULD have rolling data (broad-market
    benchmark with benchmark_shared_weight > 0) but the
    benchmark_non_this_industry_rolling_255days_price column is NULL —
    typically after a schema change that added the column via ALTER TABLE
    without repopulating it.
    """
    try:
        return bool(await conn.fetchval(_ROLLING_BACKFILL_CHECK_SQL))
    except Exception:
        return False


async def find_missing_attribution_dates(conn) -> Set[datetime.date]:
    """Return dates present in stats.cross_stats (industry grain) but
    missing from industry_attributions.

    Downstream-date detection for the no-corr mode of
    analysis.market_beta: when the correlations step is skipped,
    the incremental target dates for attributions / etf_contribution are
    the source (cross_stats) dates that have not been aggregated yet,
    instead of corr window-end dates.
    """
    rows = await conn.fetch("""
        WITH src AS (
            SELECT DISTINCT date FROM stats.cross_stats
            WHERE sec_type = 'industry'
        ),
        got AS (
            SELECT DISTINCT date FROM analysis.industry_attributions
        )
        SELECT s.date
        FROM src s LEFT JOIN got g ON g.date = s.date
        WHERE g.date IS NULL
        ORDER BY s.date
    """)
    return {r["date"] for r in rows}


# ---------------------------------------------------------------------------
#  Incremental lookback resolution (B-A5 cap)
# ---------------------------------------------------------------------------

# The lookback SELECT lives in the sql view
# analysis/market_beta/attributions/queries/fetch_lookback_date: the
# LOOKBACK_TRADING_DAYS-th broad-benchmark trading date strictly before
# the earliest target date (the subquery takes the N NEWEST DISTINCT
# dates; MIN over that window is exactly the Nth-prior trading date,
# falling back to the earliest index-history date when history is shorter
# than the lookback so the bound never truncates warm-up).


async def fetch_incremental_lookback_date(conn, min_date: datetime.date) -> datetime.date:
    """Trading-day-precise lookback start for the incremental INSERT.

    Resolves the date LOOKBACK_TRADING_DAYS broad-benchmark trading days
    before ``min_date`` and shifts it back by
    LOOKBACK_EXTRA_CALENDAR_DAYS so a stock's LAG(close) at the boundary
    row survives suspensions up to ~45 extra calendar days (see config.py).
    The result is passed as a plain date parameter — a plan-time
    constant (a scalar subquery would be re-evaluated per outer row).
    """
    from analysis.market_beta.attributions.config import (
        LOOKBACK_EXTRA_CALENDAR_DAYS,
        LOOKBACK_TRADING_DAYS,
    )

    view = render_sql_view(
        "analysis/market_beta/attributions/queries/fetch_lookback_date",
        min_date=min_date,
        lookback_days=LOOKBACK_TRADING_DAYS,
    )
    resolved = await conn.fetchval(view.sql, *view.params)
    if resolved is None:
        return datetime.date(1900, 1, 1)
    return resolved - datetime.timedelta(days=LOOKBACK_EXTRA_CALENDAR_DAYS)
