-- ============================================================================
--  Sec Board Map — listing-board identity / board-mix per security
--  (stats.sec_board_map)
--
--  ONE board tag source for the UI's code-trend header:
--    stock — exactly ONE row, pct = 100: the stock's own listing board
--            (MAIN / STAR / GEM / BSE).
--    etf   — one row per board present among the ETF's LATEST composition
--            snapshot constituents, pct = composition-weight share
--            (SUM(weight_pct of that board's stocks) / SUM(weight_pct of
--            all constituents with a known board) * 100).
--    index — same as etf (bare 6-digit code).
--
--  Board values mirror stats.stock_identity.board:
--    MAIN — SSE/SZSE main board (incl. former SME board 002/003)
--    STAR — 科创板 (688/689.SS)
--    GEM  — 创业板 (30xxxx.SZ)
--    BSE  — Beijing Stock Exchange (all .BJ stocks)
--
--  Stock boards come from the LATEST stats.stock_identity row per code;
--  rows whose board is NULL/empty fall back to the deterministic code-
--  prefix rule (same rules that seed the identity column — verified 0
--  violations against the populated rows), so stream-loader gaps don't
--  punch holes in the mix denominators.
--
--  ETF/index codes with no composition snapshot (bond/money ETFs, new
--  funds) get NO rows — the UI simply renders no tag.
--
--  Populated by THREE builds, each owning its own slice (shared helpers in
--  builds/_commons/board_map.py):
--    builds.stock  — stock rows (own listing board, pct=100)
--    builds.etf    — etf mixes (latest own snapshot, tracking-index fallback)
--    builds.index  — index mixes (latest own snapshot)
--  Every run fully recomputes its slice into a temp table, then writes back
--  incrementally (missing codes; mixes also when their latest composition
--  snapshot is newer than the stored one).  A build's --force rebuilds its
--  whole slice.  The etf/index mixes join the stock-board lookup staged on
--  the same connection, so no cross-build ordering is required.
-- ============================================================================

CREATE TABLE IF NOT EXISTS stats.sec_board_map (
    code                      TEXT          NOT NULL,
    sec_type                  TEXT          NOT NULL DEFAULT '',
    board                     TEXT          NOT NULL,
    pct                       NUMERIC(8,4)  NOT NULL DEFAULT 0,
    n_stocks                  INTEGER       NOT NULL DEFAULT 0,
    snapshot_date             DATE,

    CONSTRAINT pk_sec_board_map PRIMARY KEY (code, board),
    CONSTRAINT chk_sec_board_map_board CHECK (board IN ('MAIN', 'STAR', 'GEM', 'BSE')),
    CONSTRAINT chk_sec_board_map_type CHECK (sec_type IN ('stock', 'etf', 'index'))
) PARTITION BY HASH (code);

-- Native hash partitions (8) keyed by code — created via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('stats', 'sec_board_map', 8);

COMMENT ON TABLE  stats.sec_board_map               IS 'Listing-board identity/mix per security: stocks carry exactly ONE row (pct=100, own listing board); ETFs/indices carry one row per board present in their LATEST composition snapshot, pct = composition-weight share. Populated by builds.board_map; consumed by the code-trend header board tag.';
COMMENT ON COLUMN stats.sec_board_map.code          IS 'Security code — stock/ETF WITH exchange suffix (000001.SZ, 510050.SS), index bare 6-digit (000300). Matches sec_classification.code / sec_composition.code conventions.';
COMMENT ON COLUMN stats.sec_board_map.sec_type      IS 'Security type discriminator: stock, etf, or index. Carried so the API can filter without a classification join.';
COMMENT ON COLUMN stats.sec_board_map.board         IS 'Listing board: MAIN (SSE/SZSE main board), STAR (科创板 688/689.SS), GEM (创业板 30xxxx.SZ), BSE (Beijing).';
COMMENT ON COLUMN stats.sec_board_map.pct           IS 'Board share, percent. Stock rows: always 100. ETF/index rows: SUM(weight_pct of the board''s constituents) / SUM(weight_pct of ALL constituents with a known board) * 100 — the denominator EXCLUDES constituents whose board is unknown, so rows sum to ~100. Rounded to 2dp.';
COMMENT ON COLUMN stats.sec_board_map.n_stocks      IS 'Number of constituent stocks of this board backing the pct (ETF/index rows). Stock rows: 1.';
COMMENT ON COLUMN stats.sec_board_map.snapshot_date IS 'Composition snapshot_date the mix was computed from (ETF/index rows). NULL for stock rows (identity-derived, not composition-derived). Staleness marker for the incremental run: a code is recomputed when its source max(snapshot_date) exceeds the stored one.';

CREATE INDEX IF NOT EXISTS idx_sec_board_map_sec_type
    ON stats.sec_board_map (sec_type);
