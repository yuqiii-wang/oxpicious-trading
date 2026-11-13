-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Each industry's daily margin financing — stock and ETF member balances and buys summed, with member-normalized curves alongside.
-- tags: margin, 融资融券, industry, 行业, financing balance, 融资余额




-- ============================================================================
--  Margin Industry Stats — per-(date, industry_id) SUM aggregation of stock
--  AND ETF RONGZI (融资) margin flows.
--
--  REDUCED SCHEMA: the diagnostic count / weight-share columns and the
--  GENERATED total_* columns were REMOVED in the margin cleanup. The four
--  SUM aggregates survive, plus four *_norm member-AVERAGE columns (the
--  average of a side's members' non-NULL non-zero rz_* on that date —
--  the Margin Trends raw-vs-normalized toggle plots these as the
--  per-member-normalized industry curve). The table's remaining consumer
--  is the margin-trends themes endpoint (industry universe with margin
--  data).
--
--  Stock → industry via sec_classification (type='stock',
--  parent_index_is_primary=TRUE, parent_index_code <> '').
--  ETF   → industry via two-hop: parent_index_code → index.code →
--          index.industry_id (mirrors stats.etf_trading_amt).
--
--  Table: analysis.margin_industry_stats
--    PK: (industry_id, date)
--
--  POPULATION: analysis.margins (Python, truncate-then-recompute).
-- ============================================================================
DROP TABLE IF EXISTS analysis.margin_industry_stats;
CREATE TABLE analysis.margin_industry_stats (
    date                      DATE          NOT NULL,
    industry_id               TEXT          NOT NULL,

    -- Display label (denormalized, from stats.sec_classification)
    industry_label            TEXT          NOT NULL DEFAULT '',

    -- SUM(rz_balance) — rongzi OUTSTANDING (yuan, STOCK).
    stock_margin_balance      NUMERIC(24,4),
    etf_margin_balance        NUMERIC(24,4),

    -- SUM(rz_buy) — daily rongzi BUY amount (yuan, FLOW).
    stock_margin_buy          NUMERIC(24,4),
    etf_margin_buy            NUMERIC(24,4),

    -- AVG(rz_*) over members with a NON-NULL NON-ZERO value on this date
    -- (the per-member-normalized curve; NULL when no member contributed).
    stock_margin_balance_norm NUMERIC(24,4),
    etf_margin_balance_norm   NUMERIC(24,4),
    stock_margin_buy_norm     NUMERIC(24,4),
    etf_margin_buy_norm       NUMERIC(24,4),

    CONSTRAINT pk_margin_industry_stats PRIMARY KEY (industry_id, date)
) PARTITION BY HASH (industry_id);

-- Native hash partitions (8) keyed by industry_id
SELECT public.create_hash_partitions('analysis', 'margin_industry_stats', 8);

COMMENT ON TABLE  analysis.margin_industry_stats IS 'Per-(industry_id, date) aggregation of stock AND ETF RONGZI (融资) margin flows: stock/etf margin_balance (SUM rz_balance, yuan), margin_buy (SUM rz_buy, yuan) and the *_norm member AVERAGES (AVG rz_* over members with a NON-NULL NON-ZERO value that date — the per-member-normalized curve for the Margin Trends raw-vs-normalized toggle, plotted as SUM(stock_norm+etf_norm)). A 0 SUM is stored as NULL — 0/NULL means no member contributed on that date (missing data), never a genuine zero-margin day — so the margin-trends industry series breaks the line instead of dipping to 0. Drives the margin-trends themes industry universe AND the Margin Trends ''industry'' attribution (SUM(stock+etf) = the industry''s own margin curve; trend detection source for sec_type=3). RONGZI only. Built by analysis.margins (truncate-then-recompute); all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.margin_industry_stats.date             IS 'Trading date.';
COMMENT ON COLUMN analysis.margin_industry_stats.industry_id      IS 'Industry identifier (e.g. BANKS, SEMI). Maps from the primary parent index of each stock / the tracking index of each ETF via stats.sec_classification.';
COMMENT ON COLUMN analysis.margin_industry_stats.industry_label   IS 'Display label (denormalized from stats.sec_classification.industry_label).';
COMMENT ON COLUMN analysis.margin_industry_stats.stock_margin_balance IS 'SUM(stats.stock_liquidity_margin.rz_balance) across stocks whose primary industry = industry_id on this date. Yuan. NULL = no stock contributed on this date (zeros are never stored).';
COMMENT ON COLUMN analysis.margin_industry_stats.etf_margin_balance   IS 'SUM(stats.etf_liquidity_margin.rz_balance) across ETFs tracking indices in industry_id on this date. Yuan. NULL = no ETF contributed on this date (zeros are never stored).';
COMMENT ON COLUMN analysis.margin_industry_stats.stock_margin_buy     IS 'SUM(stats.stock_liquidity_margin.rz_buy) across stocks whose primary industry = industry_id on this date. Yuan (FLOW — daily rongzi BUY amount / 融资买入额). NULL = no stock contributed on this date (zeros are never stored).';
COMMENT ON COLUMN analysis.margin_industry_stats.etf_margin_buy       IS 'SUM(stats.etf_liquidity_margin.rz_buy) across ETFs tracking indices in industry_id on this date. Yuan (FLOW). NULL = no ETF contributed on this date (zeros are never stored).';
COMMENT ON COLUMN analysis.margin_industry_stats.stock_margin_balance_norm IS 'AVG(stats.stock_liquidity_margin.rz_balance) across the industry''s stocks with a NON-NULL NON-ZERO rz_balance on this date (zero-margin members are excluded from BOTH numerator and denominator). Yuan per member. NULL = no stock contributed on this date.';
COMMENT ON COLUMN analysis.margin_industry_stats.etf_margin_balance_norm   IS 'AVG(stats.etf_liquidity_margin.rz_balance) across the industry''s ETFs with a NON-NULL NON-ZERO rz_balance on this date. Yuan per member. NULL = no ETF contributed on this date.';
COMMENT ON COLUMN analysis.margin_industry_stats.stock_margin_buy_norm     IS 'AVG(stats.stock_liquidity_margin.rz_buy) across the industry''s stocks with a NON-NULL NON-ZERO rz_buy on this date. Yuan per member (FLOW). NULL = no stock contributed on this date.';
COMMENT ON COLUMN analysis.margin_industry_stats.etf_margin_buy_norm       IS 'AVG(stats.etf_liquidity_margin.rz_buy) across the industry''s ETFs with a NON-NULL NON-ZERO rz_buy on this date. Yuan per member (FLOW). NULL = no ETF contributed on this date.';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('margin_industry_stats', 'margin_industry_stats', NULL, NOW(),
     'Per-(date, industry_id) aggregation of stock AND ETF RONGZI (融资) margin flows: stock/etf margin_balance + margin_buy SUMs plus the *_norm member AVERAGES (AVG over members with non-NULL non-zero rz_* — the per-member-normalized curve behind the Margin Trends raw-vs-normalized toggle). A 0 SUM is stored as NULL (0/NULL = no member contributed on that date — never a genuine zero-margin day). Drives the margin-trends themes industry universe AND the ''industry'' attribution series (SUM(stock+etf) = the industry''s own margin curve) + trend detection. RONGZI only. Built by analysis.margins (truncate-then-recompute); all INSERTs in Python.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
