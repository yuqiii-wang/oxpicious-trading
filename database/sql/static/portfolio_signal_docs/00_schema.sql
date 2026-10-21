-- ----------------------------------------------------------------------------
--  Portfolio signal DOCS — the PORTFOLIO tier's knowledge documents, in
--  the STATIC schema (the static.analysis_signal_docs convention at
--  the portfolio tier, 2026-10-20).
--
--  ONE row per portfolio type (static.label_dim 'portfolio_type'):
--  the knowledge document the portfolio signal API serves with every
--  matched portfolio — {name, description, condition, sources,
--  principles, benefit}. `benefit` is the portfolio docs' analog of
--  the signal docs' `sub_signals`: the vocabulary of the BENEFIT AUDIT
--  (analysis_signals.history_portfolio_signal_performance — .pooled the
--  combined member stats, .benefit / .dd the structure audit) this
--  type claims — its `kind`, the max_dd gauge fields, and the dd lens
--  — so the audit's payload is documented where the audit is defined.
--
--  The per-group CONSTANT construction (legs / market / aim /
--  weighting + the coordination spec) is NOT here — it is
--  per-(code, sub type) and lives in
--  analysis_signals.portfolio_signal_rules.rule_params, the
--  signal_rules rule_params grammar; this document is byte-identical
--  within a portfolio type, so it normalizes out (the 2026-10-15
--  signal-docs precedent).
-- ----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS static;

DROP TABLE IF EXISTS static.portfolio_signal_docs CASCADE;

CREATE TABLE static.portfolio_signal_docs (
    portfolio_type  SMALLINT     NOT NULL,  -- label code (static.label_dim 'portfolio_type'): 0 neg_corr_hedge_pair
    document        JSONB        NOT NULL,  -- the portfolio type's knowledge document (the seed files' JSONB, verbatim)
    updated_at      TIMESTAMP    NOT NULL,  -- the document's own content revision (hand-set in the seed files)

    CONSTRAINT pk_portfolio_signal_docs PRIMARY KEY (portfolio_type)
);

COMMENT ON TABLE  static.portfolio_signal_docs IS 'Portfolio signal knowledge documents — one JSONB per portfolio_type, served by the portfolio signals API with every matched analysis_signals.portfolio_signal_rules group. Seeded (and re-seeded) from database/sql/static/portfolio_signal_docs/ — the seed files are the source of record; never hand-UPDATE the rows (the static.analysis_signal_docs convention at the portfolio tier, 2026-10-20).';
COMMENT ON COLUMN static.portfolio_signal_docs.portfolio_type IS 'The portfolio type, as its static.label_dim (''portfolio_type'') SMALLINT code: 0 neg_corr_hedge_pair — the two-leg ''A~B'' long–long negatively-correlated hedge pair.';
COMMENT ON COLUMN static.portfolio_signal_docs.document IS 'The knowledge document: {name, description, condition, sources, principles, benefit} — the signal doc shape with `benefit` in place of `sub_signals`: the vocabulary of the BENEFIT AUDIT this type claims (analysis_signals.history_portfolio_signal_performance — its kind, the max_dd gauge fields, and the dd lens). The per-portfolio constant construction lives in portfolio_signal_rules.rule_params (the per-sub coordination specs under subs), not here.';
COMMENT ON COLUMN static.portfolio_signal_docs.updated_at IS 'The document content revision (hand-set in the seed files, NOT a write stamp — rows change only via the seed files).';
