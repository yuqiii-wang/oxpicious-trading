"""llm_agents.deep_agents.tools.plan_tools.update_plan — the two
``update_plan`` variants over one name: the MAIN lane's rework tool
(adds, revise, removes on the not-yet-run part, fork termination) and
the FORK's extend tool (adds under its own fork state, nothing else —
authority-narrowed parameters, the model never sees an option it must
not use).
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, ClassVar, Dict, List, Optional, Set, Tuple

from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.authorities import (
    MAX_ADDS_PER_CALL, PlanAuthority, render_added,
)
from llm_agents.deep_agents.planning.plan_json import MAX_TITLE_LEN
from llm_agents.deep_agents.planning.store import (PLAN_STORE)

from llm_agents.deep_agents.tools.plan_tools.common import (
    _PlanAuthorityTool,
)


class UpdatePlanTool(_PlanAuthorityTool):
    """``update_plan`` — the MAIN lane's rework tool: adds, revise,
    removes on the not-yet-run part, and fork termination."""

    name: ClassVar[str] = "update_plan"
    db_key: ClassVar[Tuple[str, str]] = (name, "plan_main")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "add_steps": {
                "type": "array",
                "maxItems": MAX_ADDS_PER_CALL,
                "description": "Steps to insert, in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "node": {
                            "type": "string",
                            "description": "Short snake_case tag "
                                           "naming the BUSINESS "
                                           "aspect of the ask this "
                                           "step settles (e.g. "
                                           "moving_average_state) "
                                           "— never an operation "
                                           "(fetch_/load_/query_ "
                                           "are rejected).",
                        },
                        "title": {"type": "string"},
                        "notes": {"type": "string"},
                        "delegate_goal": {
                            "type": "string",
                            "description": "Mark the step as a fork: "
                                           "the COMPLETE self-"
                                           "contained goal a "
                                           "background subagent "
                                           "will pursue — open "
                                           "with the business "
                                           "question (subject + "
                                           "what to settle); the "
                                           "subagent also receives "
                                           "the run's business "
                                           "context (subject, "
                                           "focus question, "
                                           "selected prompts) "
                                           "automatically.",
                        },
                        "parallel": {
                            "type": "boolean",
                            "description": "Mark the step "
                                           "independent of every "
                                           "earlier step's "
                                           "findings: consecutive "
                                           "parallel/delegated "
                                           "steps run as ONE "
                                           "concurrent wave. Never "
                                           "on a step that must "
                                           "weigh earlier findings.",
                        },
                    },
                    "required": ["title"],
                },
            },
            "after_ordinal": {
                "type": "integer",
                "minimum": 1,
                "description": "Insert add_steps AFTER this step's "
                               "ordinal; omit to append at the "
                               "plan's end.",
            },
            "revise": {
                "type": "object",
                "description": "Rewrite one PENDING step.",
                "properties": {
                    "ordinal": {"type": "integer"},
                    "title": {"type": "string"},
                    "notes": {"type": "string"},
                    "delegate_goal": {
                        "type": "string",
                        "description": "Set/replace the fork goal; "
                                       "'' clears the fork marker.",
                    },
                },
                "required": ["ordinal"],
            },
            "remove_ordinals": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "PENDING steps to drop (settled "
                               "skipped, removal recorded).",
            },
            "cancel_run_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Running background subagent run "
                               "ids to terminate now.",
            },
            "cancel_running": {
                "type": "boolean",
                "description": "Terminate EVERY running background "
                               "subagent (a total replan).",
            },
        },
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Rework the plan's NOT-YET-STARTED part when your findings "
        "change what remains to be done: add steps (optionally "
        "delegated forks with a self-contained goal), revise or "
        "remove pending steps, and terminate running background "
        "subagents when the plan changed fundamentally. Steps "
        "already in_progress/completed/failed are history — they "
        "never move. The result carries the current rendered plan."
    )

    def __init__(self, authority: PlanAuthority,
                 plan_lock: Optional["asyncio.Lock"] = None, *,
                 description: Optional[str] = None) -> None:
        super().__init__(authority, description=description)
        # Serializes MAIN-lane mutations when several main
        # conversations run CONCURRENTLY (the runner's parallel wave
        # of inline steps): the read-modify-write on the pending lane
        # (remove -> revise -> add, with ordinal shifts) must not
        # interleave across wave members. None = the plain sequential
        # lane (nothing to serialize).
        self._plan_lock = plan_lock

    async def run(self, arguments: Dict[str, Any]) -> str:
        lines: List[str] = []
        if self._plan_lock is None or not self._authority.is_main:
            await self._update(arguments, lines)
        else:
            async with self._plan_lock:
                await self._update(arguments, lines)
        return "\n".join(lines)

    async def _update(self, args: Dict[str, Any],
                      lines: List[str]) -> None:
        """One call's body — the shared sequential core (the lock
        wrapper above is pure scheduling)."""
        try:
            if self._authority.is_main:
                lines.extend(await self._main_update(args))
            else:
                lines.extend(await self._authority.sub_add(args))
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            lines.append(f"update_plan error: {type(exc).__name__}: {exc}")
        lines.append("Current plan:")
        try:
            lines.append(await self._authority.rendered())
        except Exception as exc:  # noqa: BLE001 — degrade, don't crash
            lines.append(f"(plan read failed: {type(exc).__name__}: {exc})")

    async def _main_update(self, args: Dict[str, Any]) -> List[str]:
        """The MAIN scope: pending-lane removes/revise/adds + fork
        termination. Destructive and retargeting ops run FIRST, against
        the numbering the model sees; ADDS run LAST because a mid-plan
        insert shifts later ordinals (the model's remove/revise in the
        SAME call address the pre-insert numbering). Every op reports
        its own outcome line; ops on started rows are rejected by the
        pending guards (plan_store)."""
        authority = self._authority
        lines: List[str] = []
        remove = args.get("remove_ordinals")
        if isinstance(remove, list) and remove:
            ordinals: Set[int] = set()
            for value in remove:
                try:
                    ordinals.add(int(value))
                except (TypeError, ValueError):
                    return ["update_plan error: remove_ordinals must be "
                            "integers"]
            states_now = await PlanAuthority._main_lane_states(authority.thread_id)
            targets = [s for s in states_now if s.ordinal in ordinals]
            not_pending = [s.ordinal for s in targets
                           if s.status != "pending"]
            if not_pending:
                return [f"update_plan error: step(s) {sorted(not_pending)} "
                        "already started — started steps never move"]
            missing = sorted(ordinals - {s.ordinal for s in states_now})
            if missing:
                return [f"update_plan error: no plan step with ordinal(s) "
                        f"{missing}"]
            settled = await PLAN_STORE.skip_pending_states(
                [s.state_id for s in targets], "removed by plan update",
                thread_id=authority.thread_id)
            lines.append(f"removed {settled} pending step(s): "
                         f"{sorted(ordinals)}")
        revise = args.get("revise")
        if isinstance(revise, dict):
            try:
                ordinal = int(revise.get("ordinal"))
            except (TypeError, ValueError):
                return ["update_plan error: revise.ordinal must be an "
                        "integer"]
            target = next((s for s in await PlanAuthority._main_lane_states(
                authority.thread_id) if s.ordinal == ordinal), None)
            if target is None:
                return [f"update_plan error: no plan step with ordinal "
                        f"{ordinal}"]
            revised = await PLAN_STORE.revise_pending_state(
                target.state_id,
                title=(str(revise["title"]).strip()[:MAX_TITLE_LEN]
                       if revise.get("title") else None),
                notes=revise.get("notes"),
                delegate=(revise.get("delegate_goal")),
                llm_context_id=authority.llm_context_id)
            if revised is None:
                return [f"update_plan error: step {ordinal} is not "
                        "pending (started steps never move)"]
            lines.append(f"revised step {ordinal}")
        steps, error = PlanAuthority._parse_add_steps(args)
        if error:
            return lines + [error] if lines else [error]
        if steps:
            states_now = await PlanAuthority._main_lane_states(authority.thread_id)
            max_total = run_limits().max_total_plan_steps
            if len(states_now) + len(steps) > max_total:
                return lines + [f"update_plan error: the plan is capped "
                                f"at {max_total} steps"]
            after = args.get("after_ordinal")
            after_ordinal: Optional[int] = None
            if after is not None:
                try:
                    after_ordinal = int(after)
                except (TypeError, ValueError):
                    return lines + ["update_plan error: 'after_ordinal' "
                                    "must be an integer"]
                if not any(s.ordinal == after_ordinal for s in states_now):
                    return [f"update_plan error: no plan step with "
                            f"ordinal {after_ordinal}"]
            added = await PLAN_STORE.add_plan_states(
                authority.thread_id, steps, after_ordinal=after_ordinal,
                llm_context_id=authority.llm_context_id)
            lines.append(f"added {len(added)} step(s):")
            lines.append(render_added(added))
        run_ids = [str(r) for r in (args.get("cancel_run_ids") or [])
                   if str(r).strip()]
        if args.get("cancel_running") is True:
            lines.extend(await self._cancel(None))
        elif run_ids:
            lines.extend(await self._cancel(run_ids))
        return lines

    async def _cancel(self,
                      run_ids: Optional[List[str]]) -> List[str]:
        """Terminate background forks — MAIN only. The runner-bound
        canceller stops the child tasks and settles their fork states
        (None = every running fork); the outcome line tells the model
        what actually stopped."""
        authority = self._authority
        if authority.cancel_runs is None:
            return ["update_plan error: fork cancellation unavailable "
                    "here"]
        try:
            result = authority.cancel_runs(run_ids)
            if inspect.isawaitable(result):
                result = await result
            stopped = [str(r) for r in (result or [])]
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return [f"update_plan error: cancel failed: "
                    f"{type(exc).__name__}: {exc}"]
        if not stopped:
            return ["no running background subagent matched"]
        return [f"terminated background subagent(s): "
                f"{', '.join(stopped)}"]


class SubUpdatePlanTool(_PlanAuthorityTool):
    """``update_plan`` — the FORK's extend tool: adds under its own
    fork state, nothing else (same tool name, authority-narrowed
    parameters — the model never sees an option it must not use)."""

    name: ClassVar[str] = "update_plan"
    db_key: ClassVar[Tuple[str, str]] = ("update_plan", "plan_fork")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "add_nodes": {
                "type": "array",
                "maxItems": MAX_ADDS_PER_CALL,
                "description": "Investigation nodes to add under "
                               "your fork, in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "node": {
                            "type": "string",
                            "description": "Short snake_case tag "
                                           "naming the BUSINESS "
                                           "aspect this node "
                                           "settles (e.g. "
                                           "moving_average_state) "
                                           "— never an operation "
                                           "(fetch_/load_/query_ "
                                           "are rejected).",
                        },
                        "title": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["title"],
                },
            },
        },
        "required": ["add_nodes"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Extend YOUR OWN forked path when your work hits something "
        "that needs further investigation: add_nodes appends "
        "investigation nodes under your fork, each node ONE smaller "
        "goal of your fork's goal (you then work each node and tick "
        "it completed with update_plan_step only when its goal is "
        "settled). You cannot touch any other node, step, or agent."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        lines: List[str] = []
        try:
            lines.extend(await self._authority.sub_add(arguments))
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            lines.append(f"update_plan error: {type(exc).__name__}: {exc}")
        lines.append("Current plan:")
        try:
            lines.append(await self._authority.rendered())
        except Exception as exc:  # noqa: BLE001 — degrade, don't crash
            lines.append(f"(plan read failed: {type(exc).__name__}: {exc})")
        return "\n".join(lines)
