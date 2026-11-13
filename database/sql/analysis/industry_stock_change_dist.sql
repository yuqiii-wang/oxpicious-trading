-- ============================================================================
--  Industry Stock Change Distribution — per (industry, date) the number of
--  member stocks in each daily pct-change bucket (the Stock Attribution
--  audit on the Market Beta page).
--
--  Table: analysis.industry_stock_change_dist
--    PK: (industry_id, date, bucket)
--
--  PURPOSE
--    "Stock Attribution" mode on the Market Beta page audits, per
--    industry, HOW MANY member stocks rose / dropped and by how much on a
--    given date: e.g. "26 stocks in AERO_SPACE rose >= 7.5%". One row per
--    (industry_id, date, bucket) with stock_count; total_stocks is
--    denormalized onto every bucket row of the (industry, date) pair so
--    each row is self-sufficient for % display. All 12 buckets are stored
--    for every (industry, date) pair that has >= 1 mapped stock with a
--    non-NULL pct_change (zero-count buckets included) — the UI histogram
--    needs the uniform grid.
--
--  BUCKETS
--    bucket is the static.label_dim 'stock_change_bucket' SMALLINT code
--    (12 codes, semantic order drop-tail -> rise-tail; see the domain's
--    INSERT file under database/sql/static/label_dim/03_industry_aggregation.sql
--    and the _common.labels / label-codes.ts mirrors). Bucket k =
--    [edge[k-1], edge[k]) over edges -17.5 / -12.5 / -7.5 / -5 / -2.5 /
--    0 / 2.5 / 5 / 7.5 / 12.5 / 17.5 (percent) — the wings widen to 5pp
--    beyond +-7.5% where counts thin out. pct_change == 0 falls in bucket
--    6 ('0~2.5%', the rise side of the split).
--
--  MAPPING (stock -> industry)
--    stats.sec_classification rows type='stock', collapsed to ONE row per
--    code via DISTINCT ON (code) ORDER BY code, parent_index_is_primary
--    DESC, parent_index_weight DESC NULLS LAST (a stock may carry one row
--    per qualifying industry-index parent; the primary/max-weight row
--    wins). Stocks with no qualifying parent keep their fallback
--    name-classified row or land in industry_id='OTHER' (未分类) — those
--    rows are stored too so the audit's coverage is complete; the UI only
--    queries industries selected in the classification nav, so OTHER is
--    never rendered unless explicitly queried. The mapping is the CURRENT
--    snapshot applied across history (same limitation as the other
--    industry-grain analyses).
--
--  SOURCE
--    stats.stock_basic_stats.pct_change  (daily 涨跌幅 %, per (code, date))
--    stats.sec_classification            (stock -> industry_id/label)
--
--  POPULATION
--    analysis.market_beta.stock_attribution (internal step
--    run_stock_attribution, invoked from __main__ after hypes_and_drains).
--    Self-managing incremental: target dates = source dates missing from
--    this table (the stock daily build adds dates independently of the
--    cross_stats calendar the other steps follow). --force truncates and
--    rebuilds everything.
--
--  Register in analysis.analysis_identity (name='industry_stock_change_dist').
-- ============================================================================

-- ----------------------------------------------------------------------------
--  Table: analysis.industry_stock_change_dist
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: How many of each industry's member stocks rose or dropped and by how much, bucketed by daily pct change — the per-industry rise/drop breadth audit.
-- tags: industry, 行业, breadth, 广度, distribution, 分布, pct change, 涨跌幅, rise, 涨, drop, 跌, stock count, 个股数量, histogram, 直方图

DROP TABLE IF EXISTS analysis.industry_stock_change_dist;

CREATE TABLE IF NOT EXISTS analysis.industry_stock_change_dist (
    date                          DATE          NOT NULL,
    industry_id                   TEXT          NOT NULL,

    -- Daily pct-change bucket as its static.label_dim
    -- ('stock_change_bucket' domain) SMALLINT code: 0 '<-17.5%' ..
    -- 11 '>=17.5%' (semantic order drop tail -> rise tail).
    bucket                        SMALLINT      NOT NULL,

    -- Display label (denormalized from sec_classification at build time).
    industry_label                TEXT          NOT NULL DEFAULT '',

    -- Number of member stocks whose pct_change falls in this bucket on
    -- this date (0 rows are stored so the 12-bucket grid is uniform).
    stock_count                   INTEGER       NOT NULL,

    -- Denormalized total mapped stocks with non-NULL pct_change in this
    -- (industry_id, date) pair = SUM(stock_count) over the 12 buckets.
    -- Every bucket row of the pair carries the same value, so each row
    -- alone yields pct-of-industry = stock_count / total_stocks.
    total_stocks                  INTEGER       NOT NULL,

    CONSTRAINT pk_industry_stock_change_dist PRIMARY KEY (industry_id, date, bucket),
    CONSTRAINT chk_stock_change_dist_bucket CHECK (bucket BETWEEN 0 AND 11),
    CONSTRAINT chk_stock_change_dist_counts CHECK (stock_count >= 0 AND total_stocks >= 0)
) PARTITION BY HASH (industry_id);

-- Native hash partitions (8) keyed by industry_id — created via the shared
-- util (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'industry_stock_change_dist', 8);

-- Indexes:
--   1. Date-first lookup — the API resolves the latest date per industry
--      and fetches the 12 bucket rows for one (industry_id, date).
--   2. Cross-industry date slice (drives any future "all industries on
--      date X" audit view).
CREATE INDEX IF NOT EXISTS idx_stock_change_dist_date_industry
    ON analysis.industry_stock_change_dist (date, industry_id);

COMMENT ON TABLE  analysis.industry_stock_change_dist                IS 'Per-(industry_id, date, bucket) number of member stocks in each daily pct-change bucket — the Stock Attribution audit on the Market Beta page ("how many stocks in industry X rose >= 7.5% today"). bucket is the static.label_dim ''stock_change_bucket'' SMALLINT code (12 codes, drop tail -> rise tail; bucket k = [edge[k-1], edge[k]) over edges -17.5/-12.5/-7.5/-5/-2.5/0/2.5/5/7.5/12.5/17.5 percent, pct_change==0 falls in bucket 6). All 12 buckets are stored for every (industry, date) pair with >= 1 mapped stock (zero rows included). total_stocks is denormalized per pair. Mapping: stats.sec_classification type=''stock'' DISTINCT ON (code) primary-first. Source: stats.stock_basic_stats.pct_change. Built by analysis.market_beta.stock_attribution (self-managing incremental; --force truncate + rebuild).';
COMMENT ON COLUMN analysis.industry_stock_change_dist.bucket        IS 'Daily pct-change bucket as its static.label_dim (''stock_change_bucket'' domain) SMALLINT code: 0 ''<-17.5%'', 1 ''-17.5~-12.5%'', 2 ''-12.5~-7.5%'', 3 ''-7.5~-5%'', 4 ''-5~-2.5%'', 5 ''-2.5~0%'', 6 ''0~2.5%'', 7 ''2.5~5%'', 8 ''5~7.5%'', 9 ''7.5~12.5%'', 10 ''12.5~17.5%'', 11 ''>=17.5%''. Bucket k = [edge[k-1], edge[k]) (left-closed); bucket 0 is open below, bucket 11 closed above. Code order is semantic (drop tail -> rise tail) and doubles as the UI presentation order.';
COMMENT ON COLUMN analysis.industry_stock_change_dist.industry_label IS 'Display label (denormalized from stats.sec_classification at build time). ''未分类'' for industry_id=''OTHER'' (stocks with no industry-index parent).';
COMMENT ON COLUMN analysis.industry_stock_change_dist.stock_count   IS 'Number of member stocks whose stats.stock_basic_stats.pct_change (percent) falls in this bucket on this date. 0 rows are stored so the 12-bucket grid per (industry, date) is uniform.';
COMMENT ON COLUMN analysis.industry_stock_change_dist.total_stocks IS 'Denormalized total mapped stocks with non-NULL pct_change in this (industry_id, date) pair (= SUM(stock_count) over the pair''s 12 buckets). pct-of-industry = stock_count / total_stocks computable from any single row.';

-- ----------------------------------------------------------------------------
--  Register in analysis.analysis_identity
-- ----------------------------------------------------------------------------
INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('industry_stock_change_dist', 'industry_stock_change_dist', NULL, NOW(),
     'Per-(industry_id, date, bucket) number of member stocks in each daily pct-change bucket — the Stock Attribution audit on the Market Beta page ("how many stocks in industry X rose >= 7.5% today"). bucket = static.label_dim ''stock_change_bucket'' code (12 codes, drop tail -> rise tail; bucket k = [edge[k-1], edge[k]) over edges -17.5/-12.5/-7.5/-5/-2.5/0/2.5/5/7.5/12.5/17.5 percent). All 12 buckets stored per (industry, date) pair with >=1 mapped stock; total_stocks denormalized per pair. Mapping: stats.sec_classification type=''stock'' DISTINCT ON (code) primary-first. Source: stats.stock_basic_stats.pct_change. Built by analysis.market_beta.stock_attribution (self-managing incremental; --force truncate + rebuild).')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
