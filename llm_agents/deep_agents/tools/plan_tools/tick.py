"""llm_agents.deep_agents.tools.plan_tools.tick — ``update_plan_step``,
the FORK's tick tool: a subagent ticks ONLY the nodes its own
update_plan minted, addressed by state id.
"""
from __future__ import annotations

from typing import Any, ClassVar, Dict, Tuple

from llm_agents.deep_agents.planning.authorities import PlanAuthority
from llm_agents.deep_agents.planning.store import PLAN_STORE

from llm_agents.deep_agents.tools.plan_tools.common import (
    _PlanAuthorityTool,
)


class UpdatePlanStepTool(_PlanAuthorityTool):
    """``update_plan_step`` — the FORK's tick tool: a subagent ticks
    ONLY the nodes its own update_plan minted, addressed by state id."""

    name: ClassVar[str] = "update_plan_step"
    db_key: ClassVar[Tuple[str, str]] = (name, "plan_fork")
    parameters: ClassVar[Dict[str, Any]] = {
        "type": "object",
        "properties": {
            "state_id": {
                "type": "integer",
                "description": "The state id your update_plan "
                               "add reported for the node.",
            },
            "status": {
                "type": "string",
                "enum": ["in_progress", "completed", "failed"],
                "description": "The node's new status.",
            },
            "note": {
                "type": "string",
                "description": "One line recording what the node "
                               "confirmed or why it failed.",
            },
            "critical": {
                "type": "boolean",
                "description": "A MUST CHECK: set true when the note "
                               "records a CRITICAL DISCOVERY — a "
                               "finding that changes the run's "
                               "conclusion (a material break in the "
                               "evidence, a risk/trigger the plan "
                               "must react to, a number that "
                               "overturns the working view). Flagged "
                               "rows surface as CRITICAL in "
                               "search_thread_context and weigh on "
                               "the reviews and the final answer.",
            },
        },
        "required": ["state_id", "status"],
    }
    DEFAULT_DESCRIPTION: ClassVar[str] = (
        "Tick one of YOUR OWN fork's investigation nodes by the "
        "state id its update_plan call reported: 'in_progress' when "
        "you start it, 'completed' only when the node's goal is "
        "settled (the tick marks that goal finished) or 'failed' "
        "when it cannot be — plus a one-line note recording what "
        "settled or why it failed. CRITICAL is a MUST CHECK on every "
        "completed tick: pass critical=true when the note records a "
        "critical discovery — a finding that changes the run's "
        "conclusion (a material break in the evidence, a "
        "risk/trigger the plan must react to, a number that "
        "overturns the working view); flagged notes surface as "
        "CRITICAL in search_thread_context and weigh on the reviews "
        "and the final answer. You cannot tick any other node."
    )

    async def run(self, arguments: Dict[str, Any]) -> str:
        authority = self._authority
        status = str(arguments.get("status") or "")
        if status not in ("in_progress", "completed", "failed"):
            return ("update_plan_step error: status must be "
                    "in_progress|completed|failed")
        note = arguments.get("note")
        note_text = str(note).strip()[:500] if note else None
        if authority.is_main:
            try:
                ordinal = int(arguments.get("ordinal"))
            except (TypeError, ValueError):
                return ("update_plan_step error: 'ordinal' must be an "
                        "integer")
            state = None
            for row in await PlanAuthority._main_lane_states(authority.thread_id):
                if row.ordinal == ordinal:
                    state = row
                    break
            if state is None:
                return (f"update_plan_step error: no plan step with "
                        f"ordinal {ordinal}")
        else:
            try:
                state_id = int(arguments.get("state_id"))
            except (TypeError, ValueError):
                return ("update_plan_step error: 'state_id' must be an "
                        "integer")
            if state_id not in authority.minted:
                return ("update_plan_step error: you may only tick nodes "
                        "your own update_plan added under your fork")
            state = await PLAN_STORE.fetch_state(state_id)
            if state is None:
                return (f"update_plan_step error: state {state_id} is gone")
        complaint = self._authority.tick_transition(state, status)
        if complaint is not None:
            return f"update_plan_step error: {complaint}"
        critical = arguments.get("critical") is True
        try:
            await PLAN_STORE.tick_step(state.state_id, status,
                            thread_id=authority.thread_id, note=note_text,
                            node=state.node,
                            llm_context_id=authority.llm_context_id,
                            critical=critical)
        except Exception as exc:  # noqa: BLE001 — tool text, not a crash
            return f"update_plan_step error: {type(exc).__name__}: {exc}"
        # Keep the runner's spawn parenting pointed at the open step —
        # and remember the last ticked step as the fallback parent for
        # forks spawned in the same step as their tick.
        if authority.last_state_id is not None:
            authority.last_state_id[0] = state.state_id
        if authority.current_state_id is not None:
            if status == "in_progress":
                authority.current_state_id[0] = state.state_id
            elif authority.current_state_id[0] == state.state_id:
                authority.current_state_id[0] = None
        what = (f"plan step {state.ordinal}" if authority.is_main
                else f"fork node {state.state_id}")
        return f"{what} marked {status}"
