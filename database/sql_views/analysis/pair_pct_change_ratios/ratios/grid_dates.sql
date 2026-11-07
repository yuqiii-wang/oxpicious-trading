-- pair_pct_change_ratios — stride-grid dates on the global index
-- calendar: every stride-th trading day (0-based idx % stride ==
-- stride - 1). The corr grid the ratios materialize on.
-- args:
--   stride: value int — grid stride in trading days

        WITH cal AS (
            SELECT DISTINCT date FROM stats.index_basic_stats
        ), g AS (
            SELECT date, ROW_NUMBER() OVER (ORDER BY date) - 1 AS idx
            FROM cal
        )
        SELECT date FROM g WHERE idx % {{stride}} = {{stride}} - 1
    