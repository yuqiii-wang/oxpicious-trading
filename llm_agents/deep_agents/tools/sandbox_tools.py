"""llm_agents.deep_agents.tools.sandbox_tools — the ``sandbox_*`` tools
as OBJECTS over the docker py-sandbox client.

Four tools give the model a private scratch directory INSIDE the
oxpicious-py-sandbox container (the ``sagacient/cutepandas`` py3.12
stack) and the four things a scratch directory is for: WRITE a file
into it (a .py script or the data files a script consumes), RUN one of
its .py scripts and read stdout/stderr back, READ one produced file
back, and LIST the directory. Each tool holds the run's
:class:`~llm_agents.deep_agents.core.sandbox.Sandbox` client (one
remote session per run, minted lazily, shared with spawned subagents —
see that module for the delegation chain and the permission model);
the kit is what the loop and the runner mount.

The schemas' DEFAULT_DESCRIPTION constants are the seed source for the
tools' ``llm.ai_prompts`` rows (prompt_type 4, sub_type 'sandbox' —
``database/sql/text/llm/static/ai_prompts/10_tool_prompts.sql``); a kit
constructed with the run's resolved descriptions map overrides them
with the DB rows' instructions (fail-soft to the constants).
``SANDBOX_PROMPT_ADDENDUM`` (defined on the client module — it teaches
the sandbox ENVIRONMENT contract) is the kit's system-prompt addendum.
"""
from __future__ import annotations

import json
from typing import Any, ClassVar, Dict, List, Optional, Tuple

from llm_agents.deep_agents.core.sandbox import (
    SANDBOX_PROMPT_ADDENDUM, Sandbox,
)
from llm_agents.deep_agents.tools.base import Tool, ToolKit


class _SandboxTool(Tool):
    """Base of the four sandbox tools: every call mints/reuses the
    client's remote session, then makes ONE middleware call. Middleware
    or docker failures return error text (the agent's own builtin catch
    is the backstop) — handlers never raise into the loop. ``client``
    is None only for schema-only construction (``SandboxToolSet
    .schemas`` — the --list-tools no-client form)."""

    def __init__(self, client: Optional[Sandbox], *,
                 description: Optional[str] = None) -> None:
        super().__init__(description=description)
        self._client = client


class SandboxWriteFileTool(_SandboxTool):
    """``sandbox_write_file`` — write a text file into the scratch
    directory (a script or the data it consumes)."""

    name: ClassVar[str] = "sandbox_write_file"
    db_key: ClassVar[Tuple[str, str]] = (name, "sandbox")
    parameters: ClassVar[Dict[str, Any]] = {
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
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Write a text file into your private sandbox scratch "
        "directory (paths are sandbox-relative, e.g. "
        "'scripts/study.py' or 'data/dates.csv'; no '..'; UTF-8, "
        "512 KB cap). The session workspace is minted with the "
        "standard layout: scripts/ for your .py files, data/ for "
        "fetched or intermediate files, outputs/ for final results, "
        "figures/ for plots — put each file in its lane. Use the "
        "tool for the scripts you will run with sandbox_run and for "
        "the data files those scripts consume."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        session = await self._client.ensure_session()
        result = await self._client.call("sandbox_write_file", {
            "session": session,
            "path": str(arguments.get("path") or ""),
            "content": arguments.get("content"),
        })
        if isinstance(result, dict) and result.get("error"):
            return (f"MCP tool error: sandbox_write_file: "
                    f"{result['error']}")
        if isinstance(result, dict) and result.get("bytes") is not None:
            return (f"wrote {result['path']} ({result['bytes']} "
                    "bytes) into the sandbox")
        return json.dumps(result, ensure_ascii=False, default=str)


class SandboxRunTool(_SandboxTool):
    """``sandbox_run`` — execute one written .py script inside the
    container (timeout-capped, read-only DB env)."""

    name: ClassVar[str] = "sandbox_run"
    db_key: ClassVar[Tuple[str, str]] = (name, "sandbox")
    parameters: ClassVar[Dict[str, Any]] = {
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
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
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
        "writes land inside the sandbox. The finished study_kit "
        "package is importable everywhere (`import study_kit`): "
        "study_kit.db.query(sql) / fetch_daily_bars(sec_type, code, "
        "start, end) for read-only DB queries and daily bars, "
        "study_kit.studies (sma, ema, wilder_rsi, macd, roc, zscore, "
        "trailing_pct_rank, drawdown, forward_return, forward_blend, "
        "ma_alignment, spread_report) for the standard calculations — "
        "use the kit instead of re-deriving them per script."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        session = await self._client.ensure_session()
        timeout_s = arguments.get("timeout_s")
        result = await self._client.call("sandbox_run", {
            "session": session,
            "path": str(arguments.get("path") or ""),
            **({"timeout_s": int(timeout_s)}
               if isinstance(timeout_s, (int, float)) else {}),
        })
        if isinstance(result, dict) and result.get("error"):
            return f"MCP tool error: sandbox_run: {result['error']}"
        return json.dumps(result, ensure_ascii=False, default=str)


class SandboxReadFileTool(_SandboxTool):
    """``sandbox_read_file`` — read one file back out of the scratch
    directory (bytes-capped, head+tail with a marker)."""

    name: ClassVar[str] = "sandbox_read_file"
    db_key: ClassVar[Tuple[str, str]] = (name, "sandbox")
    parameters: ClassVar[Dict[str, Any]] = {
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
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Read one file back out of the sandbox directory (the "
        "files a script wrote, logs, …) as UTF-8 text; files "
        "beyond max_bytes return head+tail with a truncation "
        "marker. Paths are sandbox-relative."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        session = await self._client.ensure_session()
        max_bytes = arguments.get("max_bytes")
        result = await self._client.call("sandbox_read_file", {
            "session": session,
            "path": str(arguments.get("path") or ""),
            **({"max_bytes": int(max_bytes)}
               if isinstance(max_bytes, (int, float)) else {}),
        })
        if isinstance(result, dict):
            if result.get("error"):
                return (f"MCP tool error: sandbox_read_file: "
                        f"{result['error']}")
            return str(result.get("content", ""))
        return json.dumps(result, ensure_ascii=False, default=str)


class SandboxListFilesTool(_SandboxTool):
    """``sandbox_list_files`` — list the scratch directory."""

    name: ClassVar[str] = "sandbox_list_files"
    db_key: ClassVar[Tuple[str, str]] = (name, "sandbox")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {},
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "List every file currently in the sandbox directory — "
        "JSON array of {path, bytes, modified}, sorted by path."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        session = await self._client.ensure_session()
        result = await self._client.call("sandbox_list_files",
                                         {"session": session})
        return (result if isinstance(result, str)
                else json.dumps(result, ensure_ascii=False,
                                default=str))


class SandboxToolSet(ToolKit):
    """The four ``sandbox_*`` tools wired to ONE Sandbox client — the
    kit the runner/CLI mount (the loop mounts it itself when an agent
    is constructed with ``sandbox=``).

    ``descriptions`` (the run's resolved ``llm.ai_prompts`` tool rows,
    prompt_type 4) overrides each tool's schema description with its DB
    row's instruction — fail-soft to the built-in constants. The kit's
    system-prompt addendum is the sandbox usage contract
    (``core.sandbox.SANDBOX_PROMPT_ADDENDUM``)."""

    def __init__(self, client: Sandbox, *,
                 descriptions: Optional[Dict[str, str]] = None) -> None:
        super().__init__(descriptions)
        self._client = client

    def tools(self) -> List[Tool]:
        return [
            SandboxWriteFileTool(
                self._client,
                description=self.db_description(
                    SandboxWriteFileTool.db_key)),
            SandboxRunTool(
                self._client,
                description=self.db_description(SandboxRunTool.db_key)),
            SandboxReadFileTool(
                self._client,
                description=self.db_description(
                    SandboxReadFileTool.db_key)),
            SandboxListFilesTool(
                self._client,
                description=self.db_description(
                    SandboxListFilesTool.db_key)),
        ]

    def prompt_addendum(self) -> str:
        return SANDBOX_PROMPT_ADDENDUM

    @staticmethod
    def schemas() -> List[Dict[str, Any]]:
        """The four schemas WITHOUT a client (the CLI's --list-tools —
        description overrides need a run's descriptions map; the plain
        constants are the no-DB form)."""
        return [tool_cls(None).schema for tool_cls in (
            SandboxWriteFileTool, SandboxRunTool,
            SandboxReadFileTool, SandboxListFilesTool)]
