-- ============================================================================
--  Table: analysis.mov_ave_spreads_codes  (per-code summary of the detail
--                                             table — ONE row per
--                                             (sec_type, code))
--
--  Precomputed source for the /mov-ave-spread/codes + /themes endpoints.
--  The live queries they replaced aggregated analysis.mov_ave_spreads_detail
--  on every request (GROUP BY code over 18 MIN/MAX columns + per-code
--  LATERAL latest-row and name lookups): 7-14s for the stock universe
--  (~6.8M rows) and 5-6s for the DISTINCT-code themes scan, gating the
--  MA-Spread page's first open (2026-09-29). This table moves that work
--  into the analysis.mov_ave_spread pipeline, where it runs offline once
--  per write pass; the endpoints become a small indexed table read (~10ms).
--
--  Maintained by analysis/mov_ave_spread/__main__.py (refresh_codes_summary):
--  DELETE + INSERT per affected sec_type (or per --code in single-code
--  runs) after every detail write pass. Backfill = the same INSERT, run
--  manually once per sec_type after creating the table.
--
--  Column semantics mirror the former request-time queries exactly:
--    first_date / last_date / n_dates — date coverage per code
--    max_p{5,20,60,120,255} / max_5_{20,60,120,255}   — all-time MAX of the
--    min_p{...} / min_5_{...}                           9 gap columns
--    latest_price_vs_ma{...} / latest_ma5_vs_ma{...}   — the 9 gap values at
--                                                        last_date
--    name — latest identity name (from stats.{etf,index,stock}_identity)
-- ============================================================================
CREATE TABLE IF NOT EXISTS analysis.mov_ave_spreads_codes (
    sec_type        SMALLINT     NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    code            TEXT         NOT NULL,  -- matches detail.code (ETF bare; stock suffixed)
    name            TEXT         NOT NULL DEFAULT '',
    first_date      DATE         NOT NULL,
    last_date       DATE         NOT NULL,
    n_dates         BIGINT       NOT NULL,

    -- All-time extremes of the 9 gap columns (signed fractional ratios).
    max_p5          NUMERIC(10,6),
    max_p20         NUMERIC(10,6),
    max_p60         NUMERIC(10,6),
    max_p120        NUMERIC(10,6),
    max_p255        NUMERIC(10,6),
    max_5_20        NUMERIC(10,6),
    max_5_60        NUMERIC(10,6),
    max_5_120       NUMERIC(10,6),
    max_5_255       NUMERIC(10,6),
    min_p5          NUMERIC(10,6),
    min_p20         NUMERIC(10,6),
    min_p60         NUMERIC(10,6),
    min_p120        NUMERIC(10,6),
    min_p255        NUMERIC(10,6),
    min_5_20        NUMERIC(10,6),
    min_5_60        NUMERIC(10,6),
    min_5_120       NUMERIC(10,6),
    min_5_255       NUMERIC(10,6),

    -- Latest snapshot: the 9 gap values at last_date.
    latest_price_vs_ma5    NUMERIC(10,6),
    latest_price_vs_ma20   NUMERIC(10,6),
    latest_price_vs_ma60   NUMERIC(10,6),
    latest_price_vs_ma120  NUMERIC(10,6),
    latest_price_vs_ma255  NUMERIC(10,6),
    latest_ma5_vs_ma20     NUMERIC(10,6),
    latest_ma5_vs_ma60     NUMERIC(10,6),
    latest_ma5_vs_ma120    NUMERIC(10,6),
    latest_ma5_vs_ma255    NUMERIC(10,6),

    CONSTRAINT pk_mov_ave_spreads_codes PRIMARY KEY (sec_type, code)
);

COMMENT ON TABLE  analysis.mov_ave_spreads_codes IS 'Per-code summary of analysis.mov_ave_spreads_detail (ONE row per sec_type+code): date coverage, all-time gap extremes, latest gap snapshot, latest name. Maintained by analysis.mov_ave_spread after every detail write pass; read by the /mov-ave-spread/codes + /themes endpoints so they never aggregate the multi-million-row detail table at request time.';
COMMENT ON COLUMN analysis.mov_ave_spreads_codes.name IS 'Latest identity name (stats.{etf,index,stock}_identity row at the greatest date). Empty string when the identity table has no row for the code.';

-- ----------------------------------------------------------------------------
--  Maintenance statement (the pipeline's refresh_codes_summary runs this
--  per affected sec_type, with an optional code equality filter for
--  single-code runs; {identity} = stats.{etf,index,stock}_identity):
--
--  DELETE FROM analysis.mov_ave_spreads_codes WHERE sec_type = $1 [AND code = $2];
--
--  INSERT INTO analysis.mov_ave_spreads_codes (
--      sec_type, code, name, first_date, last_date, n_dates,
--      max_p5, ..., min_5_255,
--      latest_price_vs_ma5, ..., latest_ma5_vs_ma255)
--  WITH agg AS (
--      SELECT code,
--             MIN(date) AS first_date, MAX(date) AS last_date,
--             COUNT(date) AS n_dates,
--             MAX(price_vs_ma5) AS max_p5, ..., MIN(ma5_vs_ma255) AS min_5_255
--        FROM analysis.mov_ave_spreads_detail
--       WHERE sec_type = $1 [AND code = $2]
--       GROUP BY code
--  ),
--  latest AS (
--      SELECT DISTINCT ON (code) code,
--             price_vs_ma5, ..., ma5_vs_ma255
--        FROM analysis.mov_ave_spreads_detail
--       WHERE sec_type = $1 [AND code = $2]
--       ORDER BY code, date DESC
--  )
--  SELECT $1, a.code, COALESCE(n.name, ''),
--         a.first_date, a.last_date, a.n_dates,
--         a.max_p5, ..., a.min_5_255,
--         l.price_vs_ma5, ..., l.ma5_vs_ma255
--    FROM agg a
--    JOIN latest l ON l.code = a.code
--    LEFT JOIN LATERAL (
--        SELECT x.name FROM {identity} x
--         WHERE x.code = a.code
--         ORDER BY x.date DESC LIMIT 1 OFFSET 0
--    ) n ON TRUE;
-- ----------------------------------------------------------------------------
