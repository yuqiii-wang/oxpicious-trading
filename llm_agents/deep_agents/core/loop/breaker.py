"""llm_agents.deep_agents.core.loop.breaker — the sandbox
write-run-fix LOOP BREAKER (split out of the former flat
``core/loop.py``, 2026-11-15).

Tracks consecutive failed sandbox-tool outcomes and breaks the
write-run-fix death spiral (the 2026-11-11 run-e1c366bd failure mode —
nine whole-script rewrites chasing eight SQL bugs) with two lanes:

* the FRESH-CONTEXT ESCALATION (2026-11-11) — after
  ``sandbox_spawn_after_failures`` consecutive TRACEBACK-class script
  failures, spawn ONE repair subagent whose whole context is the
  compact handoff (``_repair_handoff``: run goal + script path + the
  last exception lines) instead of the grown transcript; the parent
  awaits it foreground.
* the bounded NUDGE — everything the spawn lane does not cover gets
  the patch-don't-rewrite teaching (threshold + per-conversation cap
  in ``deep_agent_config.json``).

The System One lane (decision/, 2026-11-12) gates the spawn: a
confident jev NO declines it (the code gates above stay hard; an
unavailable or below-floor read spawns — the breaker's default).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.payload_guard import guard_text

logger = logging.getLogger(__name__)


class _SandboxBreaker:
    """The loop breaker's state readers + lanes; composed into
    ``DeepAgent``."""

    # The sandbox tools whose outcomes the write-run-fix breaker tracks
    # (the observed pathology rewrites files and re-runs scripts — reads
    # and listings ride along as hard-error evidence only).
    _SANDBOX_TOOL_NAMES = frozenset((
        "sandbox_run", "sandbox_write_file", "sandbox_read_file",
        "sandbox_edit_file", "sandbox_list_files"))

    # The traceback-class failure texts the fresh-context repair spawn
    # keys on. A RUNTIME error prints the Traceback header; a COMPILE-TIME
    # error (SyntaxError and its IndentationError / TabError subclasses)
    # prints only the "…Error: …" line — no header — so every literal is
    # matched on its own.
    _TRACEBACK_CLASS_MARKS = ("Traceback", "SyntaxError",
                              "IndentationError", "TabError")

    def _note_sandbox_outcome(self, call: Dict[str, Any], name: str,
                              output: str) -> None:
        """Track consecutive failed sandbox-tool outcomes for the loop
        breaker. A hard error is the tool-error / cancel / exception
        text any sandbox tool can return; a ``sandbox_run`` also fails
        on its JSON ``exit_code != 0`` (the script's own traceback —
        the observed rewrite-loop shape). A successful ``sandbox_run``
        resets the streaks; successful writes/edits are neutral
        progress, not resets. Non-sandbox tools never count. Along the
        way it captures the failing script's path and, per
        traceback-class failure, the exception's LAST line — the spawn
        handoff's fix history."""
        if name not in self._SANDBOX_TOOL_NAMES:
            return
        try:
            path = ((json.loads((call.get("function") or {})
                                .get("arguments") or "{}") or {})
                    .get("path"))
            if isinstance(path, str) and path:
                self._sandbox_script_path = path
        except ValueError:
            pass
        if name == "sandbox_run" and '"exit_code"' in output:
            if '"exit_code": 0' in output:
                self._sandbox_fail_streak = 0
                self._sandbox_syntax_streak = 0
            else:
                self._sandbox_fail_streak += 1
                if any(mark in output
                       for mark in self._TRACEBACK_CLASS_MARKS):
                    self._sandbox_syntax_streak += 1
                    self._remember_fail_line(output)
            return
        first_line = output.split("\n", 1)[0]
        if (output.startswith("MCP tool error")
                or output.startswith(f"{name} error:")
                or output.startswith(f"{name}: cancelled")
                or " while calling " in first_line):
            self._sandbox_fail_streak += 1

    def _remember_fail_line(self, output: str) -> None:
        """One fix-history entry: the failed run's LAST exception line
        (the JSON-embedded stderr's tail; the capped-output fallback is
        the raw tail). At most the last eight are kept."""
        line = ""
        try:
            stderr = str((json.loads(output) or {}).get("stderr") or "")
            lines = [s.strip() for s in stderr.splitlines() if s.strip()]
            line = lines[-1] if lines else ""
        except ValueError:
            line = output.strip()[-200:]
        line = line[:200]
        if line:
            self._sandbox_fail_lines.append(line)
            del self._sandbox_fail_lines[:-8]

    async def _maybe_spawn_repair(self, goal: str,
                                 limits) -> Optional[str]:
        """The breaker's fresh-context escalation: after
        ``sandbox_spawn_after_failures`` consecutive traceback-class
        script failures, spawn ONE repair subagent — FOREGROUND, the
        parent awaits it — whose whole context is the compact handoff
        (run goal + script path + fix history) instead of this
        conversation's grown transcript. The child state row registers
        under the current step through the same input hook a
        model-called spawn rides; the injection message below carries
        the child's answer back. Returns the injection user message,
        or None when the trigger does not apply (budget spent, depth
        cap, no sandbox/subagents) — then the nudge lane still
        guards."""
        if self._sandbox_syntax_streak < limits.sandbox_spawn_after_failures:
            return None
        if self._sandbox_spawns_used >= limits.sandbox_spawn_max:
            return None
        if (not self._sandbox or not self._allow_subagents
                or self._spawn_depth >= limits.max_spawn_depth):
            return None
        # THE SYSTEM ONE LANE (decision/, 2026-11-11): when an engine is
        # injected, the calibrated jev read judges whether THIS failure
        # pattern is one a fresh-context child fixes — a confident NO
        # keeps the nudge lane judging (the code gates above stay hard;
        # an unavailable or below-floor read spawns, preserving the
        # breaker's default behavior).
        if self._decision_engine is not None:
            # Primitive kwargs only — core imports nothing from
            # langgraph.decision (the layering rule); the injected
            # engine builds its own typed facts. The read rides the
            # missing-method-safe seam (the same discipline as the three
            # router lanes): an engine predating decide_spawn, a broken
            # engine or a read exception lands as "no read available" —
            # the breaker's default behavior (spawn) stands, never a
            # crashed conversation.
            decision = await self._engine_read(
                "decide_spawn",
                syntax_streak=self._sandbox_syntax_streak,
                threshold=limits.sandbox_spawn_after_failures,
                spawns_used=self._sandbox_spawns_used,
                max_spawns=limits.sandbox_spawn_max,
                script_path=self._sandbox_script_path or "",
                fail_lines=" | ".join(self._sandbox_fail_lines[-4:]),
            )
            if (decision is not None and decision.ok
                    and decision.action == "no"):
                logger.info(
                    "sandbox repair spawn declined by the jev gate "
                    "(p=%.2f)", decision.confidence)
                return None
        self._sandbox_spawns_used += 1
        text = await self._run_subagent({
            "goal": self._repair_handoff(goal), "background": False,
            "role": "scripting"})
        if text.startswith(("spawn_subagent error:",
                            "spawn_subagent rejected by policy:",
                            "subagent input-hook failed:")):
            # The delegation path refused the spawn — spend nothing; the
            # streak stays and the nudge lane guards instead.
            self._sandbox_spawns_used -= 1
            return None
        first_line = text.split("\n", 1)[0]
        if text.startswith("subagent ") and " failed: " in first_line:
            # The child RAN but died (its own provider/sandbox failure —
            # "subagent <id> failed: …"). The budget stays consumed (the
            # hard cap holds — a failed child still spent its tokens) and
            # the streaks stay LIVE: the escalation already burned, so the
            # next failure falls through to the nudge lane instead of
            # re-arming a breaker whose fresh-context try just failed.
            logger.info("sandbox repair child failed: %s", first_line[:160])
        else:
            self._sandbox_syntax_streak = 0
            self._sandbox_fail_streak = 0
        text = guard_text(text)
        logger.info("sandbox repair spawn returned (%d chars)", len(text))
        return (f"[Fresh-context repair agent took over] A subagent "
                f"continued the failed script from a clean context and "
                f"returned:\n\n{text}")

    def _repair_handoff(self, goal: str) -> str:
        """The repair spawn's whole context — deliberately COMPACT (the
        token-saving point): the run goal, the script path (the shared
        sandbox session), the failed runs' exception lines and the
        method. Never the parent transcript."""
        path = self._sandbox_script_path or "scripts/<the failing script>"
        tried = ""
        if self._sandbox_fail_lines:
            tried = ("\nAlready tried (%d failed runs — do NOT repeat "
                     "these fixes): %s"
                     % (len(self._sandbox_fail_lines),
                        " | ".join(self._sandbox_fail_lines)))
        return (
            "[Repair handoff — fresh context] The parent conversation's "
            "script-debugging loop is burning its context budget; you "
            "take over with a clean slate. Your answer IS the handback: "
            "return the script's KEY EVIDENCE (the results it prints, "
            "concise) — the parent uses it directly.\n"
            f"Run goal: {goal}\n"
            f"Script: {path} — it lives in the SHARED sandbox session: "
            "sandbox_read_file it first; NEVER rewrite it whole — "
            "patch the one broken block with sandbox_edit_file."
            + tried +
            "\nMethod: sandbox_run it → read the traceback's LAST line "
            "→ sandbox_edit_file the ONE broken block → re-run. Keep "
            "per-block try/except that records errors and continues — "
            "one bad query must not discard the rest of the evidence. "
            "If the script's approach itself is wrong, fix the "
            "approach; if the data is missing, say so in the answer.")
