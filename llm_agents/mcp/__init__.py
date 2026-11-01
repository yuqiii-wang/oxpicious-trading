"""llm_agents.mcp — MCP server exposing read-only DB queries and live
web search for this repo.

Two modes (``python -m llm_agents.mcp``):

  - daemon (no flags): always-on streamable HTTP on 127.0.0.1:8765/mcp —
    the mode the stream watchdog spawns and supervises (heartbeats to
    monitor.service_heartbeat). Clients connect via the URL.
  - ``--stdio``: serve ONE client over stdin/stdout — the mode an MCP
    client launches as a subprocess, e.g.::

    wsl -d Ubuntu-22.04 -- bash -lc "source ~/miniconda3/etc/profile.d/conda.sh && conda activate base && cd /mnt/e/oxpicious-trading && python -m llm_agents.mcp --stdio"

Five tools in ``readonly_server.py`` (``DbReadonlyServer``): the DB
tools (db_run_query / db_list_tables / db_describe_table) connect as
the ``readonly_analytics`` role (see
``database/sql/00_readonly_role.sql``): SELECT-only on the data
schemas, with a role-level read-only default the role itself cannot
turn off. The web tools (web_search / web_summarize) reuse the
``llm_agents.online_search_summary`` providers (ZhiPu web search + LLM
summary) so MCP clients — the deep agent first — can search the open
web next to the DB; they persist nothing (text.* storage stays with
that package's CLI ``--store`` path).

Sits inside ``llm_agents`` because the repo's own agent workspace is
its first consumer (``llm_agents.deep_agents`` connects to it as an
MCP client); also keeps the top-level ``mcp`` name free for the
installed MCP SDK (the former repo-root ``mcp/`` package shadowed it).
"""
