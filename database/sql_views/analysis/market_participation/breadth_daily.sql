-- Market Participation pipeline (analysis.market_participation) —
-- per-date advance/decline breadth over the WHOLE stock universe:
-- sign counts of pct_change, the median (percentile_cont 0.5) and mean
-- of the day's non-NULL changes. NULL pct_change rows (suspended /
-- placeholder) drop out of every count and statistic.
-- args: none
    SELECT sbs.date,
           extract(epoch from sbs.date)::float8 AS date_epoch,
           count(*) FILTER (WHERE sbs.pct_change > 0)::float8 AS adv_count,
           count(*) FILTER (WHERE sbs.pct_change < 0)::float8 AS dec_count,
           count(*) FILTER (WHERE sbs.pct_change = 0)::float8 AS flat_count,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY sbs.pct_change)::float8 AS median_pct_change,
           avg(sbs.pct_change)::float8 AS mean_pct_change
    FROM stats.stock_basic_stats sbs
    WHERE sbs.pct_change IS NOT NULL
    GROUP BY sbs.date
    ORDER BY sbs.date
