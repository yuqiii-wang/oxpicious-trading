"""llm_agents.deep_agents.core.loop.tool_exec — ONE tool call.

The loop's tool-execution seam (split out of the former flat
``core/loop.py``, 2026-11-15): ``_execute_call`` dispatches one
model-proposed call — the built-in spawner/collector names, the
builtin tools (``BuiltinTool`` handlers), then the MCP fallback — and
``_scope_check`` is the authorization gate's seam (every gated MCP
call is checked BEFORE the shared daemon sees it). Beside them: the
failure classifier the repeat guard reads (``_tool_output_failed``)
and the oversized-result preview seam (``_preview_tool_output`` /
``_classify_envelope`` — the metadata+preview layer of 2026-11-12, the
soft jev classification riding a TEXT envelope when an engine is
injected).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from llm_agents.deep_agents.core.authorization import (
    GATED_MCP_TOOLS, ScopeViolation,
)
from llm_agents.deep_agents.core.delegation import (
    COLLECT_TOOL_NAME,
    FORK_TOOL_NAME,
    SUBAGENT_TOOL_NAME,
)
from llm_agents.deep_agents.limits import run_limits

logger = logging.getLogger(__name__)


class _ToolExecutor:
    """One tool call: MCP tool or the built-in spawner; composed into
    ``DeepAgent``."""

    async def _execute_call(self, call: Dict[str, Any],
                            step: int, *,
                            reply_cut: bool = False,
                            transcript: Optional[List[Dict[str, Any]]] = None,
                            ) -> Tuple[str, str]:
        """Run one tool call; return (tool name, result text for the LLM).
        ``reply_cut``: this response ended on the per-request output
        ceiling (finish_reason 'length') — a cut arguments string is
        the ceiling's fault, and the error text teaches the resend
        remedy instead of a bare JSON parse error. ``transcript`` —
        this conversation's messages so far, read ONLY by the fork
        spawner (the fork child deep-copies it; the list itself is
        never mutated)."""
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        try:
            arguments = json.loads(function.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be a JSON object")
        except ValueError as exc:
            if reply_cut:
                return (name,
                        f"MCP tool error: bad arguments for {name}: {exc} "
                        "— your previous reply hit the max output tokens "
                        "mid tool-call and the arguments JSON was cut. "
                        "Resend the SAME call with SHORTER arguments: "
                        "split a large file write into blocks, or patch "
                        "one block with sandbox_edit_file instead of "
                        "resending the whole content.")
            return name, f"MCP tool error: bad arguments for {name}: {exc}"
        if name == SUBAGENT_TOOL_NAME:
            logger.info("step %d/%d: spawn subagent(%s)", step,
                        self._max_steps,
                        json.dumps(arguments, ensure_ascii=False))
            return name, await self._run_subagent(arguments)
        if name == FORK_TOOL_NAME:
            logger.info("step %d/%d: fork subagent(%s)", step,
                        self._max_steps,
                        json.dumps(arguments, ensure_ascii=False))
            return name, await self._run_fork_subagent(arguments,
                                                       transcript)
        if name == COLLECT_TOOL_NAME:
            logger.info("step %d/%d: collect_subagents(%s)", step,
                        self._max_steps,
                        json.dumps(arguments, ensure_ascii=False))
            return name, await self._collect_subagents(arguments)
        builtin = self._builtin_tools.get(name)
        if builtin is not None:
            logger.info("step %d/%d: builtin %s(%s)", step, self._max_steps,
                        name, json.dumps(arguments, ensure_ascii=False))
            try:
                return name, await builtin.handler(arguments)
            except Exception as exc:  # noqa: BLE001 — tool text, not a crash
                return name, f"{name} error: {type(exc).__name__}: {exc}"
        logger.info("step %d/%d: call %s(%s)", step, self._max_steps, name,
                    json.dumps(arguments, ensure_ascii=False))
        if self._authorization is not None and name in GATED_MCP_TOOLS:
            violation = await self._scope_check(name, arguments)
            if violation is not None:
                return name, violation
        return name, await self._mcp.call_tool(name, arguments)

    async def _scope_check(self, name: str,
                           arguments: Dict[str, Any]) -> Optional[str]:
        """The authorization gate's loop seam (core/authorization,
        2026-11-13): every agent SQL transaction falls under the
        conversation's resolved scope — db_run_query's statement,
        db_describe_table / db_list_tables's target, text_search's
        registry key — checked BEFORE the shared MCP daemon sees the
        call. Returns the rejection text (the tool result the model
        reads, with the kg_search remedy) or None to proceed; a gate
        failure of its own logs and proceeds (never break a tool call).
        """
        assert self._authorization is not None
        permission = self._authorization.permission
        try:
            if name == "db_run_query":
                await self._scope_gate.check_sql(
                    str(arguments.get("sql") or ""), permission)
            elif name == "db_describe_table":
                self._scope_gate.check_table(
                    str(arguments.get("schema") or ""),
                    str(arguments.get("table") or ""), permission)
            elif name == "db_list_tables":
                self._scope_gate.check_schema(
                    str(arguments.get("schema") or ""), permission)
            elif name == "text_search":
                self._scope_gate.check_text_table(
                    str(arguments.get("table") or "news"), permission)
        except ScopeViolation as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001 — gate failure, not a verdict
            logger.warning("scope gate check failed for %s: %s: %s", name,
                           type(exc).__name__, exc)
        return None

    def _tool_output_failed(self, name: str, output: str) -> bool:
        """The repeat guard's failure classifier (2026-11-13): the
        tool-layer error texts all announce themselves in the first
        line ("MCP tool error: …", "<tool> error: …", "old_text not
        found in …"); a sandbox_run fails on its JSON exit_code != 0
        or a timeout — the same shapes the breaker reads."""
        if not isinstance(output, str) or not output:
            return False
        head = output[:240]
        low = head.lower()
        if (low.startswith("mcp tool error:")
                or low.startswith(f"{name.lower()} error:")
                or low.startswith("old_text not found")):
            return True
        at = head.find('"exit_code"')
        if at >= 0:
            try:
                code = int(head[at:].split(":", 1)[1].split(",")[0])
                if code != 0:
                    return True
            except (ValueError, IndexError):
                pass
        return '"timed_out": true' in low

    async def _preview_tool_output(self, name: str, output: str) -> str:
        """The oversized tool result → ``_preview`` envelope (the
        loop seam of the metadata+preview layer, 2026-11-12). A
        tabular result (the readonly daemon's {columns, rows}, a
        JSON-table sandbox stdout) takes the DATA branch (structure,
        per-column stats, head/mid/tail batches); everything else the
        TEXT branch (char slices), with the soft jev classification
        (sentiment / domain / industries — the injected engine, the
        ``preview_jev_classify`` knob, the ``JEV_PREVIEW=0``
        kill-switch) riding the envelope when it made a read.
        how_to_read names the fetch path so the model decides what to
        read. Under the entry cap nothing changes; any envelope
        failure falls back to guard_text (the caller also catches)."""
        limits = run_limits()
        if len(output) <= limits.payload_entry_max_chars:
            return output
        from llm_agents._core.preview import PreviewBuilder

        source = ("db" if name.startswith("db_")
                  else "sandbox" if name.startswith("sandbox")
                  else "tool")
        how_to_read = {
            "db": "do NOT re-fetch this data into the conversation — "
                  "write a sandbox script (study_kit db.query) filtered "
                  "on the table's key columns that AGGREGATES/derives "
                  "and prints only the small computed result, then "
                  "sandbox_run it",
            "sandbox": "the full output stays in the sandbox session — "
                       "extend the script with filters/aggregation and "
                       "print only the small computed slice",
            "tool": "re-run the tool with narrower arguments",
        }.get(source, "re-run the tool with narrower arguments")
        builder = PreviewBuilder(
            preview_batch_rows=limits.preview_batch_rows,
            preview_chars=limits.payload_preview_chars,
            stats_sample_rows=limits.preview_stats_sample_rows)
        envelope = builder.build(source, output, how_to_read)
        if envelope.kind == "text":
            envelope = await self._classify_envelope(envelope, limits)
        return envelope.as_string()

    async def _classify_envelope(self, envelope, limits):
        """The jev content reads over a TEXT envelope (sentiment /
        domain / industries). 2026-11-13: the read RESULTS no longer
        ride the envelope into the conversation (jev decisions stay out
        of LLM context) — each read records itself as a decision step
        (llm.deep_agent_aux_steps, via the engine's _read seam) and the
        argmax line logs. A missing engine, a disabled knob or a failed
        read is a no-op — the classification can never alter a
        result."""
        import os
        if (not limits.preview_jev_classify
                or self._decision_engine is None
                or os.environ.get("JEV_PREVIEW", "").strip().lower()
                in ("0", "false", "off")):
            return envelope
        head = envelope.text_batches.get("head", "")
        if not head:
            return envelope
        try:
            # The industry candidates resolve in the langgraph package
            # (the DB-reading half of the classification) — core passes
            # the text through the duck-typed engine only; the resolver
            # import here is the ONE sanctioned seam crossing (same
            # discipline as the spawn gate's injected engine).
            from llm_agents.deep_agents.langgraph.industry_candidates \
                import RESOLVER
            candidates = await RESOLVER.candidates_for_async(head)
            decisions = await self._decision_engine.decide_content(
                text_head=head[:1200], total_chars=envelope.total_chars,
                source=envelope.source, industry_candidates=candidates)
            parts = [
                f"{key}={d.action} p={d.confidence:.2f}"
                for key, d in decisions.items()
                if getattr(d, "ok", False) and getattr(d, "action", None)]
            logger.info("preview classification (off-context): %s",
                        ", ".join(parts) if parts else "no decisive read")
        except Exception as exc:  # noqa: BLE001 — fail-soft enrichment
            logger.info("preview classification unavailable (%s)", exc)
        return envelope
