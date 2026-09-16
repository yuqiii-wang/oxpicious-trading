"""llm_agents — LLM agent workspace for the text/ knowledge base.

Home of the project's LLM agents. The agents consume the `text` schema
(text.news / text.news_keywords as the retrieval corpus) and persist their
curated output in text.llm_qa (see database/sql/text/02_llm_qa.sql).

Modules:

  * ``llm_qa`` — the storage layer for Q&A knowledge-base rows plus their
    news provenance (text.news_groups -> text.news_group_items). Write to
    the store directly via

        python -m llm_agents.llm_qa add --question "…" --answer "…" [--news-ids 1,2]
        python -m llm_agents.llm_qa list [--limit 20]

    or programmatically via llm_agents.llm_qa.upsert_qa.

  * ``online_search_summary`` — the online search + LLM summary agent:
    provider classes (ZhiPu BigModel today, subclass
    BaseOnlineSearchProvider to add more), normalized reference/citation
    handling ([来源：ref_N] -> ref_N), and persistence of search references
    into text.news + the Q&A into text.llm_qa. CLI::

        python -m llm_agents.online_search_summary search --query "…"
        python -m llm_agents.online_search_summary summarize --query "…" --store

    (``python -m llm_agents search|summarize …`` dispatches there too.)

  * ``news_digestions`` — the per-article digestion agent: summarize each
    text.news article and score its sentiment in [-5, 5] into
    text.news_digestions (see database/sql/text/04_news_digestions.sql).
    CLI::

        python -m llm_agents.news_digestions run [--limit 100] [--force]
        python -m llm_agents.news_digestions list

    (``python -m llm_agents digest|digestions …`` dispatches there too.)

  * ``industry_qa_weekly`` — the weekly industry hypes & drains Q&A
    agent: picks the top-5 HYPE / top-5 DRAIN industries of the anchor
    month from analysis.industry_hypes_seasonal — the exact block the
    Market Trend view renders (default period 120 trading days) — and
    asks the online-search summarize agent WHY each rose/dropped,
    storing the Q&A into text.llm_qa (category='industry', the ranking
    date added to the question, 60-trading-day same-direction dedupe —
    a direction flip always asks;
    backfill steps the 5-trading-day grid).
    CLI::

        python -m llm_agents.industry_qa_weekly run [--dry-run]
        python -m llm_agents.industry_qa_weekly backfill --start YYYY-MM-DD

    (``python -m llm_agents industry-qa …`` dispatches there too.)
"""
