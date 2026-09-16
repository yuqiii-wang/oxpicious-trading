"""llm_agents.online_search_summary — Online search + LLM summary agent.

Runs an online web search for a question, summarizes the retrieved results
with an LLM, and (optionally) persists the outcome into the text schema:
the reference articles land in ``text.news``, and the question/answer pair
lands in ``text.llm_qa`` with news-group provenance via the existing
``llm_agents.llm_qa`` storage layer.

Package layout — three layered subpackages plus the CLI, dependencies
pointing one way (providers/storage/cli -> core, never back):

  ``core/``      — the pure domain layer (no network, no DB):
    * ``core.errors``    — OnlineSearchError (status + provider error code).
    * ``core.models``    — normalized, provider-independent result models
      (SearchHit / SearchIntent / SearchOptions / SearchResponse /
      SearchSummary) + the shared request vocabulary (RECENCY_FILTERS /
      CONTENT_SIZES) and SHANGHAI_TZ.
    * ``core.citations`` — the refer/citation/date helpers
      (normalize_refer, extract_cited_refs, parse_publish_date,
      format_references).
    * ``core.prompts``   — the summary output contract and its builders
      (summary_system_prompt, native_search_prompt, lang_label).
    * ``daily``        — the scheduled daily request preset (today /
      latest-biz-date trigger, date-embedded market + sector question,
      anchor-aware recency): what ``downloads.macro.ai_daily`` runs.
  ``providers/`` — the search/chat provider integrations (network):
    * ``providers.transport`` — RetryingJsonClient: the Bearer-auth
      retrying JSON POST (retries ONLY genuinely transient failures) and
      the project-.env loader; ``providers.base`` — BaseOnlineSearchProvider:
      the abstract contract (search / _chat_complete / summarize), API-key
      resolution, and the provider-agnostic summarize_via_compose flow
      (the documented ``{search_result}`` prompt pattern under our
      control).
    * ``providers.zhipu``  — ZhiPu BigModel web_search + native
      search-in-chat.
    * ``providers.zhihu``  — Zhihu content-search API via the shared
      anti-bot proxy; search-only, summaries compose with a delegated
      chat provider.
    * ``providers.registry`` — PROVIDERS / get_provider: where providers
      register — subclass the base in ``providers/``, then add here.
  ``storage/``   — the text-schema persistence layer (DB + verification
  fetches):
    * ``storage.matching`` — the ref_type/resolved_via vocabulary and the
      pure match helpers (normalize_text, is_exact_article,
      source_from_url, hit_ref_time).
    * ``storage.fetching`` — PageFetcher: ddgs search + markitdown page
      extraction.
    * ``storage.resolve``  — RefResolver: per-reference resolution for
      storage — corpus (zhihu-search downloads) match -> ``relevant``;
      ddgs search + markitdown content verification -> ``exact``;
      unresolvable -> a null-content placeholder text.news row typed
      ``exact`` (the metadata IS the response's own). Outcomes land in
      ``text.llm_qa_refs`` (qa_id, ref) ->
      (ref_type, news_id, resolved_url, ref_time — the reference's publish
      time per the search response, now() when absent); DDL:
      database/sql/text/03_llm_qa_refs.sql.
    * ``storage.store``    — OnlineSearchSummaryStore: persists references
      into text.news and the Q&A into text.llm_qa with news-group
      provenance; no text.news_keywords writes (corpus stats stay owned
      by builds.text).
  ``cli.py``     — argparse CLI (search / summarize, --store, --json).

CLI::

    python -m llm_agents.online_search_summary search --query "…" \
        [--provider zhipu] [--engine search_pro] [--count 10] \
        [--recency oneWeek] [--domain www.sohu.com] [--content-size high]
    python -m llm_agents.online_search_summary summarize --query "…" \
        [--mode native|compose] [--model glm-5.2] [--store] \
        [--industry BANKS] [--category macro] [+ the search flags]

``python -m llm_agents search|summarize …`` dispatches here too (see
llm_agents.__main__).

The package re-exports its public surface (models, citations, prompts,
errors, providers, registry, storage, main) so ``from
llm_agents.online_search_summary import X`` keeps working regardless of
which file X lives in — depend on the facade, not on internal paths
(cli-dependent names load lazily: importing cli runs the entry-point
resource pre-check, which library importers must not inherit).
"""
from __future__ import annotations

from llm_agents.online_search_summary.core.errors import OnlineSearchError
from llm_agents.online_search_summary.core.models import (
    CONTENT_SIZES, RECENCY_FILTERS, SHANGHAI_TZ, SearchHit, SearchIntent,
    SearchOptions, SearchResponse, SearchSummary,
)
from llm_agents.online_search_summary.core.citations import (
    extract_cited_refs, format_references, normalize_refer,
    parse_publish_date,
)
from llm_agents.online_search_summary.core.prompts import lang_label
from llm_agents.online_search_summary.daily import (
    DAILY_CATEGORY, DAILY_COUNT, RECENCY_5D, daily_market_query,
    daily_recency, daily_request, daily_target_or_clamp,
    default_daily_target,
)
from llm_agents.online_search_summary.providers.base import (
    BaseOnlineSearchProvider,
)
from llm_agents.online_search_summary.providers import (
    ZhipuSearchProvider, ZhihuSearchProvider,
)
from llm_agents.online_search_summary.providers.registry import (
    PROVIDERS, get_provider,
)
from llm_agents.online_search_summary.storage.matching import (
    REF_TYPE_EXACT, REF_TYPE_RELEVANT, VIA_CORPUS, VIA_DDGS, VIA_SUMMARY,
    hit_ref_time, is_exact_article, source_from_url,
)
from llm_agents.online_search_summary.storage.resolve import (
    RefResolver, ResolvedRef,
)
from llm_agents.online_search_summary.storage.store import (
    OnlineSearchSummaryStore,
)

__all__ = [
    "OnlineSearchError",
    "SearchHit", "SearchIntent", "SearchOptions", "SearchResponse",
    "SearchSummary", "format_references", "extract_cited_refs",
    "normalize_refer", "parse_publish_date", "lang_label",
    "RECENCY_FILTERS", "CONTENT_SIZES", "SHANGHAI_TZ",
    "DAILY_CATEGORY", "DAILY_COUNT", "RECENCY_5D", "daily_market_query",
    "daily_recency", "daily_request", "daily_target_or_clamp",
    "default_daily_target",
    "BaseOnlineSearchProvider", "ZhipuSearchProvider", "ZhihuSearchProvider",
    "PROVIDERS", "get_provider",
    "REF_TYPE_EXACT", "REF_TYPE_RELEVANT",
    "VIA_SUMMARY", "VIA_CORPUS", "VIA_DDGS",
    "hit_ref_time", "is_exact_article", "source_from_url",
    "RefResolver", "ResolvedRef",
    "OnlineSearchSummaryStore",
    "RESULT_MARKER", "main",
]

# cli.py carries entry-point side effects (resource pre-check + stdout
# setup), so its names are resolved lazily: library importers of this
# package never trigger them; ``from …online_search_summary import main``
# (the CLI dispatch path) does.
_CLI_ATTRS = frozenset({"main", "RESULT_MARKER"})


def __getattr__(name: str):
    if name in _CLI_ATTRS:
        from llm_agents.online_search_summary import cli
        return getattr(cli, name)
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}")
