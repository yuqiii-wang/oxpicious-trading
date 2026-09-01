"""Equal-variant INSERT SQL for the industry-attributions step.

Copies ALL trading_amt rows to equal rows, dividing industry_shared_weight
by N (active member index count from stats.sec_classification).
benchmark_shared_weight and ALL non_this_industry_* columns are copied
UNCHANGED — they are identical between variants because they depend on
benchmark_shared_weight (undivided), NOT industry_shared_weight.

Runs AFTER both the broad-market INSERT and the member-index INSERT, so
the equal rows inherit the populated non_this_industry_* values from the
trading_amt rows.

Two variants:
  FULL        — no date filter, plain INSERT (force mode, after TRUNCATE).
  INCREMENTAL — date filter, plain INSERT (target dates pruned to
                genuinely missing ones + transaction-wrapped).
"""
from __future__ import annotations

from analyze.industry_sentiments.attributions.config import ROLLING_WINDOWS


# The per-row member count is pre-aggregated ONCE into a member_counts
# CTE (instead of a per-row CROSS JOIN LATERAL COUNT(DISTINCT ...)),
# then LEFT JOINed — cheap even when copying millions of rows.
_EQUAL_INSERT_SQL_TEMPLATE = """
WITH member_counts AS (
    SELECT industry_id, COUNT(DISTINCT code) AS n
    FROM stats.sec_classification
    WHERE type = 'index'
      AND industry_id IS NOT NULL
      AND industry_id <> ''
      AND is_active = TRUE
      AND is_industry_not_strategy = TRUE
    GROUP BY industry_id
)
INSERT INTO analysis.industry_attributions
    (industry_id, benchmark_code, date, attribution_type,
     industry_shared_weight, benchmark_shared_weight,
     benchmark_non_this_industry_price,
{rolling_cols},
     benchmark_non_this_industry_trading_amt)
SELECT
    ia.industry_id,
    ia.benchmark_code,
    ia.date,
    'equal' AS attribution_type,
    CASE
        WHEN COALESCE(mc.n, 0) > 0 THEN ROUND(ia.industry_shared_weight / mc.n, 4)
        ELSE ia.industry_shared_weight
    END AS industry_shared_weight,
    ia.benchmark_shared_weight,
    ia.benchmark_non_this_industry_price,
{rolling_select},
    ia.benchmark_non_this_industry_trading_amt
FROM analysis.industry_attributions ia
LEFT JOIN member_counts mc ON mc.industry_id = ia.industry_id
WHERE ia.attribution_type = 'trading_amt'
  {date_filter}
"""


def _build_equal_insert_sql(
    date_filter: str = "",
) -> str:
    """Build an equal-variant INSERT.

    Args:
      date_filter: e.g. "" for full, "AND ia.date = ANY($1::date[])" for
        incremental.
    """
    rolling_cols = ",\n".join(
        f"     benchmark_non_this_industry_rolling_{w}days_price"
        for w in ROLLING_WINDOWS
    )
    rolling_select = ",\n".join(
        f"    ia.benchmark_non_this_industry_rolling_{w}days_price"
        for w in ROLLING_WINDOWS
    )
    return _EQUAL_INSERT_SQL_TEMPLATE.format(
        rolling_cols=rolling_cols,
        rolling_select=rolling_select,
        date_filter=date_filter,
    )


# Full recompute (force mode, after TRUNCATE — plain INSERT).
EQUAL_INSERT_SQL_FULL = _build_equal_insert_sql()

# Incremental (date filter, plain INSERT).
EQUAL_INSERT_SQL_INCREMENTAL = _build_equal_insert_sql(
    date_filter="AND ia.date = ANY($1::date[])",
)
