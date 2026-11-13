"""llm_agents.deep_agents.tools.sandbox_tools — the ``sandbox_*`` tools
as OBJECTS over the docker py-sandbox client.

Five tools give the model a private scratch directory INSIDE the
oxpicious-py-sandbox container (the ``sagacient/cutepandas`` py3.12
stack) and the things a scratch directory is for: WRITE a file into it
(a .py script or the data files a script consumes), RUN one of its .py
scripts and read stdout/stderr back, READ one produced file back, EDIT
one existing file in place (a one-block patch — read → replace →
write composed over the middleware's read/write actions), and LIST the
directory. Each tool holds the run's
:class:`~llm_agents.deep_agents.core.sandbox.Sandbox` client (one
remote session per run, minted lazily, shared with spawned subagents —
see that module for the delegation chain and the permission model);
the kit is what the loop and the runner mount.

The schemas' DEFAULT_DESCRIPTION constants are the seed source for the
tools' ``llm.ai_prompts`` rows (prompt_type 4, sub_type 'sandbox' —
``database/sql/llm/static/ai_prompts/10_tool_prompts.sql``); a kit
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
    """Base of the five sandbox tools: every call mints/reuses the
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
        "the data files those scripts consume. Scripts execute "
        "top-level — ONE failing query aborts the whole run — so "
        "structure study scripts with a try/except per query block "
        "that records the error and continues; when a run then "
        "fails, patch the one broken block with sandbox_edit_file "
        "instead of rewriting the file here."
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
        "writes land inside the sandbox. MATCH FIRST — before writing "
        "any calculation function, check what already exists: the "
        "finished study_kit package is importable everywhere "
        "(`import study_kit`). study_kit.db.query(sql) / "
        "fetch_daily_bars(sec_type, code, start, end) for read-only "
        "DB queries and daily bars; study_kit.db.fetch_spread_stats"
        "(sec_type, code) retrieves the MATERIALIZED MA-spread "
        "trailing stats (the 9 price_vs_ma*/ma5_vs_ma* gaps' value + "
        "trailing-252d pct_rank / zscore / stretched verdict, from "
        "analysis.mov_ave_spreads_detail joined to "
        "analysis.mov_ave_spreads_detail_trailing — retrieve, never "
        "recompute); study_kit.db.fetch_amt_metrics(sec_type, code) "
        "retrieves the MATERIALIZED trading-amount metrics (amt MAs, "
        "their Bollinger σ, market share, slopes — the 量能 lane, "
        "analysis.mov_ave_trading_amt; etf/stock/industry only); "
        "study_kit.studies (sma, ema, wilder_rsi, macd, kdj, "
        "bollinger, roc, zscore, trailing_pct_rank, drawdown, "
        "forward_return, forward_blend, ma_alignment, "
        "volume_contrast, spread_report) for the rest. "
        "When a needed calculation matches an existing function or "
        "materialized column, import and call it AS-IS in the script "
        "rather than adding a new function — write new code only for "
        "what the kit and the tables lack."
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


class SandboxEditFileTool(_SandboxTool):
    """``sandbox_edit_file`` — patch ONE exact block of an existing
    sandbox file in place: read → verify → replace → write back,
    composed over the middleware's read/write actions (no middleware
    change). The anti-rewrite-loop tool: a one-line SQL fix costs a
    hundred bytes here instead of a whole-file ``sandbox_write_file``
    resend (the 2026-11-11 run-e1c366bd failure mode — nine 14.5 KB
    rewrites chasing eight separate SQL bugs)."""

    name: ClassVar[str] = "sandbox_edit_file"
    db_key: ClassVar[Tuple[str, str]] = (name, "sandbox")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Sandbox-relative file path.",
            },
            "old_text": {
                "type": "string",
                "description": "The exact text to replace — copy it "
                               "verbatim from the file (indentation "
                               "included; sandbox_read_file the file "
                               "first if you did not write it this "
                               "session).",
            },
            "new_text": {
                "type": "string",
                "description": "The replacement text.",
            },
            "expected_count": {
                "type": "integer",
                "minimum": 1,
                "description": "How many occurrences of old_text the "
                               "file should contain (default 1; pass "
                               "the count you expect when replacing a "
                               "repeated block).",
            },
        },
        "required": ["path", "old_text", "new_text"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Patch an existing sandbox file in place — replace an exact "
        "old_text block with new_text (path sandbox-relative). The "
        "tool reads the file, replaces every verified occurrence "
        "(default: exactly one; pass expected_count for a repeated "
        "block) and writes the file back — a one-block fix costs a "
        "hundred bytes instead of rewriting the whole file with "
        "sandbox_write_file. old_text must match the file "
        "byte-for-byte (indentation included): sandbox_read_file the "
        "file first when you did not write it this session. When a "
        "sandbox_run fails, patch the ONE broken block with this "
        "tool and re-run — never resend the whole file per fix."
    )

    # The read side runs at the middleware's hard read cap
    # (llm_agents/sandbox/runner.py MAX_READ_BYTES, 1 MB); a file this
    # big that STILL truncates cannot be edited safely from its
    # head+tail — the run() guard fails-soft to a whole-file write.
    _READ_MAX_BYTES: ClassVar[int] = 1024 * 1024

    async def run(self, arguments: Dict[str, Any]) -> str:
        session = await self._client.ensure_session()
        path = str(arguments.get("path") or "")
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        if (not path or not isinstance(old_text, str) or not old_text
                or not isinstance(new_text, str)):
            return ("MCP tool error: sandbox_edit_file: path, "
                    "old_text and new_text are required (old_text "
                    "must be a non-empty string)")
        expected = arguments.get("expected_count")
        expected = (int(expected)
                    if isinstance(expected, (int, float)) else 1)
        read = await self._client.call("sandbox_read_file", {
            "session": session,
            "path": path,
            "max_bytes": self._READ_MAX_BYTES,
        })
        if isinstance(read, dict) and read.get("error"):
            return f"MCP tool error: sandbox_edit_file: {read['error']}"
        if not isinstance(read, dict):
            shown = json.dumps(read, ensure_ascii=False, default=str)
            return (f"MCP tool error: sandbox_edit_file: unexpected "
                    f"read result: {shown[:200]}")
        content = str(read.get("content", ""))
        total = int(read.get("bytes") or 0)
        # Truncation is a BYTE-CAP event: the runner head+tail-caps a
        # read ONLY when the file exceeds the cap this tool passes (1 MB,
        # == runner MAX_READ_BYTES) and says so with a literal marker.
        # Comparing bytes against len(content) — a CHAR count —
        # false-positived on every multi-byte UTF-8 file (a single
        # em-dash or CJK comment sufficed): the 2026-11-13 run's 7.7 KB
        # window_diag.py was rejected as "beyond the 1 MB read cap",
        # forcing whole-file rewrites across that run's spawns.
        if (total > self._READ_MAX_BYTES
                or "[truncated: showing first " in content):
            return (f"MCP tool error: sandbox_edit_file: {path} is "
                    f"{total:,} bytes — beyond the 1 MB read cap the "
                    "content came back truncated, so editing from it "
                    "would corrupt the file; rewrite it whole with "
                    "sandbox_write_file instead.")
        count = content.count(old_text)
        if count == 0:
            return (f"old_text not found in {path} ({len(content):,} "
                    "bytes) — sandbox_read_file it and copy old_text "
                    "verbatim (indentation included), then edit again")
        if count != expected:
            return (f"old_text occurs {count} time(s) in {path}, not "
                    f"the expected {expected} — pass "
                    f"expected_count={count}, or include more "
                    "surrounding lines in old_text to disambiguate")
        edited = content.replace(old_text, new_text)
        write = await self._client.call("sandbox_write_file", {
            "session": session,
            "path": path,
            "content": edited,
        })
        if isinstance(write, dict) and write.get("error"):
            return (f"MCP tool error: sandbox_edit_file: write-back "
                    f"failed: {write['error']}")
        return (f"edited {path}: replaced {count} occurrence(s) "
                f"({len(content):,} → {len(edited):,} bytes)")


class SandboxToolSet(ToolKit):
    """The five ``sandbox_*`` tools wired to ONE Sandbox client — the
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
            SandboxEditFileTool(
                self._client,
                description=self.db_description(
                    SandboxEditFileTool.db_key)),
        ]

    def prompt_addendum(self) -> str:
        return SANDBOX_PROMPT_ADDENDUM

    @staticmethod
    def schemas() -> List[Dict[str, Any]]:
        """The five schemas WITHOUT a client (the CLI's --list-tools —
        description overrides need a run's descriptions map; the plain
        constants are the no-DB form)."""
        return [tool_cls(None).schema for tool_cls in (
            SandboxWriteFileTool, SandboxRunTool,
            SandboxReadFileTool, SandboxListFilesTool,
            SandboxEditFileTool)]
