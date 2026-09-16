"""CLI entry point: ``python -m llm_agents.online_search_summary``."""

import asyncio

from llm_agents.online_search_summary.cli import main

if __name__ == "__main__":
    asyncio.run(main())
