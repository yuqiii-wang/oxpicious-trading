"""CLI entry point for the llm_agents package.

For now the package's only surface is the llm_qa knowledge-base store, so
``python -m llm_agents`` delegates to ``python -m llm_agents.llm_qa``.
"""

import asyncio

from llm_agents.llm_qa import main

if __name__ == "__main__":
    asyncio.run(main())
