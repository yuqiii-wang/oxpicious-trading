"""llm_agents.deep_agents.planning — the living-plan DOMAIN: the
plan-JSON contract, the proposal session, its persistence, and the
mutation authorities.

The thread's plan is a living graph with a MAIN LANE and FORKED PATHS,
persisted as the ``text.deep_agent_states`` tree (SQL-first DDL
``database/sql/text/09_deep_agent_states.sql``). This layer owns the
whole contract:

  * ``plan_json``   — the planner's reply contract: validation
    (structure AND business naming) for the states JSON (steps with
    optional delegation goals), and the same validation for runtime
    ``update_plan`` adds;
  * ``planner``     — the proposal SESSION as an independent class
    (:class:`Planner`): it assembles what the model reads (the
    business framing, the dynamic templates, the selected plan
    prompts' texts), calls the LLM, and on ANY rejection feeds the
    specific complaint back for correction — until the reply compiles
    into accepted thread states or the rounds run out (the runner
    degrades to no-plan). Pure: no DB, no provider import — the chat
    callable rides in duck-typed;
  * ``store``       — the plan tree persistence (create/add/revise/
    skip/tick over ``text.deep_agent_states`` + contexts + identities);
  * ``authorities`` — WHO may mutate what: the main lane may rework
    not-yet-run steps and terminate forks; a forked subagent is
    structurally confined to the nodes it minted under its own fork.
    The agent-facing tools (update_plan / the sub kit's tick) are
    handlers closed over a PlanAuthority — permission is enforced in
    code, never trusted to the prompt.

Purely domain-level: no LangGraph, no agent loop — both ``core`` (the
subagent fork kit) and ``graph`` (the step tools) consume it.
"""
from llm_agents.deep_agents.planning.authorities import (
    PlanAuthority, main_plan_tools, subagent_plan_tools,
)
from llm_agents.deep_agents.planning.plan_json import (
    PLAN_SYSTEM_PROMPT, parse_plan_json, parse_update_steps,
)
from llm_agents.deep_agents.planning.planner import (
    Planner, business_framing, plan_reply_steps, plan_user_content,
    render_template, template_args,
)
from llm_agents.deep_agents.planning.store import (
    PlannedStep, StateRow, ThreadPlan, purge_thread,
)

__all__ = [
    "PLAN_SYSTEM_PROMPT", "Planner", "PlanAuthority", "PlannedStep",
    "StateRow", "ThreadPlan", "business_framing", "main_plan_tools",
    "parse_plan_json", "parse_update_steps", "plan_reply_steps",
    "plan_user_content", "purge_thread", "render_template",
    "subagent_plan_tools", "template_args",
]
