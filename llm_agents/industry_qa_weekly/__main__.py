"""CLI entry point: ``python -m llm_agents.industry_qa_weekly``."""

import asyncio

from llm_agents.industry_qa_weekly.cli import main

if __name__ == "__main__":
    asyncio.run(main())
