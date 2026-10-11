"""CLI entry point: ``python -m llm_agents.online_search_summary``."""

import asyncio

from llm_agents.online_search_summary.cli import main

if __name__ == "__main__":
    from _common.service_heartbeat import autoregister  # lazy: keeps pandas behind the cudf hook
    autoregister()  # heartbeat + run audit (monitor.*)
    asyncio.run(main())
