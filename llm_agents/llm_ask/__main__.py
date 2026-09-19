"""CLI entry point: ``python -m llm_agents.llm_ask``."""

import asyncio

from llm_agents.llm_ask.cli import main

if __name__ == "__main__":
    asyncio.run(main())
