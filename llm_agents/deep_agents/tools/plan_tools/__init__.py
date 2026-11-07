"""llm_agents.deep_agents.tools.plan_tools — the plan-mutation tools as
OBJECTS over a PlanAuthority.

The thread's plan is a LIVING graph, and every agent on the run — the
MAIN thread and each forked subagent — carries tools to UPDATE it when
its work hits something that needs further investigation. WHO may touch
what is NOT model-decided: it is owned by
:class:`planning.authorities.PlanAuthority` (the permission model,
minted per conversation by the runner and structurally enforced in the
handlers below — a subagent's writes are keyed to its fork state and
its minted-id set, so a hallucinated ordinal/state id is rejected, and
plan_store's pending-guards reject writes to started rows). These tool
classes are the model-facing half: the schemas, the ``run`` handlers
(the former handler closures, transplanted verbatim) and the kits the
runner / delegation hooks mount.

  MAIN authority (each step node's conversation in the step graph —
  the thread's main lane) — ``MainPlanToolKit``:
    * the step's own lifecycle is RUNNER-side (the node ticks its
      state row in_progress/completed/failed — ticking is automatic
      bookkeeping, never a model tool on the main lane);
    * ``update_plan`` — add steps (optionally delegated forks), revise
      or remove NOT-YET-RUN steps only, and TERMINATE running
      background forks;
    * ``search_thread_context`` — the thread-internal traffic search
      every level's kit carries.

  SUB authority (one per spawned subagent, bound to its fork state) —
  ``ForkPlanToolKit``:
    * tick ONLY the states it minted itself (``update_plan_step`` by
      the state id its add returned);
    * add investigation nodes UNDER its own fork state
      (``update_plan`` add_nodes); no revise, no remove, no cancel —
      a subagent can never touch another node's or agent's work.

The rendered plan (``authorities.render_plan``) is the model's source
of truth after every mutation — each update tool's RESULT carries the
current rendered tree, so a replan never depends on the model
remembering its own edit. DB failures surface as tool-result text (the
loop must keep walking); the rows settle at finalize either way.

The DEFAULT_DESCRIPTION constants seed the tools' ``llm.ai_prompts``
rows (prompt_type 4, sub_types 'plan_main' / 'plan_fork' / 'plan');
a kit built with the run's descriptions map overrides them fail-soft.

Package layout (the former single plan_tools.py, split):

  addenda.py    — the fork addenda (the kits' system-prompt surface);
  common.py     — the shared handler core (lifecycle guard, rendered
                  echo, SUB-scope add body) + _PlanAuthorityTool;
  tick.py       — UpdatePlanStepTool (the fork's tick tool);
  update_plan.py— UpdatePlanTool (main) / SubUpdatePlanTool (fork);
  kits.py       — MainPlanToolKit / ForkPlanToolKit.

This root re-exports the whole public surface (plus the kits'
ThreadSearchTool), so every importer keeps importing from
``tools.plan_tools`` unchanged.
"""
from llm_agents.deep_agents.tools.plan_tools.addenda import (
    SUBAGENT_PLAN_ADDENDUM, SUBAGENT_SPAWN_ADDENDUM,
)
from llm_agents.deep_agents.tools.plan_tools.kits import (
    ForkPlanToolKit, MainPlanToolKit,
)
from llm_agents.deep_agents.tools.plan_tools.tick import UpdatePlanStepTool
from llm_agents.deep_agents.tools.plan_tools.update_plan import (
    SubUpdatePlanTool, UpdatePlanTool,
)
from llm_agents.deep_agents.tools.thread_search import ThreadSearchTool

__all__ = [
    "ForkPlanToolKit", "MainPlanToolKit", "SUBAGENT_PLAN_ADDENDUM",
    "SUBAGENT_SPAWN_ADDENDUM", "SubUpdatePlanTool", "ThreadSearchTool",
    "UpdatePlanStepTool", "UpdatePlanTool",
]
