
-- NOTE: stats.sec_owners has been MOVED to 11_sec_info.sql and is now populated
-- by `python -m builds.sec_info` (not build_classification.py).  The logical
-- owner_id reference on sec_classification is still matched in-memory by
-- builds.classification via builds.classification.sector_industry.owners
-- (load_owners + build_owner_matchers + match_etf_owner) — no DB sec_owners
-- table is needed at classification time.


-- ----------------------------------------------------------------------------
-- Table: sec_similars
--   Per (composition_snapshot_date, code, sec_type) top-5 similar codes,
--   top-5 similar industry-classified peer codes (from DIFFERENT industries),
--   and top-5 dissimilar industry-classified peer codes by mutual shared
--   composition weight. Built by builds.index._sec_similars from
--   stats.sec_composition + stats.sec_classification.
--
--   sec_type: 'index' and 'etf'. PK includes sec_type so both coexist.
--   Each sec_type is computed independently (index-vs-index, ETF-vs-ETF).
--
--   `date` is the COMPOSITION snapshot_date (quarterly/semi-annual), NOT a
--   trading day — downstream consumers look up the latest row with
--   date <= trading_date per code (same carry-forward pattern as
--   index_exts.stock_num). No FK to index_identity: composition dates do
--   not align with trading dates.
--
--   Sharing weight is MUTUAL and symmetric:
--     shared_weight_a = SUM(A.weight_pct) over stocks held by BOTH A and B
--     shared_weight_b = SUM(B.weight_pct) over stocks held by BOTH A and B
--     mutual_sharing_weight = (shared_weight_a + shared_weight_b) / 2
--   Ranking by this average is symmetric: A's view of B equals B's view of A.
--   Comparison uses each B's LATEST composition snapshot with snapshot_date
--   <= A's snapshot date (point-in-time). stock_code is matched on
--   LEFT(stock_code, 6) to ignore .SS/.SZ suffixes.
--
--   Industry-classified peer columns: the "industry_code" columns store SEC
--   CODES (individual index/ETF codes), NOT industry_ids. The "industry"
--   qualifier means the peer pool is filtered to securities where
--   sec_classification.is_industry_not_strategy=TRUE. The subject itself can
--   be either industry-primary or strategy-primary. Comparison is code-vs-code
--   (same mutual formula), just restricted to industry-classified peers.
--
--   Similar industry-classified peers — DISTINCT-INDUSTRY greedy selection:
--   The 1st pick is the peer with the highest mutual sharing weight. The 2nd
--   pick is the best peer from a DIFFERENT industry_id than the 1st. The 3rd
--   pick is the best peer from a different industry_id than both the 1st and
--   2nd, and so on through the 5th. Implemented as: rank peers within each
--   industry (best per industry wins), then rank those best-per-industry
--   peers by mutual DESC. The top-5 are the 5 most-similar peers from 5
--   different industries.
--
--   Dissimilar industry-classified peers: ranked by LOWEST mutual sharing
--   weight (ASC). Tie-breaker when sharing weights are equal (especially zero):
--   prefer peers in a DIFFERENT sector than the subject, then different
--   industry, maximizing classification distance.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each code's closest peers by shared composition — top similar codes and similar-industry / dissimilar industry neighbors per snapshot.
-- tags: similar codes, 相似标的, composition, 成分, peers, 同业

CREATE TABLE IF NOT EXISTS stats.sec_similars (
    date                      DATE          NOT NULL,
    code                      TEXT          NOT NULL,
    sec_type                  TEXT          NOT NULL CHECK (sec_type IN ('etf', 'index')),

    similar_1st_code_by_sharing_weights TEXT,
    similar_2nd_code_by_sharing_weights TEXT,
    similar_3rd_code_by_sharing_weights TEXT,
    similar_4th_code_by_sharing_weights TEXT,
    similar_5th_code_by_sharing_weights TEXT,
    similar_1st_code_sharing_weight_pct NUMERIC(8,4),
    similar_2nd_code_sharing_weight_pct NUMERIC(8,4),
    similar_3rd_code_sharing_weight_pct NUMERIC(8,4),
    similar_4th_code_sharing_weight_pct NUMERIC(8,4),
    similar_5th_code_sharing_weight_pct NUMERIC(8,4),

    similar_1st_industry_code_by_sharing_weights TEXT,
    similar_2nd_industry_code_by_sharing_weights TEXT,
    similar_3rd_industry_code_by_sharing_weights TEXT,
    similar_4th_industry_code_by_sharing_weights TEXT,
    similar_5th_industry_code_by_sharing_weights TEXT,
    similar_1st_industry_code_sharing_weight_pct NUMERIC(8,4),
    similar_2nd_industry_code_sharing_weight_pct NUMERIC(8,4),
    similar_3rd_industry_code_sharing_weight_pct NUMERIC(8,4),
    similar_4th_industry_code_sharing_weight_pct NUMERIC(8,4),
    similar_5th_industry_code_sharing_weight_pct NUMERIC(8,4),

    dissimilar_1st_industry_code_by_sharing_weights TEXT,
    dissimilar_2nd_industry_code_by_sharing_weights TEXT,
    dissimilar_3rd_industry_code_by_sharing_weights TEXT,
    dissimilar_4th_industry_code_by_sharing_weights TEXT,
    dissimilar_5th_industry_code_by_sharing_weights TEXT,
    dissimilar_1st_industry_code_sharing_weight_pct NUMERIC(8,4),
    dissimilar_2nd_industry_code_sharing_weight_pct NUMERIC(8,4),
    dissimilar_3rd_industry_code_sharing_weight_pct NUMERIC(8,4),
    dissimilar_4th_industry_code_sharing_weight_pct NUMERIC(8,4),
    dissimilar_5th_industry_code_sharing_weight_pct NUMERIC(8,4),

    CONSTRAINT pk_sec_similars PRIMARY KEY (code, date, sec_type)
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'sec_similars', 8);

COMMENT ON TABLE  stats.sec_similars                            IS 'Per (composition_snapshot_date, code, sec_type) top-5 similar codes + top-5 similar (distinct-industry) / dissimilar industry-classified peer codes by MUTUAL shared composition weight. Built by builds.index._sec_similars from stats.sec_composition + stats.sec_classification. `date` is the composition snapshot_date (NOT a trading day). sec_type: index and etf (both populated). Similar industry peers are greedily selected from DIFFERENT industry_ids.';
COMMENT ON COLUMN stats.sec_similars.date                      IS 'COMPOSITION snapshot_date from stats.sec_composition. One row per (snapshot_date, code, sec_type) — NOT a trading day. Downstream consumers look up the latest row with date <= trading_date per code (same carry-forward pattern as index_exts.stock_num).';
COMMENT ON COLUMN stats.sec_similars.code                      IS 'Subject code (e.g. 000300, 931382 for index; 510050 for ETF). The security whose composition we are finding similars FOR.';
COMMENT ON COLUMN stats.sec_similars.sec_type                  IS 'Security type: index or etf. Discriminates index-vs-index vs ETF-vs-ETF similarity computations. Each sec_type is computed independently from sec_composition with matching source_type.';
COMMENT ON COLUMN stats.sec_similars.similar_1st_code_by_sharing_weights IS 'Code of the most-similar peer (highest mutual_sharing_weight). NULL when no other code shares any constituent stock.';
COMMENT ON COLUMN stats.sec_similars.similar_2nd_code_by_sharing_weights IS 'Code of the 2nd-most-similar peer by mutual_sharing_weight. NULL when fewer than 2 peers share constituents.';
COMMENT ON COLUMN stats.sec_similars.similar_3rd_code_by_sharing_weights IS 'Code of the 3rd-most-similar peer by mutual_sharing_weight. NULL when fewer than 3 peers share constituents.';
COMMENT ON COLUMN stats.sec_similars.similar_1st_code_sharing_weight_pct IS 'MUTUAL shared weight between the subject and the 1st-similar peer: (SUM(subject.weight_pct) + SUM(peer.weight_pct)) / 2 over constituent stocks held by BOTH. Symmetric. Range ~[0,100]; can slightly exceed 100 due to source-data rounding. Computed from each code''s LATEST composition snapshot <= this row''s date (point-in-time).';
COMMENT ON COLUMN stats.sec_similars.similar_2nd_code_sharing_weight_pct IS 'MUTUAL shared weight with the 2nd-similar peer. See similar_1st_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.similar_3rd_code_sharing_weight_pct IS 'MUTUAL shared weight with the 3rd-similar peer. See similar_1st_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.similar_1st_industry_code_by_sharing_weights IS 'SEC CODE of the most-similar INDUSTRY-CLASSIFIED peer (highest mutual_sharing_weight). "industry" means the peer pool is filtered to securities where sec_classification.is_industry_not_strategy=TRUE. The subject itself can be industry- or strategy-primary. Stores a code (e.g. 000300 for index, 510050 for ETF), NOT an industry_id. NULL when no industry-classified peer shares constituents. The 5 similar industry peers are from 5 DIFFERENT industry_ids (distinct-industry greedy selection).';
COMMENT ON COLUMN stats.sec_similars.similar_2nd_industry_code_by_sharing_weights IS 'SEC CODE of the 2nd-most-similar industry-classified peer, from a DIFFERENT industry_id than the 1st. See similar_1st_industry_code_by_sharing_weights.';
COMMENT ON COLUMN stats.sec_similars.similar_3rd_industry_code_by_sharing_weights IS 'SEC CODE of the 3rd-most-similar industry-classified peer, from a DIFFERENT industry_id than the 1st and 2nd. See similar_1st_industry_code_by_sharing_weights.';
COMMENT ON COLUMN stats.sec_similars.similar_1st_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 1st-similar industry-classified peer. Same formula as similar_1st_code_sharing_weight_pct, just restricted to industry-classified peers.';
COMMENT ON COLUMN stats.sec_similars.similar_2nd_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 2nd-similar industry-classified peer. See similar_1st_industry_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.similar_3rd_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 3rd-similar industry-classified peer. See similar_1st_industry_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_1st_industry_code_by_sharing_weights IS 'SEC CODE of the most-DISSIMILAR industry-classified peer (LOWEST mutual_sharing_weight). Tie-breaker: prefer different sector than the subject, then different industry, maximizing classification distance. NULL when fewer than 1 industry-classified peer exists.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_2nd_industry_code_by_sharing_weights IS 'SEC CODE of the 2nd-most-dissimilar industry-classified peer. See dissimilar_1st_industry_code_by_sharing_weights.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_3rd_industry_code_by_sharing_weights IS 'SEC CODE of the 3rd-most-dissimilar industry-classified peer. See dissimilar_1st_industry_code_by_sharing_weights.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_1st_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 1st-DISSIMILAR industry-classified peer (lowest). See similar_1st_industry_code_sharing_weight_pct for the weight definition; dissimilars rank by LOWEST mutual.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_2nd_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 2nd-dissimilar industry-classified peer. See dissimilar_1st_industry_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_3rd_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 3rd-dissimilar industry-classified peer. See dissimilar_1st_industry_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.similar_4th_code_by_sharing_weights IS 'Code of the 4th-most-similar peer by mutual_sharing_weight. NULL when fewer than 4 peers share constituents.';
COMMENT ON COLUMN stats.sec_similars.similar_5th_code_by_sharing_weights IS 'Code of the 5th-most-similar peer by mutual_sharing_weight. NULL when fewer than 5 peers share constituents.';
COMMENT ON COLUMN stats.sec_similars.similar_4th_code_sharing_weight_pct IS 'MUTUAL shared weight with the 4th-similar peer. See similar_1st_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.similar_5th_code_sharing_weight_pct IS 'MUTUAL shared weight with the 5th-similar peer. See similar_1st_code_sharing_weight_pct.';

COMMENT ON COLUMN stats.sec_similars.similar_4th_industry_code_by_sharing_weights IS 'SEC CODE of the 4th-most-similar INDUSTRY-CLASSIFIED peer (by mutual_sharing_weight). NULL when fewer than 4 industry-classified peers share constituents. Similar industry peers are selected from distinct industry_ids.';
COMMENT ON COLUMN stats.sec_similars.similar_5th_industry_code_by_sharing_weights IS 'SEC CODE of the 5th-most-similar INDUSTRY-CLASSIFIED peer (by mutual_sharing_weight). NULL when fewer than 5 industry-classified peers share constituents. Similar industry peers are selected from distinct industry_ids.';
COMMENT ON COLUMN stats.sec_similars.similar_4th_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 4th-similar industry-classified peer. Same formula as similar_1st_industry_code_sharing_weight_pct.';
COMMENT ON COLUMN stats.sec_similars.similar_5th_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 5th-similar industry-classified peer. Same formula as similar_1st_industry_code_sharing_weight_pct.';

COMMENT ON COLUMN stats.sec_similars.dissimilar_4th_industry_code_by_sharing_weights IS 'SEC CODE of the 4th-most-dissimilar industry-classified peer (LOWEST mutual_sharing_weight). NULL when fewer than 4 industry-classified peers exist.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_5th_industry_code_by_sharing_weights IS 'SEC CODE of the 5th-most-dissimilar industry-classified peer (LOWEST mutual_sharing_weight). NULL when fewer than 5 industry-classified peers exist.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_4th_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 4th-DISSIMILAR industry-classified peer (lowest). See similar_1st_industry_code_sharing_weight_pct for the weight definition.';
COMMENT ON COLUMN stats.sec_similars.dissimilar_5th_industry_code_sharing_weight_pct IS 'MUTUAL shared weight with the 5th-DISSIMILAR industry-classified peer (lowest). See similar_1st_industry_code_sharing_weight_pct for the weight definition.';
