"""llm_agents.deep_agents.graph.hooks — the thread-side delegation
wiring: fork registration hooks and the fork broker.

``subagent_hooks`` builds the input/output/run-wrapper trio the step
graph stamps on every agent that can spawn (each inline step hop and
each delegated step's spawner): every spawned subagent becomes a CHILD
STATE under the step it belongs to (its forked path, with its final
answer as the subagent-result context), mints its OWN LLM context id,
binds its request-log chain inside its task, and gets its fork-scoped
plan kit mounted (``planning.authorities.subagent_plan_tools``) — all
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
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from langgraph.store.base import BaseStore

from llm_agents._core.request_log import bind_request_chain, end_request_chain
from llm_agents.deep_agents.core.delegation import (
    MAX_SPAWN_DEPTH, SubagentRun, format_subagent_result,
)
from llm_agents.deep_agents.core.loop import DeepAgent
from llm_agents.deep_agents.graph.state_store import (
    entry_checkpoint_id, new_context_id, record_subagent_context,
)
from llm_agents.deep_agents.planning.authorities import (
    SUBAGENT_SPAWN_ADDENDUM, subagent_plan_tools,
)
from llm_agents.deep_agents.planning.plan_json import (
    MAX_NODE_LEN, MAX_TITLE_LEN,
)
from llm_agents.deep_agents.planning.store import (
    AGENT_PROMPT_FORK_PLAN_ADDENDUM, AGENT_PROMPT_FORK_SPAWN_ADDENDUM,
    AGENT_PROMPT_SUBAGENT_SYSTEM, create_child_state, finish_child_state,
)

logger = logging.getLogger("deep_agents.hooks")

# Function words dropped from a fork goal when slugging its node tag —
# what remains are the goal's content words (the fork's business
# topic: its subject, its metric, its horizon).
_FORK_STOPWORDS = frozenset((
    "the", "a", "an", "of", "for", "in", "to", "on", "at", "and", "or",
    "with", "from", "by", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "its", "it", "as", "into",
    "over", "under", "about", "you", "your", "we", "our", "use",
    "using", "then", "when", "which", "what", "how", "whether", "if",
    "does", "do", "did", "can", "could", "should", "would", "must",
    "not", "no", "vs", "per", "via", "please",
))


def _fork_node_slug(goal: str) -> str:
    """The fork child state's node tag, derived from the goal's OWN
    words: the first few content words (stopwords dropped), snake-
    joined under the ``fork_`` prefix — the graph canvas labels the
    fork with this tag, so it must name the fork's business topic
    (``fork_moving_average_000300``), never the spawn operation. Falls
    back to the bare prefix when the goal yields nothing slug-able."""
    tokens = re.findall(r"[0-9a-zA-Z\u4e00-\u9fff]+", (goal or "").lower())
    kept = [t for t in tokens if t not in _FORK_STOPWORDS][:4]
    slug = "_".join(kept).strip("_")
    return f"fork_{slug}"[:MAX_NODE_LEN] if slug else "fork"


@dataclass
class ForkBroker:
    """The main authority's fork canceller — update_plan's terminate op
    routed to the execute agent's own registry. Late-bound (the agent
    exists only after its handlers, which close over the authority that
    closes over the broker); ``state_by_run`` is the SAME map the
    subagent input hook registers child states in, and
    ``context_by_run`` the SAME map it registers their LLM-context ids
    in, so a cancelled fork's state settles with its chained-context
    keys instead of waiting for finalize."""

    agent: Optional[DeepAgent] = None
    thread_id: str = ""
    state_by_run: Dict[str, int] = field(default_factory=dict)
    context_by_run: Dict[str, Optional[str]] = field(default_factory=dict)

    async def cancel(self, run_ids: Optional[List[str]]) -> List[str]:
        """Stop the named background forks (every running one when
        *run_ids* is None) and settle their fork states; returns the ids
        that actually stopped."""
        if self.agent is None:
            return []
        live = [r for r in self.agent.subagent_runs()
                if r.background and r.task is not None and not r.task.done()]
        targets = ([r for r in live if r.run_id in set(run_ids)]
                   if run_ids is not None else live)
        stopped: List[str] = []
        for run in targets:
            if not self.agent.cancel_subagent(run.run_id):
                continue
            stopped.append(run.run_id)
            state_id = self.state_by_run.get(run.run_id)
            if state_id is None:
                continue
            try:
                await finish_child_state(
                    state_id, "failed",
                    error="cancelled by plan update (fork terminated)",
                    thread_id=self.thread_id,
                    node=_fork_node_slug(run.request.goal),
                    llm_context_id=self.context_by_run.get(run.run_id))
            except Exception as exc:  # noqa: BLE001 — settle at finalize
                logger.warning("cancelled fork %s state settle failed: %s",
                               run.run_id, exc)
        return stopped


def subagent_hooks(
    thread_id: str, step_state_id: Optional[int],
    *,
    store: Optional[BaseStore] = None,
    parent_context_id: Optional[str] = None,
    model: Optional[str] = None,
    state_by_run: Optional[Dict[str, int]] = None,
    context_by_run: Optional[Dict[str, Optional[str]]] = None,
    biz_context: Optional[Dict[str, Any]] = None,
    agent_prompts: Optional[Dict[str, str]] = None,
) -> Tuple[Any, Any, Any]:
    """Input/output hooks registering each spawned subagent as a child
    state under THE STEP the spawn belongs to (``step_state_id`` — the
    step hop knows its own row; a missing/None id parents top-level,
    the degraded no-plan case) — its FORKED PATH — with its final
    answer (or error) as the child's subagent-result context; both
    fail-soft.

    The input hook also MOUNTS THE CHILD'S PLAN KIT
    (``plan_tools.subagent_plan_tools``): the fork-scoped
    update_plan/update_plan_step tools plus the system addendum that
    teaches them, stamped on ``SubagentRequest.builtin_tools`` /
    ``.system`` — so every subagent may extend its OWN fork when its
    work hits something needing further investigation, and nothing else
    (the authority enforces it structurally). ``state_by_run`` is the
    fork broker's cancel-settle map (a private dict when None).

    ``biz_context`` is the PARENT'S PASS-DOWN: the run's business
    input (goal + structured context + the rendered selected-prompt
    texts) stamped on ``SubagentRequest.data`` — beside its fork goal,
    every child (and, through the recursion, every sub-sub child)
    receives the prompts the fork's work runs under. None = plain
    non-thread usage (the child sees only its goal).

    ``agent_prompts`` is the run-start resolution of the agent's
    INTERNAL prompt rows (text.ai_prompts prompt_type 3, seeded from
    the Python constants by 15_deep_agent_prompts.sql): the child's
    base system prompt and the fork addenda come from here when their
    keys are present, falling back to the built-in constants otherwise.

    Below the spawn-depth cap the child also receives ITS OWN hook
    trio (stamped on ``SubagentRequest.subagent_*_hook``): the child's
    spawns become child states under ITS state row — the sub-sub forked
    paths of the thread tree — with their contexts parented at the
    child's context, so every rung of the hierarchy registers the same
    way the top-level forks do.

    ONE NODE ONE LLM CONTEXT, the subagent case: a child is its own
    conversation, so the input hook mints the child's OWN context id
    (stored on the child state row; the fork's added nodes stamp it
    too) and the output hook closes its registry item — counts only,
    the messages never persisted. The third element, the RUN WRAPPER,
    binds the child's request-log chain (chain_id = the child's context
    id) inside the child task — the child's text.llm_requests rows
    carry its own context id, not the parent step context's.

    Returns (input_hook, output_hook, run_wrapper)."""
    if state_by_run is None:
        state_by_run = {}
    # The child contexts registry: the caller (the fork broker) shares
    # its own map so the cancel path can key the settle too; a private
    # dict otherwise (plain non-thread usage).
    if context_by_run is None:
        context_by_run = {}

    async def input_hook(run: SubagentRun) -> None:
        context_id = new_context_id() if store is not None else None
        # The parent's prompt pass-down: the child sees its fork goal
        # AND the run's business context (rendered into its first user
        # message as the structured-context block). Never overwrite a
        # context a previous hook layer already stamped.
        if run.request.data is None and biz_context:
            run.request.data = dict(biz_context)
        # The child's base prompt: the DB-managed internal row when
        # active (15_deep_agent_prompts.sql), else the built-in
        # constant the request was built with.
        base = (agent_prompts or {}).get(AGENT_PROMPT_SUBAGENT_SYSTEM)
        if base:
            run.request.system = base
        state_id: Optional[int] = None
        try:
            state_id = await create_child_state(
                thread_id, step_state_id, _fork_node_slug(run.request.goal),
                run.request.goal[:MAX_TITLE_LEN],
                llm_context_id=context_id)
            state_by_run[run.run_id] = state_id
            context_by_run[run.run_id] = context_id
        except Exception as exc:  # noqa: BLE001 — degrade, don't veto
            logger.warning("child state create failed for subagent %s: %s",
                           run.run_id, exc)
        # The fork kit rides even when the state row failed (the
        # authority still confines writes to the fork's own adds — a
        # lost row only loses the audit trail, fail-soft like the rest).
        if state_id is not None:
            tools, addendum = subagent_plan_tools(
                thread_id, state_id, context_id)
            run.request.builtin_tools = tools
            addendum = ((agent_prompts or {}).get(
                AGENT_PROMPT_FORK_PLAN_ADDENDUM) or addendum)
            spawn_addendum = ""
            if run.request.depth < MAX_SPAWN_DEPTH:
                # The child can fork sub-sub agents: teach it, and hand
                # it its OWN hook trio so its spawns register as child
                # states under its state row (parent context = its
                # context). The trio recurses naturally — a depth-1
                # child's input hook stamps nothing at depth 2, the cap
                # — and the business context passes down with it.
                child_input, child_output, child_wrapper = subagent_hooks(
                    thread_id, state_id,
                    store=store, parent_context_id=context_id,
                    model=model, biz_context=biz_context,
                    agent_prompts=agent_prompts)
                run.request.subagent_input_hook = child_input
                run.request.subagent_output_hook = child_output
                run.request.subagent_run_wrapper = child_wrapper
                spawn_addendum = ((agent_prompts or {}).get(
                    AGENT_PROMPT_FORK_SPAWN_ADDENDUM)
                    or SUBAGENT_SPAWN_ADDENDUM)
            run.request.system = (
                f"{run.request.system}\n\n{addendum}{spawn_addendum}")

    def run_wrapper(run: SubagentRun) -> Any:
        """The child's request-log chain binding, applied inside the
        child's task (agent.SubagentRunWrapper). None = no context was
        minted (store-less run) — the child's requests then ride the
        inherited execute chain, an acceptable degrade."""
        context_id = context_by_run.get(run.run_id)
        return None if context_id is None else bind_request_chain(context_id)

    async def output_hook(run: SubagentRun) -> str:
        # The child's last request is recorded by now — its chain's
        # dedupe cache entry is dead weight; drop it.
        end_request_chain(context_by_run.get(run.run_id))
        state_id = state_by_run.get(run.run_id)
        if state_id is not None:
            try:
                await finish_child_state(
                    state_id,
                    "completed" if run.status == "completed" else "failed",
                    answer=run.answer, error=run.error,
                    thread_id=thread_id,
                    node=_fork_node_slug(run.request.goal),
                    llm_context_id=context_by_run.get(run.run_id))
            except Exception as exc:  # noqa: BLE001 — degrade, don't lose
                logger.warning("child state finish failed for subagent "
                               "%s: %s", run.run_id, exc)
        context_id = context_by_run.get(run.run_id)
        if state_id is not None and context_id is not None:
            try:
                # The child runs inside its step's superstep — the entry
                # checkpoint id HERE is that superstep's state id. The
                # registry item wears the same business slug as the
                # child state row.
                await record_subagent_context(
                    store, thread_id, context_id, state_id=state_id,
                    parent_context_id=parent_context_id,
                    checkpoint_id=entry_checkpoint_id(), model=model,
                    n_llm_calls=run.steps,
                    n_tool_calls=run.tool_call_count,
                    node=_fork_node_slug(run.request.goal))
            except Exception as exc:  # noqa: BLE001 — registry is bookkeeping
                logger.warning("child context item failed for subagent "
                               "%s: %s", run.run_id, exc)
        return format_subagent_result(run)

    return input_hook, output_hook, run_wrapper


# ----------------------------------------------------------------------
# The graph
# ----------------------------------------------------------------------

