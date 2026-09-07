"""llm_agents — LLM agent workspace for the text/ knowledge base.

Home of the project's LLM agents. The agents consume the `text` schema
(text.news / text.news_keywords as the retrieval corpus) and persist their
curated output in text.llm_qa (see database/sql/text/02_llm_qa.sql).

Current scope: `llm_qa` — the storage layer for Q&A knowledge-base rows plus
their news provenance (text.news_groups -> text.news_group_items). Agent
orchestration (retrieval, prompting, answer generation) is intentionally NOT
implemented yet; write to the store directly via

    python -m llm_agents.llm_qa add --question "…" --answer "…" [--news-ids 1,2]
    python -m llm_agents.llm_qa list [--limit 20]

or programmatically via llm_agents.llm_qa.upsert_qa.
"""
