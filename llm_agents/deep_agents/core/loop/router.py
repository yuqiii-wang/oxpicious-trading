"""llm_agents.deep_agents.core.loop.router — the SYSTEM ONE ROUTER
(split out of the former flat ``core/loop.py``, 2026-11-15).

After every LLM response that proposes tool calls, the injected jev
decision engine routes the NEXT work (``_route_response``):

* INFERENCE — continue the conversation's own chain (the default on
  every no / below-floor / missing read);
* SPAWN — a self-contained scripting sub-assistant task: ONE
  foreground child whose whole context is the compact
  ``_scripting_handoff`` (goal + the response's plan + the proposed
  calls), the parent's calls never execute;
* FORK / DRIFT (2026-11-13) — a continuation sub node that INHERITS
  this transcript from a pre-drift checkpoint (``_fork_point_candidates``
  + the FORK-POINT read), everything after the cut abandoned;
* JOIN — a MESSY scripting node (fail streak or a mostly-spent
  budget) gets ONE nudge to write its handback; a tool-calling reply
  after the nudge force-returns the joined answer (the run loop's
  lane).

Beside the four lanes: the FAILURE-PROBE lane (``_maybe_probe_failure``
— a failed scripting child triggers ONE strongly-prompted INVESTIGATOR
child, ``_probe_handoff`` carrying the failed node's exact
authorizations) and ``_engine_read`` — the MISSING-METHOD-SAFE
duck-typed read seam every lane rides (a predating/broken engine is
"no read available", never a crash). Affirm-only everywhere except the
probe: a confident yes acts, everything else continues the model's own
plan. Primitive kwargs only — core imports nothing from
langgraph.decision (the layering rule); the injected engine builds its
own typed facts.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from llm_agents.deep_agents.core.delegation import (
    FORK_TOOL_NAME,
    SubagentRun,
)
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.payload_guard import guard_text

logger = logging.getLogger(__name__)


class _JevRouter:
    """The System One router lanes; composed into ``DeepAgent``."""

    # The sandbox tools whose PROPOSAL marks a response as coding work
    # (the router's spawn pre-filter — reads/listings are not script
    # writing). A SUBSET of _SANDBOX_TOOL_NAMES.
    _ROUTABLE_SANDBOX_TOOLS = frozenset((
        "sandbox_run", "sandbox_write_file", "sandbox_edit_file"))

    async def _engine_read(self, method: str, **kwargs):
        """One duck-typed engine read, MISSING-METHOD-SAFE: an injected
        engine that predates the router (a probe's read recorder, an
        old stub) carries no decide_route_* / decide_failure_probe —
        the lane treats that as "no read available" (None), never a
        crash; any read exception lands the same way (the engine's own
        never-raise contract, defended here too)."""
        handler = getattr(self._decision_engine, method, None)
        if handler is None:
            return None
        try:
            return await handler(**kwargs)
        except Exception as exc:  # noqa: BLE001 — the lane never blocks
            logger.info("router engine read %s failed: %s", method, exc)
            return None

    def _route_env_off(self) -> bool:
        """The router's env kill-switch (JEV_ROUTE=0) — probes that
        target the UN-ROUTED lanes set it, the JEV_DECISIONS=0 pattern's
        sibling."""
        import os
        return os.environ.get("JEV_ROUTE", "").strip().lower() in (
            "0", "false", "off")

    def _fork_point_candidates(
            self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The drift fork's checkpoint candidates (2026-11-13): every
        assistant turn that STATED something (non-empty content — pure
        tool-call turns carry no pickable context), as a self-consistent
        checkpoint — its cut index sits AFTER that turn's own tool
        results, so the inherited prefix ends protocol-clean. Returns
        the ≤16 most recent (the jev per-request option ceiling
        reserves room for the 'now' option); each entry carries its
        ``label`` (turn_<msg_index> — the read's answer token), the
        capped ``head`` the read conditions on, and the ``cut`` (the
        transcript length to inherit through)."""
        checkpoints: List[Dict[str, Any]] = []
        for i, m in enumerate(messages):
            if str(m.get("role")) != "assistant":
                continue
            head = str(m.get("content") or "").strip()
            if not head:
                continue
            j = i + 1
            if m.get("tool_calls"):
                while j < len(messages) \
                        and str(messages[j].get("role")) == "tool":
                    j += 1
            checkpoints.append({"label": f"turn_{i}",
                                "head": head[:160], "cut": j})
        return checkpoints[-16:]

    async def _route_response(
            self, goal: str, message: Dict[str, Any],
            calls: List[Dict[str, Any]], step: int,
            messages: Optional[List[Dict[str, Any]]] = None,
            ) -> Optional[str]:
        """THE SYSTEM ONE ROUTER (2026-11-12): judge whether the NEXT
        work this response proposes continues this conversation's
        inference chain ('' — continue), is a self-contained coding
        sub-assistant task ('spawn' — a fresh scripting sub node takes
        over, keeping this node on its biz reasoning), a CONTINUATION
        worth delegating to an inherit-context child ('fork',
        2026-11-13 — the sub node continues from this conversation's
        full transcript while the parent awaits), or — in a scripting
        node whose debug loop is messy — should stop the node ('join' —
        hand back to the parent chain). CODE FILTERS FIRST:
        the spawn and fork reads fire only when the response PROPOSES
        sandbox write/run/edit calls (mechanical coding detection) in a
        non-scripting node with sandbox room and each lane's own
        budget; the join read
        only in a scripting node showing messiness (failed sandbox
        outcomes or a mostly-spent budget). Every read is
        AFFIRM-ONLY — a confident yes acts, everything else (no read,
        host down, below floor) continues the model's own plan."""
        if self._decision_engine is None:
            return None
        limits = run_limits()
        if not limits.jev_route or self._route_env_off():
            return None
        names = [str((c.get("function") or {}).get("name") or "")
                 for c in calls]
        sandbox_calls = sum(1 for n in names
                            if n in self._ROUTABLE_SANDBOX_TOOLS)
        # SPAWN lane: proposed coding work in a node that is NOT itself
        # scripting (the no-re-spawn rule) — the parent stays biz.
        if (sandbox_calls > 0
                and self._node_role != "scripting"
                and limits.jev_route_max_spawns > 0
                and self._route_spawns_used < limits.jev_route_max_spawns
                and self._sandbox is not None and self._can_spawn):
            decision = await self._engine_read(
                "decide_route_spawn",
                node_role=self._node_role, depth=self._spawn_depth,
                steps_used=step, max_steps=self._max_steps,
                response_head=str(message.get("content") or "")[:400],
                pending_tools=",".join(names)[:200],
                sandbox_calls=sandbox_calls,
                fail_streak=self._sandbox_fail_streak,
                syntax_streak=self._sandbox_syntax_streak,
                spawns_used=self._route_spawns_used,
                max_spawns=limits.jev_route_max_spawns)
            # _engine_read's None (missing method / failed read) is the
            # lane's "no read available" — continue the model's own
            # plan, never crash the step (the missing-method-safe
            # contract; an unguarded .ok here killed the hop).
            if decision is not None and decision.ok \
                    and decision.action == "yes":
                logger.info("step %d/%d: router spawns the coding to a "
                            "scripting sub node (p=%.2f)", step,
                            self._max_steps, decision.confidence)
                return "spawn"
        # FORK / DRIFT lane (2026-11-13, reframed): the drift watch —
        # after EVERY tool-calling response in a non-scripting node
        # with fork budget, jev judges whether the latest context has
        # GRADUALLY DRIFTED from the node's purpose (goal_head); a
        # confident yes is followed by the FORK-POINT read deciding
        # from which previous LLM context the fork continues (an
        # earlier, pre-drift checkpoint; 'now'/below-floor/unknown →
        # fork-from-now, the safe default). Everything after the cut is
        # ABANDONED — the hooks materialize its stub row (child kind 2)
        # and the UI demotes it to the right-side edge path. Affirm-only
        # twice: any no / below-floor / unavailable read continues the
        # node's own plan.
        if (self._node_role != "scripting"
                and limits.jev_route_fork
                and limits.jev_route_max_forks > 0
                and self._route_forks_used < limits.jev_route_max_forks
                and self._sandbox is not None and self._can_spawn):
            decision = await self._engine_read(
                "decide_route_fork",
                node_role=self._node_role, depth=self._spawn_depth,
                goal_head=str(goal or "")[:400],
                steps_used=step, max_steps=self._max_steps,
                response_head=str(message.get("content") or "")[:400],
                pending_tools=",".join(names)[:200],
                sandbox_calls=sandbox_calls,
                fail_streak=self._sandbox_fail_streak,
                syntax_streak=self._sandbox_syntax_streak,
                spawns_used=self._route_forks_used,
                max_spawns=limits.jev_route_max_forks)
            if decision is not None and decision.ok \
                    and decision.action == "yes":
                candidates = self._fork_point_candidates(messages)
                cut = len(messages)
                if candidates:
                    point = await self._engine_read(
                        "decide_route_fork_point",
                        goal_head=str(goal or "")[:400],
                        candidates=[{"label": c["label"],
                                     "head": c["head"]} for c in candidates],
                        steps_used=step, max_steps=self._max_steps)
                    if point is not None and point.ok:
                        chosen = (point.action or "").strip()
                        match = next((c for c in candidates
                                      if c["label"] == chosen), None)
                        if match is not None:
                            cut = match["cut"]
                n_abandoned = len(messages) - cut
                self._pending_fork_cut = cut
                self._pending_fork_note = (
                    f"[drift fork] the {n_abandoned} turn(s) after the "
                    f"pre-drift checkpoint were abandoned as off-purpose; "
                    f"their last statement read: "
                    f"{str(message.get('content') or '').strip()[:200]}")
                logger.info(
                    "step %d/%d: router drift-forks from checkpoint "
                    "cut=%d (%d later turn(s) abandoned) (p=%.2f)",
                    step, self._max_steps, cut, n_abandoned,
                    decision.confidence)
                return "fork"
        # JOIN lane: a scripting node gone messy — stop it and join the
        # parent chain (the parent's failure handling takes over).
        messy = (self._sandbox_fail_streak > 0
                 or self._sandbox_syntax_streak > 0
                 or step >= (self._max_steps * 3) // 5)
        if (self._node_role == "scripting" and limits.jev_route_join
                and messy and not self._join_nudged):
            decision = await self._engine_read(
                "decide_route_join",
                node_role=self._node_role,
                steps_used=step, max_steps=self._max_steps,
                response_head=str(message.get("content") or "")[:400],
                pending_tools=",".join(names)[:200],
                fail_streak=self._sandbox_fail_streak,
                syntax_streak=self._sandbox_syntax_streak)
            if decision is not None and decision.ok \
                    and decision.action == "yes":
                logger.info("step %d/%d: router joins this scripting "
                            "node back to the parent (p=%.2f)", step,
                            self._max_steps, decision.confidence)
                return "join"
        return None

    async def _spawn_scripting(
            self, goal: str, message: Dict[str, Any],
            calls: List[Dict[str, Any]]) -> Optional[str]:
        """The router's SPAWN action: spawn ONE scripting sub node
        FOREGROUND (the parent awaits) whose whole context is the
        SCRIPTING HANDOFF — never the parent transcript — and fold its
        answer back as the injection user message. Returns None when
        the delegation path refuses (the caller falls through to the
        model's own calls)."""
        limits = run_limits()
        self._route_spawns_used += 1
        text = await self._run_subagent({
            "goal": self._scripting_handoff(goal, message, calls),
            "background": False, "role": "scripting"})
        if text.startswith(("spawn_subagent error:",
                            "spawn_subagent rejected by policy:",
                            "subagent input-hook failed:")):
            self._route_spawns_used -= 1
            return None
        text = guard_text(text)
        logger.info("router scripting spawn returned (%d chars)", len(text))
        return (f"[Scripting sub node took over] A sub node ran the "
                f"coding work this step proposed (clean context, "
                f"shared sandbox) and returned:\n\n{text}")

    def _scripting_handoff(
            self, goal: str, message: Dict[str, Any],
            calls: List[Dict[str, Any]]) -> str:
        """The scripting spawn's whole context — COMPACT (the
        fresh-context point): the run goal, the response's own plan
        text, the proposed sandbox calls verbatim (capped) and the
        method. Never the parent transcript."""
        head = str(message.get("content") or "").strip()[:1200]
        call_lines: List[str] = []
        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            if name in self._ROUTABLE_SANDBOX_TOOLS:
                call_lines.append(
                    f"- {name}({str(function.get('arguments') or '')[:400]})")
        return (
            "[Scripting handoff — fresh context] A research "
            "conversation proposed the coding work below; you are the "
            "scripting sub node that takes it over. Your answer IS the "
            "handback: return the script's KEY EVIDENCE (the results "
            "it prints, concise and quantitative) plus, when something "
            "failed, the exact error and what is still missing — the "
            "parent uses it directly.\n"
            f"Run goal: {goal}\n"
            + (f"The proposing step's plan:\n{head}\n" if head else "")
            + "Proposed tool calls:\n" + "\n".join(call_lines)
            + "\nMethod: the sandbox session is SHARED — write/run "
            "scripts there (sandbox_write_file / sandbox_run), keep "
            "per-block try/except that records errors and continues, "
            "read the traceback's LAST line before patching, and "
            "aggregate/derive in pandas rather than one SQL monolith. "
            "Stay on the scripting — do not re-open the business "
            "question.")

    async def _fork_scripting(
            self, goal: str, message: Dict[str, Any],
            calls: List[Dict[str, Any]],
            transcript: List[Dict[str, Any]],
            cut: Optional[int] = None,
            drift_note: Optional[str] = None) -> Optional[str]:
        """The router's DRIFT-FORK action (2026-11-13): fork ONE
        inherit-context sub node FOREGROUND (the parent awaits) that
        continues this conversation from the chosen PRE-DRIFT
        checkpoint — the deep-copied transcript PREFIX rides into the
        child (``_run_subagent``'s inherit lane), everything after the
        cut is abandoned (``fork_abandoned_note`` → the hooks'
        kind-2 stub row) — and its answer folds back as the injection
        user message. Returns None when the delegation path refuses
        (the caller falls through to the model's own calls, the budget
        already refunded)."""
        self._route_forks_used += 1
        if cut is None or cut > len(transcript):
            cut = len(transcript)
        abandoned = cut < len(transcript)
        text = await self._run_subagent(
            {"goal": self._fork_handoff(goal, message, calls,
                                        abandoned=abandoned),
             "background": False, "role": "scripting"},
            inherit_transcript=transcript[:cut],
            fork_abandoned_note=drift_note if abandoned else None,
            tool_name=FORK_TOOL_NAME)
        if text.startswith((
                "spawn_subagent error:", "spawn_subagent rejected by "
                "policy:", "subagent input-hook failed:",
                "fork_subagent error:", "fork_subagent rejected by "
                "policy:")):
            self._route_forks_used -= 1
            return None
        text = guard_text(text)
        logger.info("router drift fork returned (%d chars)", len(text))
        mode = ("pre-drift context inherited — the abandoned tail was "
                "discarded" if abandoned else "full context inherited")
        return (f"[Fork sub node took over] A fork of this conversation "
                f"({mode}, shared sandbox) recovered the node's purpose "
                f"and returned:\n\n{text}")

    def _fork_handoff(
            self, goal: str, message: Dict[str, Any],
            calls: List[Dict[str, Any]],
            abandoned: bool = False) -> str:
        """The drift fork's directive: the child inherits the PRE-DRIFT
        context (or the whole conversation on fork-from-now), so the
        goal re-anchors the NODE'S PURPOSE, names the abandoned
        direction (context only — never to resume) and carries the
        latest response's proposed calls verbatim (capped)."""
        head = str(message.get("content") or "").strip()[:1200]
        call_lines: List[str] = []
        for call in calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            if name in self._ROUTABLE_SANDBOX_TOOLS:
                call_lines.append(
                    f"- {name}({str(function.get('arguments') or '')[:400]})")
        return (
            "[Fork continuation — drift recovery] The conversation you "
            "inherited is this node's context as of a pre-drift "
            "checkpoint"
            + (" — its later turns drifted off-purpose and were "
               "ABANDONED (not inherited); do NOT resume that "
               "direction." if abandoned else ".") + " The node's "
            "PURPOSE you must complete: " + goal + "\n"
            "Your answer IS the handback: return the KEY EVIDENCE "
            "(concise and quantitative) plus, when something failed, "
            "the exact error and what is still missing — the parent "
            "uses it directly and has NOT seen anything you do here.\n"
            + (f"The abandoned tail's last statement (context only — "
               f"do not resume it):\n{head}\n" if head and abandoned
               else (f"The proposing step's plan:\n{head}\n"
                     if head else ""))
            + ("Proposed tool calls (the drift watch intercepted them; "
               "run them only as far as they serve the purpose):\n"
               + "\n".join(call_lines) if call_lines else "")
            + "\nMethod: the sandbox session is SHARED and its files "
            "are already yours — continue the established scripts "
            "rather than rewriting them, keep per-block try/except, "
            "read the traceback's LAST line before patching, and stay "
            "on the purpose — do not re-open or re-derive settled "
            "work.")

    async def _maybe_probe_failure(
            self, run: SubagentRun, text: str) -> str:
        """The FAILURE-PROBE lane (2026-11-12): a scripting child just
        FAILED and joined back — spawn ONE INVESTIGATOR sub node to find
        out WHY, authorized with the failed node's exact references
        (state id, llm chain id, goal, error) to inspect through
        search_thread_context and the shared sandbox. STRONGLY
        PROMPTED, the inverse of the affirm-only lanes: the jev read
        spawns on EVERY outcome except a confident NO (an unavailable or
        below-floor read still spawns — bounded by the probe budget).
        The investigator's findings append to the tool text the parent
        model reads."""
        limits = run_limits()
        if (self._decision_engine is None or not limits.jev_route
                or limits.jev_route_probe_max <= 0
                or self._route_probes_used >= limits.jev_route_probe_max
                or not self._can_spawn or self._route_env_off()):
            return text
        decision = await self._engine_read(
            "decide_failure_probe",
            failed_role=run.request.role or "scripting",
            failed_goal=run.request.goal[:400],
            error_text=str(run.error or text)[:400],
            probes_used=self._route_probes_used,
            max_probes=limits.jev_route_probe_max)
        if decision is None:
            return text  # the lane has no read path — no probe
        if decision.ok and decision.action == "no":
            logger.info("failure probe declined by the jev gate "
                        "(p=%.2f)", decision.confidence)
            return text
        self._route_probes_used += 1
        probe_text = await self._run_subagent({
            "goal": self._probe_handoff(run), "background": False,
            "role": "investigator"})
        if probe_text.startswith(("spawn_subagent error:",
                                  "spawn_subagent rejected by policy:",
                                  "subagent input-hook failed:")):
            self._route_probes_used -= 1
            return text
        probe_text = guard_text(probe_text)
        logger.info("failure probe investigator returned (%d chars)",
                    len(probe_text))
        return (f"{text}\n\n[Failure probe — an investigator sub node "
                f"examined why] {probe_text}")

    def _probe_handoff(self, run: SubagentRun) -> str:
        """The investigator's whole context: the failed node's goal and
        error plus its EXACT references — the parent's AUTHORIZATION.
        The chain-scoped search_thread_context the investigator carries
        reaches the failed node's recorded llm traffic by chain id, and
        the shared sandbox session holds its script."""
        refs: List[str] = []
        if run.state_id is not None:
            refs.append(f"- node state id: {run.state_id} (its row in "
                        "llm.deep_agent_states / the thread graph)")
        if run.llm_context_id is not None:
            refs.append(f"- llm request chain id: {run.llm_context_id} "
                        "(search_thread_context hits its recorded "
                        "requests — filter the results on it)")
        return (
            "[Failure probe — you are the investigator] A scripting "
            "sub node of this run FAILED and the parent still needs "
            "the work. Find out WHY it failed — the root cause, not "
            "the symptom — and what the parent should do differently. "
            "You are AUTHORIZED to inspect exactly these:\n"
            f"Failed goal: {run.request.goal[:600]}\n"
            f"Recorded error: {str(run.error or '?')[:400]}\n"
            + ("\n".join(refs) + "\n" if refs else "")
            + "- the SHARED sandbox session (sandbox_read_file the "
            "failed node's script; never rewrite it)\n"
            "Use search_thread_context for the recorded traffic (the "
            "run's whole chain is in scope — the references above name "
            "the failed node's chain), read the last exception line, "
            "and answer concisely: root cause / what to change / "
            "whether a re-run with the fix is worth it.")
