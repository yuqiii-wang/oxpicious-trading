-- Table: sec_importance_ranks
--   Per-code processing-importance rank 0-9 (0 = most important), WITHIN
--   each sec_type -- every sec type (index / etf / stock) has its own full
--   0-9 scale. sec_type is the label_dim SMALLINT code (etf=0 index=1
--   stock=2); `exchange` is the download-service routing key (mirrors
--   sec_classification.exchange, plus the curated 'SZSE' published-index
--   marker). Rebuilt monthly (first run of each month) by
--   builds.classification.importance. Consumed by downloads / builds /
--   analysis to enumerate codes rank 0 -> 9 within their type so the most
--   important securities are fetched/computed first when a run is
--   interrupted or throttled (the `_common.importance` ordering helpers),
--   and by the downloads tier to assign each code's download service by
--   its exchange.
--
--   TOP 2 RANKS ARE HAND-CURATED, not computed: ranks 0 and 1 of every
--   sec_type come from hardcoded code lists in
--   database/sec_statics/importance_rules/<sec_type>_<rank>_rank.json
--   (index_0_rank.json, etf_1_rank.json, ...) -- the builder reads them
--   from there and assigns those ranks verbatim (a curated code overrides
--   whatever the quantitative ladder would say). The computed ladder
--   NEVER assigns ranks 0-1 -- those bands are NULL from the DB side and
--   are only filled by the JSON overlay.
--
--   Computed bands 2-9 (trailing ~200 calendar days of trading amount;
--   NTILE bands are equal-population sextiles of avg trading amount among
--   that type's non-curated active population):
--
--     INDEX 2-7  NTILE(6) sextiles of avg daily trading_amount among
--                active real non-curated indices, most liquid first
--                (broad-market indices not listed in the JSONs rank here
--                by turnover, like cross_stats' benchmark ranking)
--             8  active real index with no trading-amount data
--             9  inactive indices + DUMMY synthetic indices
--
--     ETF   2-7  NTILE(6) sextiles of avg daily trading_amount among
--                active non-curated ETFs, most liquid first
--             8  active ETF with no trading-amount data
--             9  inactive ETFs
--
--     STOCK   2  max index weight >= 7%
--             3  >= 5%   (cumulative >= 5%: the heavyweight cohort)
--             4  >= 3%
--             5  >= 2%   (the classification STOCK_WEIGHT_THRESHOLD)
--             6  >= 1% OR held by any ETF (latest per-ETF snapshot)
--             7  appears in any index composition (weight < 1%)
--             8  active but in no index/ETF composition
--             9  inactive stocks
--
--   Stock weights are the max weight_pct across every index's LATEST
--   composition snapshot (point-in-time per index, INCLUDES broad indices
--   -- unlike sec_classification parent rows). ETF broad-parent uses ANY
--   classification row of the ETF (parent_index_is_primary is name-match
--   dependent and misses e.g. 588000 -> 000688). Whole table is TRUNCATE +
--   rebuilt monthly; as_of records the build date.
--
--   EXCLUSIONS: codes with exclusion scope 'all'
--   (database/sec_statics/exclusion_rules/<sec_type>_excluded.json --
--   delisted stocks/ETFs, dead indices) carry NO row here: the monthly
--   build drops them, so rank-ordered sweeps never enumerate them.
--   Service-scoped exclusions (csindex / csindex_pe / stream) stay
--   ranked; the affected download services skip them via
--   database.sec_statics.exclusion_rules_store.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each security's processing-importance rank within its type — the order the platform walks the universe.
-- tags: importance, 重要性, rank, 排名, reference, 参考

CREATE TABLE IF NOT EXISTS stats.sec_importance_ranks (
    code                    TEXT          NOT NULL,
    -- label_dim domain 'sec_type' (01_label_dim + _common.labels
    -- SEC_TYPE_CODE + label-codes.ts): etf=0, index=1, stock=2.
    sec_type                SMALLINT      NOT NULL,
    -- Download-service routing key, mirroring sec_classification.exchange
    -- (SZ / SS / GEM / STAR / BJ / HK / OVERSEAS; NULL for DUMMY indices)
    -- PLUS one hand-curated marker the curated JSONs may set: 'SZSE' =
    -- SZSE-exchange-PUBLISHED index (399001 深证成指 family) that
    -- csindex.com.cn does NOT serve — the SZSE downloader/streamer owns it.
    exchange                TEXT,
    importance_rank         SMALLINT      NOT NULL CHECK (importance_rank BETWEEN 0 AND 9),

    -- audit / drill-down metrics behind the band assignment
    avg_trading_amount      NUMERIC(24,4),
    liquidity_pct           NUMERIC(5,4),
    max_parent_weight_pct   NUMERIC(8,4),
    etf_held                BOOLEAN,
    is_broad_market         BOOLEAN,
    aum_yi                  NUMERIC(12,4),
    as_of                   DATE          NOT NULL,

    CONSTRAINT pk_sec_importance_ranks PRIMARY KEY (code)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code -- created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'sec_importance_ranks', 8);

COMMENT ON TABLE  stats.sec_importance_ranks                   IS 'Per-code processing-importance rank 0-9 (0 = most important), WITHIN each sec_type (SMALLINT label_dim code: etf=0 index=1 stock=2) -- every type has its own full 0-9 scale (see the per-type band ladders in the DDL header). Rebuilt monthly (truncate + rebuild) by builds.classification.importance from sec_classification + sec_index_tags + trading amounts + sec_composition weights. Downloads/builds/analysis enumerate codes rank 0 -> 9 via _common.importance so interrupted runs have covered the important codes first; the exchange column assigns each code''s download service by exchange.';
COMMENT ON COLUMN stats.sec_importance_ranks.code              IS 'Security code, same format as sec_classification.code (ETF/index bare 6-digit; stock WITH exchange suffix, e.g. 000001.SZ). One row per code.';
COMMENT ON COLUMN stats.sec_importance_ranks.sec_type          IS 'Security type code, label_dim domain ''sec_type'' (mirror of sec_classification.type): etf=0, index=1, stock=2. Decoded via static.label_dim / _common.labels SEC_TYPE_LABEL. The importance_rank scale is PER TYPE -- never compare ranks across types.';
COMMENT ON COLUMN stats.sec_importance_ranks.exchange          IS 'Download-service routing key (downloads tier reads it via _common.importance, cache-first). Mirrors sec_classification.exchange per code (SZ / SS / GEM / STAR / BJ / HK / OVERSEAS; NULL for DUMMY synthetic indices), EXCEPT the curated importance_rules JSONs may override a code with ''SZSE'' = SZSE-exchange-PUBLISHED index that csindex.com.cn does not serve (399001 深证成指 family) — the SZSE downloader/streamer owns those codes, every csindex service skips them. Editing a curated code''s exchange in database/sec_statics/importance_rules re-assigns its download service at the next monthly build.';
COMMENT ON COLUMN stats.sec_importance_ranks.importance_rank   IS 'Importance band 0-9 within the code''s sec_type, 0 = most important. Ranks 0-1 are HAND-CURATED from database/sec_statics/importance_rules/<sec_type>_<rank>_rank.json (curated codes override the ladder). Computed bands: INDEX 2-7 turnover sextiles, 8 no-amount, 9 inactive/dummy; ETF 2-7 turnover sextiles, 8 no-amount, 9 inactive; STOCK 2-5 max index weight bands (>=7/5/3/2%), 6 >=1% or ETF-held, 7 any composition member, 8 no membership, 9 inactive. NOT a label_dim vocabulary -- an ordinal banding documented here, decoded by range semantics only.';
COMMENT ON COLUMN stats.sec_importance_ranks.avg_trading_amount IS 'Mean daily trading amount in yuan over the trailing ~200 calendar days: index -> stats.index_basic_stats.trading_amount; etf -> stats.etf_liquidity_margin.trading_amount. NULL for stocks and securities with no amount data.';
COMMENT ON COLUMN stats.sec_importance_ranks.liquidity_pct     IS 'Percentile (0-1) of avg_trading_amount within the ACTIVE same-sec_type population at build time. NULL when no amount data.';
COMMENT ON COLUMN stats.sec_importance_ranks.max_parent_weight_pct IS 'Stock only: max weight_pct across every index''s LATEST composition snapshot (stats.sec_composition source_type=''index'', point-in-time per index; includes BROAD indices, unlike sec_classification parent rows). NULL for etf/index.';
COMMENT ON COLUMN stats.sec_importance_ranks.etf_held          IS 'Stock only: TRUE when the stock appears in ANY ETF''s latest composition snapshot (stats.sec_composition source_type=''etf'', per-ETF latest). NULL for etf/index.';
COMMENT ON COLUMN stats.sec_importance_ranks.is_broad_market   IS 'Index: sec_index_tags.is_broad_market. ETF: TRUE when the tracking index (ANY classification parent row) is broad-market. NULL for stocks.';
COMMENT ON COLUMN stats.sec_importance_ranks.aum_yi            IS 'ETF only: net asset value in yi yuan (100M) copied from sec_classification.aum_yi at build time. NULL when the classification CSV carried no AUM (only ~200 of ~2100 ETFs have it).';
COMMENT ON COLUMN stats.sec_importance_ranks.as_of             IS 'Build date (monthly snapshot anchor). Whole table is rebuilt when a new month first runs the builder.';

CREATE INDEX IF NOT EXISTS idx_sec_importance_ranks_rank
    ON stats.sec_importance_ranks (sec_type, importance_rank, code);

CREATE INDEX IF NOT EXISTS idx_sec_importance_ranks_exchange
    ON stats.sec_importance_ranks (exchange)
    WHERE exchange IS NOT NULL;
