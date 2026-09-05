-- ============================================================================
--  Schema: analysis_composites
--
--  COMPOSITE analyses — multi-factor blends that combine several upstream
--  analyses into a single audited view. Each table cross-references two or
--  more base series (industry composite prices, benchmark index prices,
--  rolling stats) and stores per-window audit metrics.
--
--  Layout:
--    - 01_industry_corr_benchmark_offsets.sql —
--      analysis_composites.industry_corr_benchmark_offsets: opposite
--      industry correlations by benchmark offset. Industry MA trends are
--      offset by adding / subtracting a broad-market benchmark (rebased to
--      the industry's level at each window start), prices are recomputed
--      from the offset trends, and pairwise Pearson correlations are
--      audited over 20 / 60 / 255 trading-day windows next to the raw
--      (overall) correlation.
--
--  Population convention:
--    `python -m analyze.analysis_composites` computes the tables
--    incrementally (potential window END dates on the source calendar grid
--    not yet covered by a computed window end are upserted). `--force`
--    truncates + recomputes everything; `--industry ID[,ID...]` /
--    `--code CODE[,CODE...]` recompute + upsert only the pairs among the
--    chosen industries (UI refresh button).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS analysis_composites;

COMMENT ON SCHEMA analysis_composites IS 'Composite analyses — multi-factor blends combining several upstream series into single audited views. First table: industry_corr_benchmark_offsets (opposite industry correlations by benchmark offset — industry MA trends offset by ± a broad-market benchmark, prices recomputed from the offset trends, pairwise correlations audited over 20/60/255d windows). Populated by python -m analyze.analysis_composites (incremental / --force / --industry filtered).';
