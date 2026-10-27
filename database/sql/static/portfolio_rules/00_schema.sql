-- ----------------------------------------------------------------------------
--  Portfolio-rule DOCS — the analysis_signals.portfolio_signal_rules
--  knowledge documents, in the STATIC schema (the
--  static.signal_rules_docs convention at the portfolio tier).
--
--  The portfolio docs tier was first set up 2026-10-20 as
--  static.portfolio_signal_docs ({name, description, ...,
--  benefit} per portfolio_type) and DROPPED 2026-10-27 — nothing
--  ever read the joined document. Re-set up the same day as THIS
--  table: keyed the same (one row per static.label_dim
--  'portfolio_type'), document in the pruned signal-docs convention —
--  only the texts a consumer renders, here per PORTFOLIO RULE (the
--  portfolio_sub_type vocabulary): {rules{<portfolio_sub_type>:
--  {meaning, trigger}}}.
--
--  NO consumer yet: the portfolio API does not join this table —
--  wire it at the read boundary when a consumer arrives. The docs
--  change by code-reviewed edits to the seed files, then re-apply.
-- ----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS static;

DROP TABLE IF EXISTS static.portfolio_rules_docs CASCADE;

CREATE TABLE static.portfolio_rules_docs (
    portfolio_type SMALLINT  NOT NULL,  -- label code (static.label_dim 'portfolio_type'): 0=neg_corr_hedge_pair
    document       JSONB     NOT NULL,  -- {rules{<portfolio_sub_type>: {meaning, trigger}}}
    updated_at     TIMESTAMP NOT NULL,  -- the document's own content revision (hand-set in the seed files)

    CONSTRAINT pk_portfolio_rules_docs PRIMARY KEY (portfolio_type)
);

COMMENT ON TABLE  static.portfolio_rules_docs IS 'Portfolio-rule knowledge documents — one JSONB per portfolio_type, per-rule texts under rules{<portfolio_sub_type>}. NO consumer yet (the portfolio API does not join it). Seeded (and re-seeded) from database/sql/static/portfolio_rules/ — the seed files are the source of record; never hand-UPDATE the rows.';
COMMENT ON COLUMN static.portfolio_rules_docs.portfolio_type IS 'The portfolio type, as its static.label_dim (''portfolio_type'') SMALLINT code: 0 neg_corr_hedge_pair — the two-leg ''A~B'' long–long negatively-correlated hedge pair.';
COMMENT ON COLUMN static.portfolio_rules_docs.document IS 'The knowledge document: {rules{<portfolio_sub_type>: {meaning, trigger}}} — per PORTFOLIO RULE texts (trio_wash / spread), grounded in the engine-written rule_params.subs.<sub>.condition lines.';
COMMENT ON COLUMN static.portfolio_rules_docs.updated_at IS 'The document content revision (hand-set in the seed files, NOT a write stamp — rows change only via the seed files).';
