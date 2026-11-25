-- ============================================================================
--  Master Import Script — llm/ (regenerated 2026-11-10: ONE TABLE = ONE
--  FILE; complete + FK-safe order; supersedes the stale hand list).
--  Usage: psql -d analytics -f llm/00_init.sql
-- ============================================================================

-- schema files
\ir 00_llm_schema.sql

-- tables (FK order)
\ir deep_agent_identities.sql
\ir ai_prompts.sql
\ir deep_agent_contexts.sql
\ir deep_agent_langgraph_templates.sql
\ir deep_agent_store_items.sql
\ir kg_edges.sql
\ir kg_entities.sql
\ir kg_hotpaths.sql
\ir kg_queries.sql
\ir kg_rule_entities.sql
\ir kg_rule_syncs.sql
\ir kg_search_stats.sql
\ir llm_qa.sql
\ir llm_qa_by_ask.sql
\ir llm_qa_by_research.sql
\ir deep_agent_states.sql
\ir deep_agent_authorizations.sql
\ir llm_qa_ask_context.sql
\ir llm_qa_keywords.sql
\ir llm_qa_keywords_by_ask.sql
\ir llm_qa_refs.sql
\ir llm_requests.sql
\ir llm_qa_ask_context_images.sql

-- seeds
\ir static/ai_prompts/01_deep_agent_prompts.sql
\ir static/ai_prompts/02_skills_db_query.sql
\ir static/ai_prompts/03_skills_online_search.sql
\ir static/ai_prompts/04_skills_biz_guides_general.sql
\ir static/ai_prompts/05_skills_biz_guides_commons.sql
\ir static/ai_prompts/06_skills_biz_guides_signals.sql
\ir static/ai_prompts/07_skills_biz_guides_derivatives.sql
\ir static/ai_prompts/08_skills_biz_guides_composites.sql
\ir static/ai_prompts/09_plan_prompts.sql
\ir static/ai_prompts/10_tool_prompts.sql
\ir static/ai_prompts/11_skills_backend_tech.sql
\ir static/ai_prompts/12_skills_text_corpus.sql
\ir static/knowledge_graphs/data_and_labels/01_label_domains.sql
\ir static/knowledge_graphs/db_semantics/01_index_daily_chain.sql
\ir static/knowledge_graphs/db_semantics/02_etf_stock_daily_chains.sql
\ir static/knowledge_graphs/db_semantics/03_intraday_bars_and_views.sql
\ir static/knowledge_graphs/db_semantics/04_bond_rates_chain.sql
\ir static/knowledge_graphs/db_semantics/05_futures_chain.sql
\ir static/knowledge_graphs/db_semantics/06_options_stats_chain.sql
\ir static/knowledge_graphs/db_semantics/07_options_analysis_live.sql
\ir static/knowledge_graphs/db_semantics/08_classification_industry_cross_stats.sql
\ir static/knowledge_graphs/db_semantics/09_margin_chain.sql
\ir static/knowledge_graphs/db_semantics/10_ma_spread_studies.sql
\ir static/knowledge_graphs/db_semantics/11_pe_dividends.sql
\ir static/knowledge_graphs/db_semantics/12_market_beta_industry.sql
\ir static/knowledge_graphs/db_semantics/13_trend_crowdedness.sql
\ir static/knowledge_graphs/db_semantics/14_signals_live.sql
\ir static/knowledge_graphs/db_semantics/15_text_corpus_static.sql
\ir static/knowledge_graphs/db_semantics/16_strategy.sql
\ir static/knowledge_graphs/db_semantics/17_sql_join_paths.sql
\ir static/knowledge_graphs/db_semantics/18_market_participation.sql
\ir static/knowledge_graphs/prompts/01_prompt_catalog.sql

