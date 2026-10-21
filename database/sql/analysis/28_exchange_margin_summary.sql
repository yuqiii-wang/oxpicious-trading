-- ============================================================================
--  Exchange Margin Summary — per-(exchange, date) MARKET-WIDE margin totals
--  (analysis.exchange_margin_summary)
--
--  Accommodates the 2026-10-19 study finding: the downloads layer ALREADY
--  fetches each exchange's official market-wide margin summary every trade
--  date, as files SEPARATE from the per-security detail exports, and no
--  downstream build ever consumed them:
--    temps/sse_margin/sse_margin_summary_YYYYMMDD.csv   (RZRQ_HZ_INFO)
--    temps/szse_margin/szse_margin_summary_YYYYMMDD.csv (1837_xxpl tab1)
--  One row per exchange per trade date — the grain no other margin table
--  has (per-security tables are (code, date), index series is
--  (index_code, date), industry stats is (industry_id, date)).
--
--  OFFICIAL TOTALS — NOT derivable from the detail exports: across sampled
--  2025-09..2026-10 dates the summary 融资余额 exceeds the SUM of the
--  exchange's own detail rows by a systematic ~0.5–0.75% on BOTH exchanges
--  (the 汇总 covers securities absent from the public 明细 export). This
--  table is therefore the ONLY source for the exchange-official totals.
--
--  Column coverage differs by exchange (union stored, missing → NULL):
--    SSE: 信用交易日期 + 融资余额/融资买入额/融资偿还额/融券余量/
--         融券余量金额/融券卖出量/融资融券余额
--    SZSE: no date column (date comes from the filename) and NO 融资偿还额
--          → rz_repay is NULL for SZ rows.
--
--  FULL summary stored (RONGZI + RONQIN columns) — unlike the analysis
--  margin tables, which are deliberately RONGZI-only. Consumers filter.
--
--  POPULATION: Python loader over the summary CSVs (all INSERTs in Python
--  per project rule) — not wired yet at DDL time.
-- ============================================================================

DROP TABLE IF EXISTS analysis.exchange_margin_summary;

CREATE TABLE analysis.exchange_margin_summary (
    exchange        TEXT          NOT NULL,
    date            DATE          NOT NULL,

    -- The exchange-published totals, nullable per the etf_liquidity_margin
    -- convention: NULL = that exchange's summary export has no such column
    -- (or the file was missing/empty at build time); 0 is a REAL reported
    -- zero. Yuan / 股/份 units exactly as published (no rescaling).
    rz_balance      NUMERIC(24,4),
    rz_buy          NUMERIC(24,4),
    rz_repay        NUMERIC(24,4),
    rq_sell_qty     NUMERIC(24,4),
    rq_balance_qty  NUMERIC(24,4),
    rq_balance_amt  NUMERIC(24,4),
    total_balance   NUMERIC(24,4),

    CONSTRAINT pk_exchange_margin_summary PRIMARY KEY (exchange, date),
    CONSTRAINT chk_exchange_margin_summary_exchange
        CHECK (exchange IN ('SS', 'SZ'))
) PARTITION BY HASH (exchange);

-- One partition per exchange (2) — via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p01
SELECT public.create_hash_partitions('analysis', 'exchange_margin_summary', 2);

-- Date-first index: the PK is exchange-first; the UI date-range scans hit
-- both exchanges at once (mirrors idx_stock_margin_date rationale).
CREATE INDEX IF NOT EXISTS idx_exchange_margin_summary_date
    ON analysis.exchange_margin_summary (date);

COMMENT ON TABLE  analysis.exchange_margin_summary IS 'Per-(exchange, date) MARKET-WIDE margin totals from the exchanges'' official summary exports (SSE RZRQ_HZ_INFO, SZSE 1837_xxpl tab1) — files the downloads layer already fetches daily alongside the per-security detail CSVs but nothing consumed. Full summary stored (RONGZI + RONQIN). These are the OFFICIAL totals: not derivable by summing the detail exports (systematic ~0.5-0.75% gap — the 汇总 covers securities absent from the public 明细). exchange TEXT per the label_dim NOT-converted list. All INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.exchange_margin_summary.exchange IS 'Listing venue of the summary row: "SS" (上交所 SSE) or "SZ" (深交所 SZSE). Matches the canonical source-CSV exchange column values.';
COMMENT ON COLUMN analysis.exchange_margin_summary.date IS 'Trading date. SSE carries it in-file (信用交易日期); SZSE summary exports have no date column — the loader derives it from the filename YYYYMMDD.';
COMMENT ON COLUMN analysis.exchange_margin_summary.rz_balance IS '融资余额 (yuan) — market-wide rongzi outstanding. Both exchanges publish it. NULL = not in that exchange''s export / file missing at build time; 0 is a real reported zero.';
COMMENT ON COLUMN analysis.exchange_margin_summary.rz_buy IS '融资买入额 (yuan) — market-wide daily rongzi BUY amount (FLOW). Both exchanges publish it.';
COMMENT ON COLUMN analysis.exchange_margin_summary.rz_repay IS '融资偿还额 (yuan) — market-wide daily rongzi REPAYMENT (FLOW). SSE only; the SZSE summary export has no such column → always NULL for SZ rows.';
COMMENT ON COLUMN analysis.exchange_margin_summary.rq_sell_qty IS '融券卖出量 (股/份) — market-wide daily sec-borrow SELL quantity. SSE publishes it unitless (same 股/份 semantics); SZSE as 融券卖出量(股/份).';
COMMENT ON COLUMN analysis.exchange_margin_summary.rq_balance_qty IS '融券余量 (股/份) — market-wide sec-borrow outstanding quantity. SSE publishes it unitless; SZSE as 融券余量(股/份).';
COMMENT ON COLUMN analysis.exchange_margin_summary.rq_balance_amt IS '融券余额 (yuan) — market-wide sec-borrow outstanding value. SSE 融券余量金额(元); SZSE 融券余额(元).';
COMMENT ON COLUMN analysis.exchange_margin_summary.total_balance IS '融资融券余额 (yuan) — market-wide total margin outstanding (rz_balance + rq_balance_amt). Both exchanges publish it.';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('exchange_margin_summary', 'exchange_margin_summary', NULL, NOW(),
     'Per-(exchange, date) MARKET-WIDE margin totals from the exchanges'' official summary exports (SSE RZRQ_HZ_INFO / SZSE 1837_xxpl tab1) — downloaded daily alongside the per-security detail CSVs, previously unconsumed. Full summary stored (RONGZI + RONQIN): rz_balance/rz_buy/rz_repay (SSE only) + rq_sell_qty/rq_balance_qty/rq_balance_amt/total_balance. Official totals — NOT derivable from the detail exports (systematic ~0.5-0.75% gap on both exchanges). All INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
