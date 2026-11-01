"""llm_agents.deep_agents.cli — argparse CLI.

Usage::

    python -m llm_agents.deep_agents "which tables are in the stats schema?"
    python -m llm_agents.deep_agents --stdio "…"            # own server subprocess
    python -m llm_agents.deep_agents --list-tools           # show MCP tools, no LLM call
    python -m llm_agents.deep_agents "…" --show-transcript  # also print each tool call
    python -m llm_agents.deep_agents "…" --data '{"k": "v"}' --json

``--mcp-url`` attaches to a running MCP server (default: the repo's
read-only DB daemon at http://127.0.0.1:8765/mcp); ``--stdio`` spawns
``python -m llm_agents.mcp --stdio`` as a child process instead — the
two are mutually exclusive.

The agent carries one BUILT-IN tool beside the MCP ones:
``spawn_subagent`` — the model can delegate self-contained sub-goals
to child agents (same provider and MCP tools, no further spawning,
fresh context); sibling calls run in parallel and each child's final
answer returns as the tool result. The PARENT owns the supervision
policy — ``--subagent-timeout`` bounds each child's whole life,
``--max-concurrent-subagents`` caps the fan-out, ``--no-subagents``
removes the tool. ``--show-transcript`` also prints the subagent
registry (id / status / steps / goal).

The agent also carries a CODE SANDBOX by default
(``llm_agents.deep_agents.core.sandbox``): four ``sandbox_*`` built-in
tools giving the model a private session directory inside the
DOCKERIZED py sandbox (the ``sagacient/cutepandas`` py3.12 stack),
reached through the ``llm_agents.sandbox`` MCP middleware — write
files, run .py scripts inside the container (killed at timeout, capped
output; the DB env is the READ-ONLY ``readonly_analytics`` role), and
read/list results back. Spawned subagents share the parent's session.
``--no-sandbox`` removes the tools for the run.

``--data`` / ``--data-file`` attach an arbitrary JSON OBJECT as the run's
structured context (a fenced block appended to the goal — the agent reads
it like user-message content); the file form exists for payloads too big
for one argv (the Windows ~32k command-line limit) and is deleted after
reading. ``--json`` prints the result as one ``@@DEEP_AGENT_JSON@@{…}``
line on stdout for the API bridge to parse.
"""
from __future__ import annotations

# Network-only agent (LLM POST + MCP session, no pandas/cudf) — same
# light resource gate as llm_ask.
from _common.pre_check import pre_check

pre_check(min_sys_gb=4, require_gpu=False)

from _common.build_commons import setup_utf8_stdout  # noqa: E402

setup_utf8_stdout()

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, Optional  # noqa: E402

from _common.log_setup import setup_logging  # noqa: E402

from llm_agents.deep_agents.core.delegation import (  # noqa: E402
    COLLECT_SUBAGENTS_SCHEMA,
    SUBAGENT_TOOL_SCHEMA,
)
from llm_agents.deep_agents.core.mcp import DEFAULT_MCP_URL, McpToolClient  # noqa: E402
from llm_agents.deep_agents.core.provider import ZhipuChatProvider  # noqa: E402
from llm_agents.deep_agents.core.sandbox import (  # noqa: E402
    SANDBOX_TOOL_SCHEMAS, Sandbox,
)

logger = setup_logging("deep_agents")

# Machine-readable result marker (the llm_ask contract — last marker of
# stdout is the result envelope).
RESULT_MARKER = "@@DEEP_AGENT_JSON@@"


def load_data(args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    """The --data / --data-file payload as a dict (None when neither given)."""
    if args.data and args.data_file:
        raise SystemExit("--data and --data-file are mutually exclusive")
    raw: Optional[str] = None
    if args.data_file:
        try:
            with open(args.data_file, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except OSError as exc:
            raise SystemExit(f"--data-file is not readable: {exc}")
    elif args.data:
        raw = args.data
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise SystemExit(f"--data/--data-file is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise SystemExit("--data/--data-file must hold a JSON object")
    return data


async def main() -> None:
    ap = argparse.ArgumentParser(
        prog="python -m llm_agents.deep_agents",
        description="Minimalist tool-calling agent: LLM loop over the "
                    "read-only DB MCP server.")
    ap.add_argument("question", nargs="?", default=None,
                    help="The goal the agent pursues (omit with "
                         "--list-tools).")
    transport = ap.add_mutually_exclusive_group()
    transport.add_argument("--mcp-url", default=DEFAULT_MCP_URL,
                           help="Streamable-HTTP URL of a running MCP "
                                "server (default: %(default)s).")
    transport.add_argument("--stdio", action="store_true",
                           help="Spawn 'python -m llm_agents.mcp --stdio' "
                                "as a subprocess and talk over "
                                "stdin/stdout instead of HTTP.")
    ap.add_argument("--model", default=None,
                    help="Chat model (provider default glm-5.3-flash).")
    ap.add_argument("--max-steps", type=int, default=20,
                    help="Tool-calling steps before giving up "
                         "(default: %(default)s; generous since every "
                         "request carries the provider's per-request "
                         "token ceiling).")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="Per-request output ceiling, max_tokens "
                         "(default: the provider's 8192 — a loose "
                         "runaway guard, not a length prescription).")
    ap.add_argument("--system", default=None,
                    help="Override the default system prompt.")
    ap.add_argument("--temperature", type=float, default=None,
                    help="Sampling temperature within [0, 1] "
                         "(provider default).")
    ap.add_argument("--data", default=None,
                    help="Structured context for the run: one JSON object "
                         "string, attached to the goal verbatim.")
    ap.add_argument("--data-file", default=None,
                    help="File holding the --data JSON (large payloads; "
                         "deleted after reading).")
    ap.add_argument("--no-subagents", action="store_true",
                    help="Remove the built-in spawn_subagent tool: the "
                         "model works inline only.")
    ap.add_argument("--no-sandbox", action="store_true",
                    help="Remove the sandbox_* built-in tools: no "
                         "scratch-dir code execution this run.")
    ap.add_argument("--subagent-timeout", type=float, default=None,
                    help="Wall-clock seconds before a spawned subagent is "
                         "stopped (default: unbounded).")
    ap.add_argument("--max-concurrent-subagents", type=int, default=4,
                    help="How many subagents may run at once; extras wait "
                         "queued (default: %(default)s).")
    ap.add_argument("--json", action="store_true",
                    help="Print a @@DEEP_AGENT_JSON@@{…} result line on "
                         "stdout (the API bridge's parse marker).")
    ap.add_argument("--list-tools", action="store_true",
                    help="List the MCP server's tools and exit (no LLM "
                         "call, no API key needed).")
    ap.add_argument("--show-transcript", action="store_true",
                    help="Log every tool call and its result too.")
    args = ap.parse_args()

    if not args.list_tools and not args.question:
        ap.error("a question is required unless --list-tools is given")
    data = load_data(args)

    if args.stdio:
        mcp = McpToolClient(
            stdio_command=[sys.executable, "-u", "-m", "llm_agents.mcp",
                           "--stdio"])
    else:
        mcp = McpToolClient(url=args.mcp_url)

    async with mcp:
        if args.list_tools:
            for tool in await mcp.tool_schemas():
                function = tool["function"]
                logger.info("tool %s: %s", function["name"],
                            function["description"].splitlines()[0])
            # The built-in spawner is part of the agent's effective
            # toolset (unless disabled) — show it with the MCP ones.
            if not args.no_subagents:
                function = SUBAGENT_TOOL_SCHEMA["function"]
                logger.info("tool %s (built-in): %s", function["name"],
                            function["description"].splitlines()[0])
                function = COLLECT_SUBAGENTS_SCHEMA["function"]
                logger.info("tool %s (built-in): %s", function["name"],
                            function["description"].splitlines()[0])
            if not args.no_sandbox:
                for schema in SANDBOX_TOOL_SCHEMAS:
                    function = schema["function"]
                    logger.info("tool %s (built-in): %s", function["name"],
                                function["description"].splitlines()[0])
            return
        # Imported here (not module-top) so --list-tools works without
        # an API key — the provider resolves one at construction.
        from llm_agents.deep_agents.core.loop import DeepAgent

        agent = DeepAgent(
            ZhipuChatProvider(max_output_tokens=args.max_tokens), mcp,
            model=args.model,
            max_steps=args.max_steps, system=args.system,
            temperature=args.temperature,
            allow_subagents=not args.no_subagents,
            sandbox=None if args.no_sandbox else Sandbox(),
            subagent_timeout=args.subagent_timeout,
            max_concurrent_subagents=args.max_concurrent_subagents)
        result = await agent.run(args.question, data=data)
    # Session closed BEFORE the output section: --json's marker must be
    # the LAST thing on stdout (the MCP teardown logs would otherwise
    # follow it and break a stdout-tail JSON parse).
    # The payload file's one consumer has read it — same backstop-free
    # cleanup contract as the llm_ask payload files.
    if args.data_file:
        try:
            os.remove(args.data_file)
        except OSError:
            pass
    if args.show_transcript:
        for run in result.subagent_runs:
            logger.info("subagent %s [%s] steps=%d tool_calls=%d goal=%s",
                        run.run_id, run.status, run.steps,
                        run.tool_call_count, run.request.goal)
        for call in result.tool_calls:
            logger.info("transcript step %d %s ->\n%s", call["step"],
                        call["name"], call["output"])
    logger.info("ANSWER (%d steps, %d tool calls, %d subagent(s)):\n%s",
                result.steps, len(result.tool_calls),
                len(result.subagent_runs), result.answer)
    if args.json:
        print(RESULT_MARKER + json.dumps(
            {"answer": result.answer, "steps": result.steps,
             "n_tool_calls": len(result.tool_calls),
             "model": args.model or ZhipuChatProvider.DEFAULT_MODEL},
            ensure_ascii=False))
