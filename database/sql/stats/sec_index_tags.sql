
-- ----------------------------------------------------------------------------
--  Sec Index Tags — multi-classification for indices
--  An index may carry MULTIPLE (sector_id, industry_id) tags, enabling
--  multi-faceted browsing (e.g. "央企红利" is both DIV/DIV_SOE and
--  BROAD/BROAD_SOE).  The PRIMARY classification remains in
--  sec_classification (sector_id/industry_id columns); this table stores
--  the full set of tags for filtering/grouping.
--
--  Populated by build_classification.py from database/sec_statics/sec_classification.json `tags`
--  arrays.  Truncated + rebuilt every run.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The extra sector/industry tags an index carries beyond its primary classification.
-- tags: index, 指数, tags, 标签, classification, 分类, sector, 板块, industry, 行业

CREATE TABLE IF NOT EXISTS stats.sec_index_tags (
    code                      TEXT          NOT NULL,
    sector_id                 TEXT          NOT NULL,
    industry_id               TEXT          NOT NULL,
    is_broad_market           BOOL,
    -- Denormalized from sec_classification; context for the
    -- is_broad_market derivation.
    is_industry_not_strategy  BOOL NOT NULL DEFAULT TRUE,
    CONSTRAINT pk_sec_index_tags PRIMARY KEY (code, sector_id, industry_id)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'sec_index_tags', 8);

COMMENT ON TABLE  stats.sec_index_tags              IS 'Multi-classification tags for indices. Each index can have multiple (sector_id, industry_id) pairs. Primary tag matches sec_classification.sector_id/industry_id.';
COMMENT ON COLUMN stats.sec_index_tags.code         IS 'Index code (bare 6-digit, e.g. 000300). References sec_classification.code where type=''index''.';
COMMENT ON COLUMN stats.sec_index_tags.sector_id    IS 'L1 sector id (e.g. DIV, MIL, AERO, BROAD, TECH).';
COMMENT ON COLUMN stats.sec_index_tags.industry_id  IS 'L2 industry id (e.g. DIV_SOE, MIL_DEFENSE, AERO_SPACE, BROAD_CSI300, BROAD_SSE50).';
COMMENT ON COLUMN stats.sec_index_tags.is_broad_market IS 'TRUE iff this index''s PRIMARY classification is the BROAD strategy (is_industry_not_strategy = FALSE AND the primary sector_id = ''BROAD''). Since sector_id now carries EITHER industry OR strategy based on is_industry_not_strategy, a strategy-primary index has sector_id=''BROAD'' directly. Industry-primary indices whose secondary tag happens to be BROAD (e.g. 中证银行 → FIN/BANKS primary + BROAD_CSI secondary tag) do NOT set this flag on any row. An index is considered broad-market if ANY of its tags has is_broad_market=TRUE. Populated by build_classification.py. BROAD themes are flagship index series (BROAD_SSE50/180/380, BROAD_CSI300/500/800/1000/2000, BROAD_CSI_A, BROAD_GEM, BROAD_STAR, BROAD_BSE, BROAD_TECH_INNOV, plus generic BROAD_SSE/BROAD_CSI/BROAD_SZSE catch-alls); each broad index carries exactly ONE BROAD theme (the most specific match wins, catch-alls dropped on collision).';

CREATE INDEX IF NOT EXISTS idx_sec_index_tags_sector_industry
    ON stats.sec_index_tags (sector_id, industry_id);
