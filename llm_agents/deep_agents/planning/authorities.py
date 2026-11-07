"""llm_agents.deep_agents.planning.authorities — the plan-mutation permission model.

The thread's plan is a LIVING graph: every agent on the run — the MAIN
thread and each forked subagent — may UPDATE the plan when its work
hits something that needs further investigation. Who may touch what is
NOT model-decided: it is owned by this module's
:class:`PlanAuthority`, minted per conversation by the runner (the
model-facing TOOLS over an authority — schemas + handlers + kits —
live in ``tools.plan_tools``):

  MAIN authority (each step node's conversation in the step graph —
  the thread's main lane) — the thread as a whole is its scope:
    * the step's own lifecycle is RUNNER-side (the node ticks its
      state row in_progress/completed/failed — ticking is automatic
      bookkeeping, never a model tool on the main lane);
    * ``update_plan`` — add steps (optionally delegated forks),
      revise or remove NOT-YET-RUN steps only, and TERMINATE running
      background forks — "the LLM on the main thread can update the
      graph for nodes not yet run and terminate running sub agents".

  SUB authority (one per spawned subagent, bound to its fork state) —
  its forked path is its ONLY scope:
    * tick ONLY the states it minted itself, addressed by the state id
      the add returned;
    * add investigation nodes UNDER its own fork state (worked by the
      subagent itself — a fork node has no delegation marker, forking
      further happens through the spawn tool instead);
    * below the spawn-depth cap the subagent also carries
      spawn_subagent: a sub-sub agent's child state lands under ITS
      fork state the same way (registered by its own hooks), so its
      forked path may nest one level deeper;
    * no revise, no remove, no cancel — a subagent can never touch
      another node or agent's work ("sub agents can only update nodes
      after the forked node paths not affecting other nodes and
      agents").

Structural enforcement, not prompt enforcement: the sub handler's
writes are keyed to ``fork_state_id`` and the minted-id set, so a
hallucinated ordinal/state id in a subagent call is rejected by the
handler, and plan_store's pending-guards reject writes to started
rows. DB failures surface as tool-result text (the loop must keep
walking); the rows settle at finalize either way.

The EXTERNAL authority (:func:`external_add_steps`) is a third writer:
the daemon's POST /threads/:id/steps endpoint — an out-of-band caller
(operator tooling, another service) extending a RUNNING thread's main
lane. It is add-only and top-lane-only: settled rows are HISTORY and
never move (add_plan_states shifts later rows' ordinals, never a
status or a context pointer), pending rows on every path — the main
lane's future steps and each fork's subtree — are kept (children are
untouched: the shift filters parent_state_id IS NULL), and the same
vocabulary/caps as the model's own adds apply (business node names,
MAX_ADDS_PER_CALL, the configured max_total_plan_steps). The running
graph picks the new nodes up on its next hop — the router re-reads the
living tree between hops, so an external topology change lands exactly
like a model replan.
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.plan_json import (
    MAX_TITLE_LEN, parse_update_steps,
)
from llm_agents.deep_agents.planning.store import (
    PLAN_NODE,
    PlannedStep,
    StateRow,
    PLAN_STORE,
)

from llm_agents.deep_agents.planning.data_definitions import (
    PlanAuthority,
    ExternalPlanError,
    MAX_ADDS_PER_CALL,
    TICKABLE_FROM,
    render_added,
    render_plan,
)
logger = logging.getLogger("deep_agents.plan_tools")




# ----------------------------------------------------------------------
# Rendering — the model's source of truth after every mutation
# ----------------------------------------------------------------------




# ----------------------------------------------------------------------
# The authority
# ----------------------------------------------------------------------






# ----------------------------------------------------------------------
# The external authority — out-of-band topology adds on a RUNNING thread
# ----------------------------------------------------------------------
