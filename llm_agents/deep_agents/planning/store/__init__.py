"""llm_agents.deep_agents.planning.store — thread plan/state persistence.

The DB side of the deep-agent thread design: ``llm.deep_agent_states``
(one row per graph node/task state of a run, each referencing its LLM
context by ``llm_context_id`` — a reference only, the context data
lives nowhere by design),
``llm.deep_agent_contexts`` (the JSONB payloads states point into —
ONE ENTRY per thread per node per task/step, each keyed by its node,
its task row and the chained llm_context_id of the node conversation
that produced it, so the payloads chain under their node's ONE context
and link its ``llm.llm_requests`` rows; each entry ALSO carries
``is_critical`` — lifted from the payload's "critical" flag, the
model's own [CRITICAL]-marker / tick-argument mark of a
run-conclusion-changing discovery — and ``tags``, the
StructBERT-tokenized GIN-indexed vector over the payload text that the
thread-internal search matches THREAD-SCOPED, both written in
:meth:`_StoreBase._insert_context`, the table's ONE write place) and
``llm.deep_agent_identities`` (the code <-> thread_id link — one row
per scoped run, the reverse index the summary history's per-code read
filters on). The LangGraph STORE's items live beside these in
``llm.deep_agent_store_items`` (``state_store.py`` — node states and
the context registry). DDL ``database/sql/text/llm/07_deep_agent_states.sql``
+ ``10_deep_agent_store.sql`` — LangGraph's thread / node / task /
state / subgraph concepts without the framework.

Every accessor is a method of ONE service object, :class:`PlanStore` —
the module-level functions became its methods (2026-11-06) with
signatures unchanged; every method raises on DB failure and the
degrade-to-plain-run policy lives in the runner, which wraps each call
fail-soft — a DB outage must never fail the summary, the same
never-block contract as ``llm_agents._core.request_log``.

Write volume is tiny (a handful of rows per run), so everything is the
plain-SQL rung: one short-lived asyncpg connection per call via
``_common.db_commons`` (imported lazily — the activation-order rule the
other llm_agents modules follow). The sql_writes mirror keys on
``database/sql_writes/llm_agents/deep_agents/planning/store/<table>/
<action>.sql`` — the package path IS the mirror path.

Package layout (the former single store.py, split 2026-11-07):

  models.py      — the row contracts + shared vocabulary + the pure
                   payload helpers (extract_critical, tags tokenizer);
  base.py        — _StoreBase: _connect, _insert_context (the ONE
                   contexts write place), _state_row;
  prompts.py     — PromptStoreMixin: ai_prompts accessors +
                   AGENT_PROMPT_* keys + hotpath evidence;
  states.py      — StateStoreMixin: the plan/step lifecycle writes;
  outputs.py     — OutputStoreMixin: final-result / graph-output /
                   supervisor-review rows + finalize_thread;
  chain.py       — ChainStoreMixin: chain reads / purges / settle +
                   the tags backfill;
  identities.py  — IdentityStoreMixin: the scope -> identity link.

This root composes them into ``class PlanStore`` and re-exports the
whole public surface, so every importer keeps importing
from ``planning.store`` unchanged.
"""
from llm_agents.deep_agents.planning.store.base import _StoreBase
from llm_agents.deep_agents.planning.store.chain import ChainStoreMixin
from llm_agents.deep_agents.planning.store.identities import (
    IdentityStoreMixin, identities_from_scope,
)
from llm_agents.deep_agents.planning.store.models import (
    CRITICAL_KEY, CRITICAL_MARKER, IGNORE_NOTE, PLAN_NODE, RESULT_NODE,
    SUPERVISOR_NODE, TICK_STATUSES, HotpathChainRow, PlannedStep,
    PromptCatalogRow, PromptRow, StateRow, ThreadIdentity, ThreadPlan,
    extract_critical,
)
from llm_agents.deep_agents.planning.store.outputs import OutputStoreMixin
from llm_agents.deep_agents.planning.store.prompts import (
    AGENT_PROMPT_COMPACT_SYSTEM, AGENT_PROMPT_CUSTOM_OUTPUT_DIRECTIVE,
    AGENT_PROMPT_FORK_PLAN_ADDENDUM, AGENT_PROMPT_FORK_SPAWN_ADDENDUM,
    AGENT_PROMPT_PLAN_BATCH_FOLLOWUP, AGENT_PROMPT_PLAN_REPAIR_MESSAGE,
    AGENT_PROMPT_PLAN_SYSTEM, AGENT_PROMPT_RESULT_OUTPUT_DIRECTIVE,
    AGENT_PROMPT_STEP_ADDENDUM, AGENT_PROMPT_STANDING_SYSTEM,
    AGENT_PROMPT_SUBAGENT_SYSTEM, AGENT_PROMPT_SUBPLAN_SYSTEM,
    AGENT_PROMPT_SUPERVISOR_INTERVENTION, AGENT_PROMPT_SUPERVISOR_SYSTEM,
    PromptStoreMixin,
)
from llm_agents.deep_agents.planning.store.states import StateStoreMixin


class PlanStore(StateStoreMixin, ChainStoreMixin, OutputStoreMixin,
                PromptStoreMixin, IdentityStoreMixin, _StoreBase):
    """The DB side of the deep-agent thread design, as ONE service object
    (2026-11-06 — the former ~30 module-level accessors; 2026-11-07 the
    methods grouped into the domain mixins this class composes): every
    method keeps the function signature it had (``thread_id`` stays a
    parameter — the store is thread-agnostic, serving the whole chain)
    and the package keeps a process-wide :data:`PLAN_STORE` instance.
    Every write also announces its deep-agent event (``events.publish``)
    as a typed ``events.DeepAgentEvent``. The degrade-to-plain-run
    policy still lives in the runner, which wraps each call fail-soft —
    a DB outage must never fail the summary.
"""


# The process-wide store instance — the module-level accessors became
# its methods (2026-11-06); every consumer holds this one object.
PLAN_STORE = PlanStore()

__all__ = [
    # rows
    "PlannedStep", "StateRow", "ThreadPlan", "ThreadIdentity",
    "PromptRow", "PromptCatalogRow", "HotpathChainRow",
    # store
    "PlanStore", "PLAN_STORE",
    # vocabulary + helpers
    "TICK_STATUSES", "PLAN_NODE", "RESULT_NODE", "SUPERVISOR_NODE",
    "IGNORE_NOTE", "CRITICAL_KEY", "CRITICAL_MARKER", "extract_critical",
    "identities_from_scope",
    # the internal prompt-row keys
    "AGENT_PROMPT_PLAN_SYSTEM", "AGENT_PROMPT_STEP_ADDENDUM",
    "AGENT_PROMPT_SUBAGENT_SYSTEM", "AGENT_PROMPT_FORK_PLAN_ADDENDUM",
    "AGENT_PROMPT_FORK_SPAWN_ADDENDUM", "AGENT_PROMPT_SUPERVISOR_SYSTEM",
    "AGENT_PROMPT_STANDING_SYSTEM", "AGENT_PROMPT_CUSTOM_OUTPUT_DIRECTIVE",
    "AGENT_PROMPT_RESULT_OUTPUT_DIRECTIVE", "AGENT_PROMPT_SUBPLAN_SYSTEM",
    "AGENT_PROMPT_PLAN_REPAIR_MESSAGE", "AGENT_PROMPT_PLAN_BATCH_FOLLOWUP",
    "AGENT_PROMPT_COMPACT_SYSTEM", "AGENT_PROMPT_SUPERVISOR_INTERVENTION",
]
