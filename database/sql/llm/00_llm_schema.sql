-- ============================================================================
--  LLM schema bootstrap
--  Schema: llm
--    The LLM layer's persistence: the question-answering knowledge bases
--    (llm_qa / llm_qa_by_ask / llm_qa_by_research + their refs/keywords/
--    context children), the source request log (llm_requests), the prompt
--    registry (ai_prompts), the deep-agent run persistence (deep_agent_
--    states / contexts / identities / store_items / langgraph_templates) and
--    the knowledge hypergraph (kg_entities / kg_edges — minimalist setup,
--    no writer yet).
--
--    Migrated out of the `text` schema (2026-11-04): text keeps the news
--    corpus (news / news_groups / news_keywords / news_digestions /
--    search_lexicon_df / the news_keyword_audit matview); the LLM-layer
--    tables live here. Cross-schema FKs stay: the Q&A rows still point at
--    text.news_groups / text.news provenance. The LangGraph runtime's
--    checkpoint store remains the separate framework-managed `langgraph`
--    schema (07_deep_agent_states.sql).
--
--  Dir convention: this DDL lives one level under the text dir
--  (database/sql/text/llm/) because the tables were split out of it;
--  files apply after text/01_news.sql (FK dependency) — numeric names
--  sort before this dir's, and the corpus files the FKs need (00/01)
--  sort first. Seeds: static/ai_prompts/ populates llm.ai_prompts.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS llm;

COMMENT ON SCHEMA llm IS
    'LLM layer: Q&A knowledge bases (llm_qa / llm_qa_by_ask / '
    'llm_qa_by_research + refs / keywords / ask-context children), the '
    'source request log (llm_requests), the prompt registry (ai_prompts), '
    'the deep-agent run persistence (deep_agent_states / contexts / '
    'identities / store_items / langgraph_templates) and the knowledge '
    'hypergraph (kg_entities / kg_edges — minimalist setup, no writer '
    'yet). Split out of the text schema (2026-11-04); Q&A rows keep '
    'cross-schema FKs to the text.news corpus provenance.';
