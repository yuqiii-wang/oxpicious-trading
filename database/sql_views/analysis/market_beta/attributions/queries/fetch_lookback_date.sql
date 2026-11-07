-- Industry-attributions step incremental lookback resolution (B-A5 cap) —
-- the LOOKBACK_TRADING_DAYS-th broad-benchmark trading date strictly
-- before the earliest target date: the subquery takes the N NEWEST
-- DISTINCT dates; MIN over that window is exactly the Nth-prior trading
-- date. Falls back to the earliest index-history date (never NULL) when
-- history is shorter than the lookback (fresh ingest) so the bound never
-- truncates warm-up. The result is shifted back by
-- LOOKBACK_EXTRA_CALENDAR_DAYS in Python (per-stock LAG suspension
-- margin) and passed to the incremental INSERT as a plan-time constant.
-- args:
--   min_date: value date — earliest incremental target date
--   lookback_days: value int — LOOKBACK_TRADING_DAYS (510) trading days
    SELECT COALESCE(
        MIN(date),
        (SELECT MIN(date) FROM stats.index_basic_stats)
    ) AS lookback_date
    FROM (
        SELECT DISTINCT date
        FROM stats.index_basic_stats
        WHERE code IN (
            SELECT code FROM stats.sec_index_tags WHERE is_broad_market = TRUE
        )
          AND date < {{min_date}}::date
          AND close IS NOT NULL
        ORDER BY date DESC
        LIMIT {{lookback_days}}
    ) t
