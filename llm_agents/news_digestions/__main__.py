"""CLI entry point: ``python -m llm_agents.news_digestions``."""

import asyncio

from llm_agents.news_digestions.cli import main

if __name__ == "__main__":
    asyncio.run(main())
