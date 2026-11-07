-- Cache freshness probe — the latest (MAX) date of each major data source
-- in one round trip, taken from each family's IDENTITY table (not the
-- reconstructed *_daily LEFT-JOIN fan-out views: same MAX at a fraction of
-- the cost). The frontend compares these against what it holds to decide
-- whether cached data is stale.
-- Serves GET /api/cache/latest-dates.
-- args: none
    SELECT
      (SELECT MAX(date)          FROM stats.debt_identity)    AS debt,
      (SELECT MAX(date)          FROM stats.etf_identity)     AS etf_margin,
      (SELECT MAX(date)          FROM stats.index_identity)   AS index_daily,
      (SELECT MAX(date)          FROM stats.options_identity) AS options,
      (SELECT MAX(snapshot_date) FROM stats.sec_composition)  AS sec_composition,
      (SELECT MAX(date)          FROM stats.stock_identity)   AS stock_daily,
      (SELECT MAX(date) FROM stats.index_intraday_5min WHERE close IS NOT NULL) AS intraday_movements
