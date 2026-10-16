-- ----------------------------------------------------------------------------
--  Signal family DOCS — the analysis_signals knowledge documents, in
--  the STATIC schema (static = the seeded reference data layer: rows
--  land only via these SQL files, never via a pipeline writer).
--
--  The family knowledge of the 2026-10-14 rules tier: previously a
--  JSONB `document` column on analysis_signals.signal_rules —
--  byte-identical within a family, so it normalized out — then briefly
--  the engines' static template JSONs; since 2026-10-15 the DB rows
--  seeded HERE are the record of record.
--
--  ONE row per signal family (static.label_dim 'signal_type'): the
--  knowledge document the rules API serves with every rule cell —
--  {name, description, condition, sources, principles, sub_signals{
--  <sub_type>: {meaning, trigger, action, params}}}. The per-cell
--  measured evidence is NOT here (rule_params carries it); the docs
--  change by code-reviewed edits to the seed files, then re-apply.
-- ----------------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS static;

DROP TABLE IF EXISTS static.analysis_signal_docs CASCADE;

CREATE TABLE static.analysis_signal_docs (
    signal_type SMALLINT     NOT NULL,  -- label code (static.label_dim 'signal_type'): 0=mov_rsi 1=bollinger 2=ma_cross 3=options_wall 4=options_vix 5=momentum
    document    JSONB        NOT NULL,  -- the family knowledge document (the seed files' JSONB, verbatim)
    updated_at  TIMESTAMP    NOT NULL,  -- the document's own content revision (hand-set in the seed files)

    CONSTRAINT pk_analysis_signal_docs PRIMARY KEY (signal_type)
);

COMMENT ON SCHEMA static IS 'Seeded reference data — static vocabularies and knowledge documents whose rows land ONLY via the SQL seed files under database/sql/static/ (never via a pipeline writer); re-apply a seed file to refresh its rows.';
COMMENT ON TABLE  static.analysis_signal_docs IS 'Signal family knowledge documents — one JSONB per signal_type family, served by the rules API with every analysis_signals.signal_rules cell. Seeded (and re-seeded) from database/sql/static/analysis_signal_docs/ — the seed files are the source of record; never hand-UPDATE the rows.';
COMMENT ON COLUMN static.analysis_signal_docs.signal_type IS 'The family, as its static.label_dim (''signal_type'') SMALLINT code: 0=mov_rsi 1=bollinger 2=ma_cross 3=options_wall 4=options_vix 5=momentum.';
COMMENT ON COLUMN static.analysis_signal_docs.document IS 'The knowledge document: {name, description, condition, sources, principles, sub_signals{<sub_type>: {meaning, trigger, action, params}}} — matches the retired static/example.<code>.<sub>.json template shape (its empty performance placeholder and updated_at key excluded; updated_at is the column).';
COMMENT ON COLUMN static.analysis_signal_docs.updated_at IS 'The document content revision (hand-set in the seed files, NOT a write stamp — rows change only via the seed files).';
