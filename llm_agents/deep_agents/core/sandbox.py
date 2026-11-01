"""llm_agents.deep_agents.core.sandbox — the deep agent's code sandbox,
delegating to the dockerized py-sandbox through the llm_agents.sandbox
MCP middleware.

The deep agent's DB MCP tools are read-only (capped queries, web
search); any real computation happens here. The tools give the model a
private session directory INSIDE the oxpicious-py-sandbox container
(the ``sagacient/cutepandas`` py3.12 stack — pandas, numpy, scipy,
scikit-learn, statsmodels, matplotlib, polars, pyarrow, duckdb, …) and
the four things a scratch directory is for: WRITE a file into it (a
.py script or the data files a script consumes), RUN one of its .py
scripts and read stdout/stderr back, READ one produced file back, and
LIST the directory.

THE DELEGATION CHAIN (replaces the former local subprocess sandbox —
a plain ``python`` child of this interpreter with a whitelisted env):

    these builtin tools (schemas below)
      → llm_agents.sandbox MCP middleware (per-call streamable-HTTP
        session, http://127.0.0.1:8789/mcp — one MCP call per tool
        invocation, stateless between them)
      → the container's runner API (llm_agents/sandbox/runner.py)
      → script subprocess inside the container

The tools ride the DeepAgent's ``builtin_tools`` slot
(``Sandbox.builtin_tools()``); the runner mints one sandbox per run
and spawned subagents inherit it — parent and children share ONE
remote session (minted lazily on the first tool call, under a lock so
parallel subagent calls cannot mint two).

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
from typing import TYPE_CHECKING, Any, Dict, Optional

from llm_agents.deep_agents.core.mcp import McpToolClient

if TYPE_CHECKING:
    from llm_agents.deep_agents.core.tools import BuiltinTool

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
    "impossible with those credentials. Print results concisely "
    "(output is capped); write anything large to a sandbox file and "
    "read it back. Keep ALL scratch work inside the sandbox session. "
    "For KEYWORD / content search over the text schema (news, Q&A), "
    "call the text_search MCP tool instead of SQL ILIKE or a sandbox "
    "script: the corpus is Chinese and only the pre-segmented, "
    "GIN-indexed search_tsv matches it — a sandbox script cannot run "
    "the segmenter, and raw Chinese text in a hand-written query "
    "matches nothing."
)

_WRITE_FILE_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "sandbox_write_file",
        "description": (
            "Write a text file into your private sandbox scratch "
            "directory (paths are sandbox-relative, e.g. 'study.py' or "
            "'out/dates.csv'; no '..'; UTF-8, 512 KB cap). Use it for "
            "the .py scripts you will run with sandbox_run and for the "
            "data files those scripts consume."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Sandbox-relative file path.",
                },
                "content": {
                    "type": "string",
                    "description": "The file's full text content.",
                },
            },
            "required": ["path", "content"],
        },
    },
}

_RUN_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "sandbox_run",
        "description": (
            "Execute one .py file you previously wrote into the "
            "sandbox, inside the docker py-sandbox (py3.12 + "
            "pandas/numpy/scipy/scikit-learn/statsmodels/polars/"
            "pyarrow/duckdb); the script's working directory IS the "
            "sandbox directory. Returns JSON {script, exit_code, "
            "timed_out, duration_s, db, stdout, stderr} with output "
            "capped. The environment is a minimal passthrough plus the "
            "READ-ONLY database credentials: PGHOST / PGPORT / "
            "PGDATABASE / PGUSER / PGPASSWORD point at the "
            "readonly_analytics role, so `psycopg.connect()` connects "
            "with SELECT-only rights and DB writes fail. Sibling files "
            "in the sandbox are importable; relative paths the script "
            "writes land inside the sandbox."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Sandbox-relative path of the .py "
                                   "script to execute.",
                },
                "timeout_s": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 600,
                    "description": "Wall-clock seconds before the "
                                   "script is killed (default 120).",
                },
            },
            "required": ["path"],
        },
    },
}

_READ_FILE_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "sandbox_read_file",
        "description": (
            "Read one file back out of the sandbox directory (the "
            "files a script wrote, logs, …) as UTF-8 text; files "
            "beyond max_bytes return head+tail with a truncation "
            "marker. Paths are sandbox-relative."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Sandbox-relative file path.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Read cap in bytes (default 65536).",
                },
            },
            "required": ["path"],
        },
    },
}

_LIST_FILES_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "sandbox_list_files",
        "description": (
            "List every file currently in the sandbox directory — "
            "JSON array of {path, bytes, modified}, sorted by path."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

# For listing (--list-tools) without a middleware round trip.
SANDBOX_TOOL_SCHEMAS = (
    _WRITE_FILE_SCHEMA, _RUN_SCHEMA, _READ_FILE_SCHEMA, _LIST_FILES_SCHEMA,
)


class Sandbox:
    """One per-run REMOTE sandbox session plus its four built-in tools.

    Constructed per run (the runner's execute node, or the CLI); the
    REMOTE session is minted lazily on the first tool call (so a run
    that never computes never touches the middleware) and cached —
    every later tool call reuses it. Pass the instance to
    ``DeepAgent(sandbox=...)`` — the agent merges ``builtin_tools()``
    into its toolset, appends ``SANDBOX_PROMPT_ADDENDUM`` to its
    system prompt and hands the SAME instance to spawned subagents
    (one shared session, like the old shared scratch directory).

    Every tool call opens ONE short-lived MCP session to the
    middleware (``McpToolClient``) — stateless between calls, no
    connection to close at run end; the middleware holds the docker
    side.
    """

    def __init__(self, mcp_url: str = DEFAULT_SANDBOX_MCP_URL) -> None:
        self._mcp_url = mcp_url
        self._session: Optional[str] = None
        self._session_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # The remote session (one per run, minted under a lock)
    # ------------------------------------------------------------------

    async def _mint_session(self) -> str:
        """One remote session id via the middleware's
        sandbox_new_session tool; cached for the run's life. A
        middleware/container outage surfaces as error text in every
        tool result — the run survives without computation."""
        async with self._session_lock:
            if self._session is not None:
                return self._session
            result = await self._call("sandbox_new_session", {})
            if isinstance(result, dict) and result.get("session"):
                self._session = str(result["session"])
                logger.info("sandbox session minted: %s", self._session)
                return self._session
            raise RuntimeError(
                "sandbox_new_session failed: "
                + json.dumps(result, ensure_ascii=False, default=str))

    async def _call(self, tool: str,
                    arguments: Dict[str, Any]) -> Any:
        """One MCP tool call on a fresh session to the middleware;
        JSON text results are parsed back so handlers see dicts."""
        async with McpToolClient(url=self._mcp_url) as client:
            text = await client.call_tool(tool, arguments)
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return text

    # ------------------------------------------------------------------
    # The DeepAgent built-in tools
    # ------------------------------------------------------------------

    def builtin_tools(self) -> Dict[str, "BuiltinTool"]:
        """The four ``sandbox_*`` tools wired to the remote session,
        in the DeepAgent ``builtin_tools`` shape. Handlers never raise
        into the loop — middleware/docker failures return error text
        (the agent's own builtin catch is the backstop)."""
        from llm_agents.deep_agents.core.tools import BuiltinTool

        async def write_handler(args: Dict[str, Any]) -> str:
            session = await self._mint_session()
            result = await self._call("sandbox_write_file", {
                "session": session,
                "path": str(args.get("path") or ""),
                "content": args.get("content"),
            })
            if isinstance(result, dict) and result.get("error"):
                return f"MCP tool error: sandbox_write_file: " \
                       f"{result['error']}"
            if isinstance(result, dict) and result.get("bytes") is not None:
                return (f"wrote {result['path']} ({result['bytes']} "
                        "bytes) into the sandbox")
            return json.dumps(result, ensure_ascii=False, default=str)

        async def run_handler(args: Dict[str, Any]) -> str:
            session = await self._mint_session()
            timeout_s = args.get("timeout_s")
            result = await self._call("sandbox_run", {
                "session": session,
                "path": str(args.get("path") or ""),
                **({"timeout_s": int(timeout_s)}
                   if isinstance(timeout_s, (int, float)) else {}),
            })
            if isinstance(result, dict) and result.get("error"):
                return f"MCP tool error: sandbox_run: {result['error']}"
            return json.dumps(result, ensure_ascii=False, default=str)

        async def read_handler(args: Dict[str, Any]) -> str:
            session = await self._mint_session()
            max_bytes = args.get("max_bytes")
            result = await self._call("sandbox_read_file", {
                "session": session,
                "path": str(args.get("path") or ""),
                **({"max_bytes": int(max_bytes)}
                   if isinstance(max_bytes, (int, float)) else {}),
            })
            if isinstance(result, dict):
                if result.get("error"):
                    return (f"MCP tool error: sandbox_read_file: "
                            f"{result['error']}")
                return str(result.get("content", ""))
            return json.dumps(result, ensure_ascii=False, default=str)

        async def list_handler(_args: Dict[str, Any]) -> str:
            session = await self._mint_session()
            result = await self._call("sandbox_list_files",
                                      {"session": session})
            return (result if isinstance(result, str)
                    else json.dumps(result, ensure_ascii=False,
                                    default=str))

        return {
            "sandbox_write_file": BuiltinTool(
                schema=_WRITE_FILE_SCHEMA, handler=write_handler),
            "sandbox_run": BuiltinTool(
                schema=_RUN_SCHEMA, handler=run_handler),
            "sandbox_read_file": BuiltinTool(
                schema=_READ_FILE_SCHEMA, handler=read_handler),
            "sandbox_list_files": BuiltinTool(
                schema=_LIST_FILES_SCHEMA, handler=list_handler),
        }
