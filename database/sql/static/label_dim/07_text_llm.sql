-- ============================================================================
--  static.label_dim seed — TEXT / LLM-LAYER vocabularies
--
--  The text-schema knowledge-base closed vocabularies. Owning tables:
--  text.llm_qa_by_research (research_logic — the dominant investing
--  logic a research-logic Q&A row classifies). First domain of the
--  schema's LLM-layer enums converted to the label-code contract
--  (2026-10-27); further text.* conversions register here.
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- research_logic: text.llm_qa_by_research.logic_label — the dominant
    -- investing logic of the row's underlying industry/security (what the
    -- answer's intro/return/risks decomposition explains). 99 other = the
    -- fallback (the logic did not fit / the model could not classify)
    ('research_logic',  0, 'growth',    0),
    ('research_logic',  1, 'value',     1),
    ('research_logic',  2, 'dividend',  2),
    ('research_logic',  3, 'commodity', 3),
    ('research_logic',  4, 'cyclical',  4),
    ('research_logic',  5, 'defensive', 5),
    ('research_logic',  6, 'quality',   6),
    ('research_logic',  7, 'theme',     7),
    ('research_logic', 99, 'other',    99);
