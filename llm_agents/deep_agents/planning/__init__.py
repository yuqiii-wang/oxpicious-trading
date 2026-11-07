"""llm_agents.deep_agents.planning — the living-plan DOMAIN: the
plan-JSON contract, the proposal session, its persistence, and the
mutation authorities.

The thread's plan is a living graph with a MAIN LANE and FORKED PATHS,
persisted as the ``llm.deep_agent_states`` tree (SQL-first DDL
``database/sql/text/llm/07_deep_agent_states.sql``). This layer owns
the whole contract:

  * ``plan_json``   — the planner's reply contract (a subpackage,
    2026-11-07): validation (structure AND business naming) for the
    states JSON (steps with optional delegation goals), the planning
    system prompts + the DB-editable repair/followup frames, and the
    same validation for runtime ``update_plan`` adds;
  * ``planner``     — the proposal SESSION as an independent class
    (:class:`Planner`, a subpackage): it assembles what the model
    reads (the business framing, the dynamic templates, the selected
    plan prompts' texts), calls the LLM, and on ANY rejection feeds
    the specific complaint back for correction — until the reply
    compiles into accepted thread states or the rounds run out (the
    runner degrades to no-plan). Pure: no DB, no provider import —
    the chat callable rides in duck-typed;
  * ``session``     — the plan agent's ENGINE (:class:`PlanSession`, a
    subpackage): the batched proposal stream persisting each validated
    batch NOW, the gradual prompt-loading ladder resolved alongside,
    the fork pass-down and the standing opening prompt. Run by
    ``agents.plan_agent.PlanAgent`` BEFORE the graphs invoke;
  * ``prompt_loading`` — the gradual discretionary prompt-loading
    ladder (a subpackage): catalog previews → keyword match → the
    KG-hotpath corroboration → the planner verdict → execution-time
    discretion, plus the planner-scoped data compaction;
  * ``store``       — the plan tree persistence (a subpackage of
    domain mixins composed into one ``PlanStore``: create/add/revise/
    skip/tick over ``llm.deep_agent_states`` + contexts + identities)
    and the prompt accessors (system / agent / tool / plan rows over
    ``llm.ai_prompts``);
  * ``authorities`` — WHO may mutate what: the main lane may rework
    not-yet-run steps and terminate forks; a forked subagent is
    structurally confined to the nodes it minted under its own fork.
    The model-facing tools over an authority (schemas + handlers +
    kits) live in the tool layer (``tools.plan_tools``); permission
    stays enforced in code here, never trusted to the prompt;
  * ``templates``   — the runner side of saved-plan reuse (the zero-LLM
    template lane);
  * ``thread_search`` — the thread-internal LLM-traffic search BODY
    (the model-facing tool object is ``tools.thread_search``);
  * ``kg_prompts``  — the KG-hotpath corroboration probe (plan-time
    consult of the promoted ``llm.kg_hotpaths`` chains).

Purely domain-level: no LangGraph, no agent loop — ``core`` (the
subagent fork kit), ``skills`` (the selected-prompt objects), the tool
layer (the plan kits) and ``graph`` (the orchestration) consume it; it
imports none of them back.

The five former single-file modules (store / plan_json / planner /
prompt_loading / session) are SUBPACKAGES since 2026-11-07 — each
root re-exports its whole prior surface, so every importer keeps
importing ``planning.<module>`` unchanged. This package root carries
the same shield for the convenience names below (nothing in the repo
imports the package level today; the re-exports stay maintained).
"""
from llm_agents.deep_agents.planning.authorities import (
    ExternalPlanError, PlanAuthority,
)
from llm_agents.deep_agents.planning.plan_json import (
    parse_plan_json, parse_update_steps, plan_system_prompt,
)
from llm_agents.deep_agents.planning.planner import (
    PlanPromptAssembler, Planner, TemplateArgs,
    render_template, template_args,
)
from llm_agents.deep_agents.planning.store import (
    PLAN_STORE, PlannedStep, StateRow, ThreadPlan,
)

__all__ = [
    "Planner", "PlanPromptAssembler", "TemplateArgs",
    "ExternalPlanError", "PlanAuthority",
    "PlannedStep", "StateRow", "ThreadPlan",
    "PLAN_STORE",
    "parse_plan_json", "parse_update_steps",
    "plan_system_prompt",
    "render_template", "template_args",
]
