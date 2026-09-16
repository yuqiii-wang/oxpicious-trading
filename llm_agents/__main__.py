"""CLI entry point for the llm_agents package.

Dispatches on the first positional argument:

  * ``search`` / ``summarize`` — the online search + summary agent
    (``python -m llm_agents search --query "…"``).
  * ``digest`` / ``digestions`` — the per-article news digestion agent
    (``python -m llm_agents digest --limit 100``).
  * ``industry-qa`` — the weekly industry hypes & drains Q&A agent
    (``python -m llm_agents industry-qa run [--dry-run]``).
  * anything else (``add`` / ``list`` / ``deactivate`` / …) — the llm_qa
    knowledge-base store, i.e. ``python -m llm_agents.llm_qa``.
"""

import asyncio
import sys

_ONLINE_SEARCH_COMMANDS = {"search", "summarize"}
_DIGEST_COMMANDS = {"digest", "digestions"}
_INDUSTRY_QA_COMMANDS = {"industry-qa", "industry_qa"}


async def _dispatch():
    first = sys.argv[1] if len(sys.argv) > 1 else ""
    if first in _ONLINE_SEARCH_COMMANDS:
        from llm_agents.online_search_summary import main
        return await main()
    if first in _DIGEST_COMMANDS:
        from llm_agents.news_digestions import main
        return await main()
    if first in _INDUSTRY_QA_COMMANDS:
        from llm_agents.industry_qa_weekly import main
        return await main()
    from llm_agents.llm_qa import main
    await main()


if __name__ == "__main__":
    asyncio.run(_dispatch())
