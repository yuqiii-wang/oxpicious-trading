-- The per-pool FULL calendars of the composites baseline table (cheap
-- DISTINCT read) — the bounded incremental load re-anchors each pool's
-- pivot onto them so the stride grid keeps the historical index-0
-- anchor.
-- args:
--   baseline_table: fragment — stats.industry_basic_stats
            SELECT DISTINCT pool_size, date FROM {{baseline_table}}
            WHERE mean_close IS NOT NULL
            ORDER BY pool_size, date
