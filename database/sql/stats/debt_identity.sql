-- ============================================================================
--  Debt Daily - Split Tables
--  Original: single debt table from schema.sql (pre-split)
--  Split into: debt_identity, debt_omo, debt_repo, debt_outright_repo,
--              debt_mlf, debt_shibor, debt_treasury
--  Reconstruct via: v_debt_daily view (see 99_reconstruct_views.sql)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Table: debt_identity
--   Identity core (PK) for all debt sub-tables
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Which trading days carry debt-market data — the shared date identity for all debt rate and liquidity tables.
-- tags: debt, 债券, rates, 利率, identity, 标识, reference, 参考

CREATE TABLE IF NOT EXISTS stats.debt_identity (
    date                      DATE          NOT NULL,
    CONSTRAINT pk_debt_identity PRIMARY KEY (date)
);

COMMENT ON TABLE  stats.debt_identity                IS 'Debt daily identity: one row per trading day. PK shared by all debt sub-tables.';
