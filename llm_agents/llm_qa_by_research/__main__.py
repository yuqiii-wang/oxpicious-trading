"""``python -m llm_agents.llm_qa_by_research`` — the CLI entry point."""
from __future__ import annotations

import asyncio

from llm_agents.llm_qa_by_research.cli import main

if __name__ == "__main__":
    asyncio.run(main())
