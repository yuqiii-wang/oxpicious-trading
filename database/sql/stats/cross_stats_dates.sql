
-- ----------------------------------------------------------------------------
--  Dates map: one row per date loaded into cross_stats (pair grain drives
--  the map; industry grain shares the same dates by construction). Missing-
--  date detection queries this tiny map instead of scanning the main table.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The map of dates loaded into the cross-stats pair grain — the missing-date detector.
-- tags: cross stats, 跨标的数据, dates, 日期, reference, 参考

CREATE TABLE IF NOT EXISTS stats.cross_stats_dates (
    date DATE PRIMARY KEY
);
COMMENT ON TABLE  stats.cross_stats_dates IS 'One row per date loaded into stats.cross_stats (pair grain). Missing-date detection reads this map instead of scanning the hash-partitioned main table. Maintained by builds.cross_stats.';
