"""llm_agents — LLM agent workspace for the text/ knowledge base.

Home of the project's LLM agents. The agents consume the `text` schema
(text.news / text.news_keywords as the retrieval corpus) and persist their
curated output in text.llm_qa (see database/sql/text/02_llm_qa.sql).

``_core`` is the shared provider plumbing inside this package (the
LlmProviderError type, the retrying JSON transport + .env loader, and
BaseLlmProvider — see ``llm_agents/_core/__init__.py``); the agent
packages below build on it and must never grow their own copies.

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

  * ``llm_ask`` — the plain LLM ask agent: one question -> one chat
    completion via a registered provider (ZhiPu BigModel today, subclass
    BaseLlmAskProvider to add more), NO online search — the request
    never carries tools. Optional images (PNG data URLs) attach as
    image_url parts only on vision-capable models (the core/vision.py
    config: glm-4*v and glm-5.3-flash yes, glm-5.2 / glm-4.5 no);
    text-only models drop
    them and proceed. Nothing is persisted; the CLI prints the answer
    (optionally as a ``@@LLM_ASK_JSON@@`` marker line). This package
    also backs the data_viz "AI Ask" chart adviser (the "?" beside
    chart titles): the Express ai-ask service (POST /api/ai/ask) writes
    {question, plotInfo, screenshots, themeMode} to a payload file and
    spawns ``ask --payload-file`` — the adviser prompt + plot-info
    context are built from it, and screenshots switch the model to the
    provider's default vision model. CLI::

        python -m llm_agents.llm_ask ask --question "…" [--model glm-5.3-flash]
        python -m llm_agents.llm_ask ask --payload-file …

    (``python -m llm_agents ask …`` dispatches there too.)

  * ``news_digestions`` — the per-article digestion agent: summarize each
    text.news article and score its sentiment in [-5, 5] into
    text.news_digestions (see database/sql/text/04_news_digestions.sql).
    CLI::

        python -m llm_agents.news_digestions run [--limit 100] [--force]
        python -m llm_agents.news_digestions list

    (``python -m llm_agents digest|digestions …`` dispatches there too.)

  * ``deep_agents`` — the minimalist tool-calling agent: an LLM loop
    whose tools come from an MCP server (default the repo's own
    read-only DB server). A per-run CODE SANDBOX
    (``llm_agents.deep_agents.core.sandbox``) adds the four ``sandbox_*``
    built-in tools: the model writes .py scripts into a session
    directory of the DOCKERIZED py sandbox (via the ``llm_agents.sandbox``
    MCP middleware) and runs them inside the cutepandas py3.12 container
    — DB env preset READ-ONLY (readonly_analytics role), timeout kill,
    capped output.
    CLI::

        python -m llm_agents.deep_agents "…"
        python -m llm_agents.deep_agents --list-tools

    (``python -m llm_agents deep "…"`` dispatches there too.)

  * ``mcp`` — the MCP server those agents talk to (moved here from
    repo-root ``mcp/`` 2026-10-29, which also un-shadowed the
    installed ``mcp`` SDK): read-only DB queries (readonly_analytics
    role) plus web_search / web_summarize — the online_search_summary
    providers exposed as live web tools, persisting nothing. CLI::

        python -m llm_agents.mcp [--stdio]

The historical industry hypes & drains Q&A backfill (category
'industry' / 'market' rows in text.llm_qa) was removed with its
``industry_qa_weekly`` package — the live daily flow is
``downloads.macro.ai_daily`` (category 'market_daily'); the historical
rows remain queryable in text.llm_qa.
"""
