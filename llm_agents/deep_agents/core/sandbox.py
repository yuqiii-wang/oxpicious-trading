"""llm_agents.deep_agents.core.sandbox — the deep agent's code-sandbox
CLIENT, delegating to the dockerized py-sandbox through the
llm_agents.sandbox MCP middleware.

The deep agent's DB MCP tools are read-only (capped queries, web
search); any real computation happens here. The model reaches the
sandbox through the four ``sandbox_*`` TOOL OBJECTS
(``tools.sandbox_tools`` — write a file, run a script, read a file,
list the directory), each holding THIS client: the tools give the model
a private session directory INSIDE the oxpicious-py-sandbox container
(the ``sagacient/cutepandas`` py3.12 stack — pandas, numpy, scipy,
scikit-learn, statsmodels, matplotlib, polars, pyarrow, duckdb, …).

THE DELEGATION CHAIN (replaces the former local subprocess sandbox —
a plain ``python`` child of this interpreter with a whitelisted env):

    the sandbox_* tools (tools.sandbox_tools)
      → llm_agents.sandbox MCP middleware (per-call streamable-HTTP
        session, http://127.0.0.1:8789/mcp — one MCP call per tool
        invocation, stateless between them)
      → the container's runner API (llm_agents/sandbox/runner.py)
      → script subprocess inside the container

The tools ride the DeepAgent via ``SandboxToolSet`` (constructed on
this client — the runner mints one Sandbox per run and spawned
subagents inherit it, so parent and children share ONE remote session,
minted lazily on the first tool call under a lock so parallel subagent
calls cannot mint two).

PERMISSIONS — the container IS the isolation boundary the old local
design disclaimed ("hard isolation is the follow-up if one is ever
needed" — this is it):

  * filesystem/network — a script sees the container and its compose
    network, NOT the host repo; the only published surface is the
    runner API on host loopback.
  * DB — READ-ONLY, enforced by the DATABASE: the compose service
    presets the libpq PG* env to the ``readonly_analytics`` role
    (SELECT-only on the data schemas, role-level
    ``default_transaction_read_only = on`` —
    ``database/sql/00_readonly_role.sql``), so a plain
    ``import psycopg; psycopg.connect()`` in a sandbox script connects
    read-only and every write attempt fails server-side.
  * time & size — the runner kills a script at its timeout (default
    120s, hard cap 600s), caps stdout/stderr (head+tail with a
    marker), caps one written file at 512 KB and one read at 1 MB;
    this module adds no further caps of its own.

LIVENESS: the container is kept always-on by the compose restart
policy + HEALTHCHECK, the middleware daemon by the stream watchdog
(heartbeat row ``llm_agents.sandbox``), and the middleware self-heals
a stopped container with ``docker start`` (docker_api.py). A sandbox
tool call still surfaces an {"error": …} result when the whole chain
is down — the run survives, only computation is unavailable.

No cudf/pandas in this module graph: the sandbox moves file text and
JSON-able results only — dataframe work happens inside the container.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict

from llm_agents.deep_agents.core.mcp import McpToolClient

logger = logging.getLogger(__name__)

DEFAULT_SANDBOX_MCP_URL = "http://127.0.0.1:8789/mcp"

SANDBOX_PROMPT_ADDENDUM = (
    "CODE SANDBOX — you also have a private scratch directory for real "
    "computation, running in a DOCKERIZED Python 3.12 sandbox with the "
    "full data-science stack (pandas, numpy, scipy, scikit-learn, "
    "statsmodels, matplotlib, seaborn, polars, pyarrow, duckdb, "
    "openpyxl, requests): sandbox_write_file writes files into it "
    "(Python scripts and the data files they read), sandbox_run "
    "executes one of its .py scripts and returns stdout/stderr, "
    "sandbox_read_file / sandbox_list_files read results back. Prefer "
    "ONE script over many small MCP queries whenever the work is "
    "computational (aggregation, statistics, cross-checking): a "
    "script can open the database ITSELF — the environment presets "
    "PGHOST / PGPORT / PGDATABASE / PGUSER / PGPASSWORD to the "
    "READ-ONLY readonly_analytics role, so `import psycopg; "
    "psycopg.connect()` connects SELECT-only and writing to the DB is "
    "impossible with those credentials. The finished study_kit package "
    "is importable in every script (`import study_kit`): "
    "study_kit.db.query / fetch_daily_bars for read-only queries and "
    "daily bars, study_kit.studies for the standard calculations (RSI, "
    "MACD, trailing percentiles, MA spreads, forward returns) — use "
    "it instead of re-deriving them. The session workspace carries "
    "the standard layout sub dirs: keep .py scripts in scripts/, "
    "fetched or intermediate files in data/, final results in "
    "outputs/, plots in figures/. Print results concisely "
    "(output is capped); write anything large to a sandbox file and "
    "read it back. Keep ALL scratch work inside the sandbox session. "
    "For KEYWORD / content search over the text/llm schemas (news corpus, Q&A), "
    "call the text_search MCP tool instead of SQL ILIKE or a sandbox "
    "script: the corpus is Chinese and only the pre-segmented, "
    "GIN-indexed search_tsv matches it — a sandbox script cannot run "
    "the segmenter, and raw Chinese text in a hand-written query "
    "matches nothing."
)


class Sandbox:
    """One per-run REMOTE sandbox session — the client the sandbox_*
    tools (``tools.sandbox_tools.SandboxToolSet``) call through.

    Constructed per run (the runner's step node, or the CLI); the
    REMOTE session is minted lazily on the first tool call (so a run
    that never computes never touches the middleware) and cached —
    every later tool call reuses it. The tool kit constructed on this
    instance merges into the agent's toolset and the SAME instance is
    handed to spawned subagents (one shared session, like the old
    shared scratch directory).

    Every call opens ONE short-lived MCP session to the middleware
    (``McpToolClient``) — stateless between calls, no connection to
    close at run end; the middleware holds the docker side.
    """

    def __init__(self, mcp_url: str = DEFAULT_SANDBOX_MCP_URL) -> None:
        self._mcp_url = mcp_url
        self._session: Any = None
        self._session_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # The remote session (one per run, minted under a lock)
    # ------------------------------------------------------------------

    async def ensure_session(self) -> str:
        """One remote session id via the middleware's
        sandbox_new_session tool; cached for the run's life. A
        middleware/container outage surfaces as error text in every
        tool result — the run survives without computation."""
        async with self._session_lock:
            if self._session is not None:
                return str(self._session)
            result = await self.call("sandbox_new_session", {})
            if isinstance(result, dict) and result.get("session"):
                self._session = str(result["session"])
                logger.info("sandbox session minted: %s", self._session)
                return self._session
            raise RuntimeError(
                "sandbox_new_session failed: "
                + json.dumps(result, ensure_ascii=False, default=str))

    async def call(self, tool: str,
                   arguments: Dict[str, Any]) -> Any:
        """One MCP tool call on a fresh session to the middleware;
        JSON text results are parsed back so tool handlers see
        dicts."""
        async with McpToolClient(url=self._mcp_url) as client:
            text = await client.call_tool(tool, arguments)
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return text
