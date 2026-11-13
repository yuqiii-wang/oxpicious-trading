-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each RSI reading's rank within the security's own full RSI history — where today's momentum sits historically.
-- tags: rsi, rank, 排名, momentum, 动量, history percentile, 历史分位




-- ----------------------------------------------------------------------------
--  Table: analysis.mov_ave_rsi_ranks  (per-code RSI history ranks)
--    One row per (sec_type, code, date) — mirrors analysis.mov_ave_rsi 1:1.
--    For each RSI window, stores the ordinal rank of that date's RSI WITHIN
--    THE CODE'S OWN FULL RSI HISTORY (every date whose rsi_{W}days is
--    non-null): rank 1 = the code's highest RSI reading for the window,
--    N = the lowest. Ties break by earlier date (order of appearance in the
--    date-sorted build frame). Ranks are dense 1..N, so MAX(rank) per
--    (sec_type, code, window) equals the count of ranked days and a
--    "top X%" cutoff is rank <= ceil(X% * MAX(rank)).
--
--  rank_rsi_{W}days is NULL wherever the window's RSI is NULL (the first
--  W-1 rows of a code's history — min_periods=W — plus fully-flat
--  gain/loss windows), hence the columns are nullable.
--
--  WHY WHOLESALE REBUILDS: every stored rank can shift whenever ANY new
--  day's RSI lands anywhere in the code's history (a new reading re-ranks
--  the whole distribution and moves every percentile denominator), so
--  incremental per-date upserts would update nearly every row on every
--  run. Populated by analysis.mov_ave_spread (steps/rsi_ranks.py) on every
--  run that recomputes RSI: DELETE the run's sec_type/code scope,
--  recompute from the same in-memory RSI frame (no extra DB round-trip),
--  CSV-COPY insert. Per-code semantics also keep the rebuild valid in
--  --code single-code mode (a per-date cross-sectional rank would need the
--  whole universe's frame, which a single-code run does not have). No FK
--  to mov_ave_rsi — the NO-FK derived-data design of
--  01_mov_ave_spreads_detail.sql.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS analysis.mov_ave_rsi_ranks CASCADE;
CREATE TABLE analysis.mov_ave_rsi_ranks (
    sec_type        SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock 3=industry
    code            TEXT         NOT NULL,
    date            DATE         NOT NULL,

    -- Per-code RSI history rank per window: 1 = highest RSI of the code's
    -- own history, N = lowest (earlier date wins ties); NULL where that
    -- window's RSI is NULL.
    rank_rsi_3days       INTEGER,
    rank_rsi_6days       INTEGER,
    rank_rsi_10days      INTEGER,
    rank_rsi_14days      INTEGER,
    rank_rsi_20days      INTEGER,
    rank_rsi_60days      INTEGER,

    CONSTRAINT pk_mov_ave_rsi_ranks PRIMARY KEY (code, sec_type, date)
) PARTITION BY HASH (code);

-- Native hash partitions (16) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p15.
-- Same layout as mov_ave_rsi so the wholesale DELETE per sec_type and the
-- per-code API reads walk the same partitioning.
SELECT public.create_hash_partitions('analysis', 'mov_ave_rsi_ranks', 16);

-- (sec_type, code, date) index — the sec_type-leading read shape the API
-- uses (one code's rank history per request); same rationale as the
-- mov_ave_rsi index above.
CREATE INDEX IF NOT EXISTS idx_mov_ave_rsi_ranks_sec_type_code_date
    ON analysis.mov_ave_rsi_ranks (sec_type, code, date);

COMMENT ON TABLE  analysis.mov_ave_rsi_ranks             IS 'Per-code RSI history ranks (3/6/10/14/20/60 days): the ordinal rank of each date''s RSI within the code''s own full RSI history — 1 = highest RSI reading, N = lowest, earlier date wins ties, dense 1..N so MAX(rank) per (code, window) is the count of ranked days. Mirrors analysis.mov_ave_rsi row-for-row; rank columns are NULL where that window''s RSI is NULL. Rebuilt wholesale per sec_type by analysis.mov_ave_spread (steps/rsi_ranks.py) on every run that recomputes RSI.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.sec_type    IS 'Security type as its static.label_dim (''sec_type'' domain) SMALLINT code: 0 = etf (ETF), 1 = index (CSI-style index), 2 = stock (individual equity), 3 = industry (composite industry).';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.code        IS 'Ticker. ETFs bare 6-digit (e.g. "510050"); indices bare code (e.g. "000300"); stocks keep their exchange suffix (e.g. "600008.SS").';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.date        IS 'Business date (trading day) — matches the mov_ave_rsi row being ranked.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.rank_rsi_3days  IS 'Rank of rsi_3days within the code''s own non-null rsi_3days history: 1 = highest, N = lowest, earlier date wins ties. NULL where rsi_3days is NULL. Dense 1..N.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.rank_rsi_6days  IS 'Rank of rsi_6days within the code''s own non-null rsi_6days history: 1 = highest, N = lowest, earlier date wins ties. NULL where rsi_6days is NULL. Dense 1..N.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.rank_rsi_10days IS 'Rank of rsi_10days within the code''s own non-null rsi_10days history: 1 = highest, N = lowest, earlier date wins ties. NULL where rsi_10days is NULL. Dense 1..N.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.rank_rsi_14days IS 'Rank of rsi_14days within the code''s own non-null rsi_14days history: 1 = highest, N = lowest, earlier date wins ties. NULL where rsi_14days is NULL. Dense 1..N.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.rank_rsi_20days IS 'Rank of rsi_20days within the code''s own non-null rsi_20days history: 1 = highest, N = lowest, earlier date wins ties. NULL where rsi_20days is NULL. Dense 1..N.';
COMMENT ON COLUMN analysis.mov_ave_rsi_ranks.rank_rsi_60days IS 'Rank of rsi_60days within the code''s own non-null rsi_60days history: 1 = highest, N = lowest, earlier date wins ties. NULL where rsi_60days is NULL. Dense 1..N. Computed by analysis.mov_ave_spread (steps/rsi_ranks.py) from the RSI step''s in-memory full-history frame — one whole-frame grouped rank per window (groupby(sec_type, code).rank(method="first", ascending=False)) on the cudf.pandas proxy.';
