"""llm_agents.deep_agents.langgraph.hooks — the thread-side delegation
wiring: fork registration hooks and the fork broker.

``ForkHooks`` is the input/output/run-wrapper trio the step
graph stamps on every agent that can spawn (each inline step hop and
each delegated step's spawner): every spawned subagent becomes a CHILD
STATE under the step it belongs to (its forked path, with its final
answer as the subagent-result context), mints its OWN LLM context id,
binds its request-log chain inside its task, and gets its fork-scoped
plan kit mounted (``tools.plan_tools.ForkPlanToolKit``) — all
fail-soft.

BUSINESS CONTEXT PASS-DOWN: the parent passes every forked child the
run's business prompts as input — ``biz_context`` (the run's goal +
structured context + the rendered texts of the selected plan prompts)
is stamped on ``SubagentRequest.data``, so beside its fork goal the
child's first user message carries what the run is ABOUT (subject,
focus question, the study instructions the user picked). The context
recurses: a sub-sub child receives it too.

The child state's node tag is a slug derived from the fork goal's own
words (``fork_<first-content-words>``, ``_fork_node_slug``) — the
graph canvas labels the fork with it, so it names the fork's business
topic, not the spawn operation that created it.

NESTED FORKS: below the spawn-depth cap the input hook ALSO stamps the
child's OWN hook trio on ``SubagentRequest`` (parented at the child's
state row, its context as the parent context) — so a subagent that
spawns sub-sub agents registers them as child states UNDER ITS OWN
state row, one level deeper in the same thread tree, under the same
supervision contract. At the cap the child carries no spawn tool and
gets no hooks (nothing to register).

``ForkBroker`` is the main lane's fork canceller: ``update_plan``'s
terminate op routed to the agent's own registry — it stops the child
tasks and settles their fork states immediately (``failed 'cancelled
by plan update'``) instead of waiting for finalize. Late-bound by
design (the handlers close over the authority that closes over the
broker that binds the agent after its ctor).

(PACKAGE NOTE, 2026-11-07: this was the single
``langgraph/hooks.py`` module; the split moved every
class/function body byte-identical into the submodules
below, and this package re-exports the same names.)
"""
from __future__ import annotations

from .broker import ForkBroker
from .forks import ForkHooks
from .slug import _FORK_STOPWORDS, _fork_node_slug  # noqa: F401

__all__ = [
    "ForkBroker", "ForkHooks",
]


