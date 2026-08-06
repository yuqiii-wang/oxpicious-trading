-- ============================================================================
--  analysis.industry_member_index_map
--
--  Pre-computed mapping of each industry to its NON-BROAD member indices,
--  with the composition-derived shared weights frozen at the latest
--  stats.sec_composition snapshot.
--
--  PURPOSE
--    Fast-tracking analysis.industry_attributions population. The
--    composition snapshot weights (industry_shared_weight,
--    benchmark_shared_weight) are CONSTANT across dates — they only change
--    when compositions are refreshed (monthly). Pre-computing them ONCE
--    in this mapping table lets the attributions INSERT be a simple
--    cross-join with stats.index_basic_stats dates, instead of recomputing
--    the expensive composition aggregation per date.
--
--  CONTENTS
--    One row per (industry_id, benchmark_code) where benchmark_code is a
--    NON-BROAD member index of the industry. Broad-market codes are
--    EXCLUDED (they are materialized in industry_attributions via the
--    broad-market INSERT from sec_alloc_perf_attribution).
--
--    industry_shared_weight  = SUM over stocks S held by M of
--                              (total_weight(S, industry) - M.weight_pct(S))
--                              = SUM over OTHER same-industry members N
--                              of N's weight on stocks shared with M.
--                              Self-pair (M, M) excluded by subtraction.
--
--    benchmark_shared_weight = M's weight on the UNION of industry member
--                              stocks. Since M is itself a member, M's
--                              stocks are a subset of the union, so this
--                              is typically ~100 (M's total weight on its
--                              own stocks).
--
--  POPULATION
--    analyze.industry_sentiments.attributions (step a6, TRUNCATE + INSERT
--    ... SELECT from sec_composition + sec_classification). Recomputed on
--    every attributions run (cheap — only ~235 rows).
--
--  Register in analysis.analysis_identity (name='industry_member_index_map').
-- ============================================================================

CREATE TABLE IF NOT EXISTS analysis.industry_member_index_map (
    industry_id              TEXT          NOT NULL,
    benchmark_code           TEXT          NOT NULL,

    -- SUM over other same-industry members N of N's weight on stocks
    -- shared with M (self-pair excluded). Can exceed 100.
    industry_shared_weight   NUMERIC(8,4)  NOT NULL DEFAULT 0,

    -- M's weight on the industry stock union (typically ~100 since M is
    -- fully contained in its own industry).
    benchmark_shared_weight  NUMERIC(8,4)  NOT NULL DEFAULT 0,

    CONSTRAINT pk_industry_member_index_map PRIMARY KEY
        (industry_id, benchmark_code)
);

COMMENT ON TABLE  analysis.industry_member_index_map IS 'Pre-computed mapping of each industry to its NON-BROAD member indices, with composition-derived shared weights frozen at the latest sec_composition snapshot. Used to fast-track analysis.industry_attributions population (weights are constant across dates; only recomputed when compositions change). Built by analyze.industry_sentiments.attributions (step a6, TRUNCATE + INSERT...SELECT).';
COMMENT ON COLUMN analysis.industry_member_index_map.industry_id IS 'Subject industry_id (from stats.sec_classification type=''index'').';
COMMENT ON COLUMN analysis.industry_member_index_map.benchmark_code IS 'NON-BROAD member index code of the industry. Broad-market codes are EXCLUDED (already materialized in industry_attributions via the broad-market INSERT).';
COMMENT ON COLUMN analysis.industry_member_index_map.industry_shared_weight IS 'SUM over other same-industry members N of N''s weight on stocks shared with M. Self-pair (M, M) excluded by subtracting M''s own weight from the industry total. Can exceed 100 (sum of multiple member portfolios).';
COMMENT ON COLUMN analysis.industry_member_index_map.benchmark_shared_weight IS 'M''s weight on the UNION of industry member stocks (latest sec_composition snapshot). Typically ~100 since M is fully contained in its own industry.';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('industry_member_index_map', 'industry_member_index_map', NULL, NOW(),
     'Pre-computed mapping of each industry to its NON-BROAD member indices, with composition-derived shared weights (industry_shared_weight, benchmark_shared_weight) frozen at the latest sec_composition snapshot. Used to fast-track analysis.industry_attributions population. Built by analyze.industry_sentiments.attributions (step a6, TRUNCATE + INSERT...SELECT).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
