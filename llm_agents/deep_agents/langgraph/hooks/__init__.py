"""llm_agents.deep_agents.langgraph.hooks — the thread-side delegation
wiring: spawn registration hooks and the spawn broker.

``SpawnHooks`` is the input/output/run-wrapper trio the step
graph stamps on every agent that can spawn (each inline step hop and
each delegated step's spawner): every spawned subagent becomes a CHILD
STATE under the step it belongs to (its spawned path, with its final
answer as the subagent-result context), mints its OWN LLM context id,
binds its request-log chain inside its task, and gets its spawn-scoped
plan kit mounted (``tools.plan_tools.SpawnPlanToolKit``) — all
fail-soft.

BUSINESS CONTEXT PASS-DOWN: the parent passes every spawned child the
run's business prompts as input — ``biz_context`` (the run's goal +
structured context + the rendered texts of the selected plan prompts)
is stamped on ``SubagentRequest.data``, so beside its spawn goal the
child's first user message carries what the run is ABOUT (subject,
focus question, the study instructions the user picked). The context
recurses: a sub-sub child receives it too.

The child state's node tag is a slug derived from the spawn goal's own
words (``spawn_<first-content-words>``, ``_spawn_node_slug``) — the
graph canvas labels the spawn with it, so it names the spawn's business
topic, not the spawn operation that created it.

NESTED SPAWNS: below the spawn-depth cap the input hook ALSO stamps the
child's OWN hook trio on ``SubagentRequest`` (parented at the child's
state row, its context as the parent context) — so a subagent that
spawns sub-sub agents registers them as child states UNDER ITS OWN
state row, one level deeper in the same thread tree, under the same
supervision contract. At the cap the child carries no spawn tool and
gets no hooks (nothing to register).

``SpawnBroker`` is the main lane's spawn canceller: ``update_plan``'s
terminate op routed to the agent's own registry — it stops the child
tasks and settles their spawn states immediately (``failed 'cancelled
by plan update'``) instead of waiting for finalize. Late-bound by
design (the handlers close over the authority that closes over the
broker that binds the agent after its ctor).

(PACKAGE NOTE, 2026-11-07: this was the single
``langgraph/hooks.py`` module; the split moved every
class/function body byte-identical into the submodules
below, and this package re-exports the same names.)
"""
from __future__ import annotations

from .broker import SpawnBroker
from .spawns import SpawnHooks
from .slug import _SPAWN_STOPWORDS, _spawn_node_slug  # noqa: F401

__all__ = [
    "SpawnBroker", "SpawnHooks",
]


