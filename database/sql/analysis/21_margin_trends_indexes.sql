-- ============================================================================
--  Margin Trends lookup indexes — data_viz /api/analysis/margin-trends/*
--
--  The Margin Trends page filters analysis.margin_index_series by industry_id
--  (themes items, industry-series, trends CTE). The table is hash-partitioned
--  by index_code with PK (index_code, date), so industry_id had NO index path:
--  every industry-scoped query parallel-scanned all 8 partitions (~325k rows)
--  to find one industry's ~1.6k rows — ~200-340ms per endpoint, paid on every
--  industry select (2026-09-29).
--
--  The themes nav META_SQL also lateral-probes stats.sec_classification by
--  (industry_id, type) once per industry; the existing
--  idx_sec_classification_sector_industry leads with sector_id, so those
--  probes were per-partition seq scans (~70 loops).
--
--  The trends endpoint then filters analysis.margin_changes by
--  sec_type='index' and joins on code; the PK leads with code, so sec_type
--  had no index path and every request seq-scanned all 8 partitions
--  (~360k rows, ~21k matching). Mirrors idx_pe_sec_type_code (20_nav).
--
--  Write cost on analysis.margins' bulk COPY loads is negligible relative to
--  the truncate-then-recompute rebuild.
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_margin_index_series_industry
    ON analysis.margin_index_series (industry_id, index_code);

CREATE INDEX IF NOT EXISTS idx_sec_classification_industry_type
    ON stats.sec_classification (industry_id, type);

CREATE INDEX IF NOT EXISTS idx_margin_changes_sec_type_code
    ON analysis.margin_changes (sec_type, code);
