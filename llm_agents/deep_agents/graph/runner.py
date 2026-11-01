"""llm_agents.deep_agents.graph.runner — the thread runner as a REAL LangGraph
graph whose NODE CHAIN IS THE PLAN: plan → (steps as nodes) → finalize →
result.

One run of the deep agent is one THREAD (a uuid-hex ``thread_id``): the
LangGraph config's thread_id (the checkpointer keys the run's state
snapshots by it) AND the parent id on every ``text.deep_agent_states``
row (DDL ``database/sql/text/09_deep_agent_states.sql`` — the concept
mapping table is in that file's header).

The graph (``build_thread_graph``) — the plan node WRITES THE PLAN FOR
LANGGRAPH: each planned research step executes as its OWN node hop (one
superstep, one LLM conversation), the chain walked by a conditional
router that re-reads the living plan tree between hops:

    START → plan → next? ─┬→ step ─→ next? ─┬→ step ─→ … → finalize → result → END
                          │(no plan)        │(no pending step)
                          └→ execute ───────┴→ finalize
                          (step lane, context approaching the limit:
                           step → compact → step — see compact below)

  plan      — the HARDCODED first node of EVERY run: the model studies
              the BUSINESS INPUT (scope subject, focus question,
              selected skills, analysis items — rendered as a compact
              business-framing block beside the raw structured context)
              and lays out research steps that reflect what the ask
              wants, as the states JSON ``{"steps": [{"node", "title",
              "notes", "delegate": {"goal"}}]}``. The proposal is an
              INDEPENDENT session (:class:`planning.planner.Planner`):
              ANY rejected reply — bad JSON, missing titles, ops-named
              steps — goes BACK to the model with the specific
              complaint, until the plan compiles into accepted thread
              states or the correction rounds run out (then the run
              degrades). A ``delegate`` goal marks a FORK step.
  compact   — the CONTEXT-COMPACTION node (``graph.compaction``):
              routed in by the step lane when the accumulated findings
              approach the LLM context limit — ONE LLM call merges the
              prior digest + the raw findings tail into a fresh
              bounded digest (``findings_digest``), clears the merged
              raw lines, and slots a visible ``compact`` state row into
              the plan tree right after the last settled step. Every
              later hop carries digest + new tail; when the tail again
              approaches the limit the NEXT compact node folds it into
              the digest — finished steps MERGE into the latest
              compacted node instead of aging away to title lines.
              Fail-soft: a failed compaction keeps the raw findings
              (the render aging stays the never-fail backstop) and the
              chain advances.
  step      — ONE planned research lane per hop (the same node name
              loops; each hop is its own superstep with its own
              checkpoint and its OWN LLM context — one node one context
              now means one conversation per research step). The hop:
              tick the state in_progress (RUNNER-side — ticking is
              automatic bookkeeping, never a model tool), then
              * an INLINE step — a fresh DeepAgent conversation whose
                user message carries the run goal + structured context
                + the selected prompts' rendered texts + the step's
                directive + the FINDINGS of every earlier step (the
                per-step conversations meet only through this
                accumulated evidence), with MCP/sandbox tools,
                ``update_plan`` (rework the not-yet-run part of the
                plan — the living-graph mutation), and
                spawn/collect_subagents for parallel forks of its own;
              * a DELEGATED (fork) step — NO main conversation at all:
                ``agent.run_delegation(goal)`` runs the subagent under
                the full supervision policy and its answer becomes the
                step's finding. THE PARALLEL WAVE: CONSECUTIVE
                delegated-or-parallel steps run as ONE concurrent wave
                — a delegated goal is fixed at plan time, before any
                step ran, so sibling forks cannot depend on each
                other's findings, and a parallel-marked inline step
                carries the planner's explicit independence assertion
                (an unmarked inline step is the dependency barrier: the
                wave stops there). Wave members run concurrently under
                ``MAX_PARALLEL_DELEGATED_STEPS`` slots (a delegated
                member runs its fork engine, an inline member its own
                conversation, their ``update_plan`` writes serialized
                by one lock); each settles its own row and the findings
                append in ordinal order, so the plan reads exactly as
                it would have serially. THE FORK PASS-DOWN: every child
                this hop spawns (delegated engine or inline fork, and
                every sub-sub child below) receives the run's business
                context — goal + structured context + the selected
                prompts' rendered texts — stamped on its request's
                structured data, so a fork works under the prompts the
                user picked, not just the goal text;
              then tick completed (finding as the result note) or
              failed, append the finding(s) to the state, and compute
              ``next`` from the FRESH plan tree (replans — adds,
              removes, revisions from any step or subagent — take
              effect on the very next hop: the graph changes as
              ongoing nodes find things).
  execute   — the DEGRADED lane (plan persistence failed): the old
              one-conversation node, no plan tools — the pre-thread
              behavior as the fail-soft backstop.
  finalize  — settle whatever the run ended with (in_progress → failed,
              pending → skipped) and snapshot the thread.
  result    — the HARDCODED final-result node, the graph's LAST node:
              its fixed output is ONE context row of kind final-result
              (the run's answer document — the LAST completed step's
              finding, normally the plan's final_view synthesis step —
              or the failure reason). No LLM.

The MAIN THREAD vs FORKED PATHS contract (plan_tools is authoritative):
each step node's conversation is a MAIN authority — it may rework any
NOT-YET-RUN node and TERMINATE running forks; a forked subagent's
writes are structurally confined to its own subtree (its minted ids +
adds under its fork state), so concurrent forks can never clobber each
other or the main lane. Below the spawn-depth cap a forked subagent
may itself fork sub-sub agents (spawn_subagent): their child states
land under ITS fork state — nested one level deeper in the same tree,
supervised by it, their final answers gathered by it before it
concludes (the parent reads its immediate children's outputs; deeper
levels roll up through them).

ERROR-AS-STATE ROUTING: a step hop failure is caught, the step's state
row settles failed, ``run_error`` flows through the state and the chain
ADVANCES (the router picks the next pending step — one failed research
step must not discard the rest); finalize still settles the thread.
The ORIGINAL exception rides the run context and ``run_thread``
re-raises it after the graph completes — EXCEPT step failures, which
the run survives by design (answer = what the remaining steps found).
A failure of the graph ENGINE itself (outside the nodes) propagates
raw, and the thread stays unsettled — outside the old plain-run
contract too.

CHECKPOINTER + STORE: the graph compiles with BOTH LangGraph
persistence primitives. AsyncPostgresSaver (langgraph-checkpoint-
postgres) keys per-superstep state snapshots by the thread_id — the
interrupt/resume groundwork (its state values are all plain, so
checkpoints carry no custom msgpack types; its serializer keeps an
EXPLICIT allowlist via ``_checkpoint_serde`` so LEGACY checkpoints —
written when the plan dataclasses still rode the state — deserialize
typed when LangGraph's coming strict-msgpack default lands);
beside it, the runner's OWN BaseStore
implementation (``state_store.DeepAgentStore`` over SQL-first
``text.deep_agent_store_items``) persists the runner-lane NODE STATES
(plan / each step execution / finalize / result lifecycle — a step
item keys ``node:step-<state_id>`` and carries the state row's id) and
the LLM-context REGISTRY — every context referenced by id (its node,
its langgraph checkpoint id, its call counts) with the context DATA
(messages, prompts, tool transcripts) never persisted anywhere. ONE
NODE, ONE LLM CONTEXT: each node execution mints exactly one context
id (``NodeRunWriter``) — each step hop mints its own, each spawned
subagent mints its own (and its fork's added nodes stamp the
subagent's), the delegated step hop itself carries none (its child
owns the work), finalize (no LLM) carries none. Each context's
REQUESTS are linkable though: the node binds its context id as the
request log's chain (``bind_request_chain`` — subagents rebind their
own inside their task), so every call lands in ``text.llm_requests``
with ``chain_id`` = the context id. The persisted ThreadState carries
counts only (``n_tool_calls``; findings are bounded plain strings) —
the tool-call transcript rides the run context side channel, never a
checkpoint. Both primitives fail-soft, the request_log never-block
contract: when either cannot initialize, the graph runs without it and
the run proceeds — persistence may never fail the summary.

PERSISTENCE POLICY for the thread tables — fail-soft too: every
plan_store call is wrapped inside the nodes; a DB outage (or a
malformed plan) degrades the run to the pre-thread behavior (the plain
execute lane, no plan rows) and logs a warning. The thread IDENTITY
link (text.deep_agent_identities — the classification-nav scope pick
<-> thread_id) and the standing system prompt resolution
(text.ai_prompts prompt_type 0) are the same fail-soft rung, resolved
at RUN START in ``run_thread``.

PROMPT TEMPLATES: the plan prompts (prompt_type 2) and the standing
system prompt (the DB row, the caller's ``system``, or the built-in
default) render as DYNAMIC TEMPLATES before use — ``{{key}}``
placeholders fill from the run's input args (``sec_type`` / ``code`` /
``name`` / ``question``, template_args); known keys always substitute
(empty when the run carries no scope), unknown keys stay literal.

STATE SERIALIZABILITY: every ``ThreadState`` value is PLAIN JSON-ish —
no custom type rides a checkpoint. The plan itself never enters the
state: the plan_store DB rows own the living tree and every hop
re-reads it fresh (a checkpointed plan copy would go stale the moment
update_plan mutates the tree), and the settled snapshot finalize
produces is re-read from the plan_store by ``run_thread`` after the
graph completes. The live agent objects (provider
/ MCP client / asyncio tasks) are closed over by the nodes, never
carried in state. The tool-call TRANSCRIPT is state-external too: step
hops return counts (``n_tool_calls``) and the run context carries the
transcripts, so checkpoints and store items stay STATES — LLM context
data is referenced by id, never persisted.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict
from urllib.parse import quote

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph
from langgraph.store.base import BaseStore

from llm_agents._core.request_log import (
    bind_request_chain, end_request_chain,
)
from llm_agents.deep_agents.core.loop import (
    DEFAULT_SYSTEM_PROMPT,
    AgentResult,
    DeepAgent,
)
from llm_agents.deep_agents.core.mcp import McpToolClient
from llm_agents.deep_agents.core.provider import ZhipuChatProvider
from llm_agents.deep_agents.core.sandbox import Sandbox
from llm_agents.deep_agents.graph.hooks import ForkBroker, subagent_hooks
from llm_agents.deep_agents.graph.compaction import compact_findings, should_compact
from llm_agents.deep_agents.graph.prompts import (
    STEP_SYSTEM_ADDENDUM,
    finding_line,
    step_user_message,
)
from llm_agents.deep_agents.graph.state_store import (
    NODE_COMPACT, NODE_EXECUTE, entry_checkpoint_id, NODE_FINALIZE, NODE_PLAN,
    NODE_RESULT, DeepAgentStore, NodeRunWriter, settle_node_items,
)
from llm_agents.deep_agents.planning.authorities import main_plan_tools
from llm_agents.deep_agents.planning.plan_json import PLAN_SYSTEM_PROMPT
from llm_agents.deep_agents.planning.planner import (
    Planner, render_template, template_args,
)
from llm_agents.deep_agents.planning.store import (
    AGENT_PROMPT_FORK_PLAN_ADDENDUM, AGENT_PROMPT_FORK_SPAWN_ADDENDUM,
    AGENT_PROMPT_PLAN_SYSTEM, AGENT_PROMPT_STEP_ADDENDUM,
    AGENT_PROMPT_SUBAGENT_SYSTEM, PlannedStep, StateRow, ThreadPlan,
    add_plan_states, begin_thread, create_thread, fetch_agent_prompts,
    fetch_plan_prompts, fetch_step_delegate, fetch_system_prompt,
    finalize_thread, identity_from_scope, list_thread_states,
    record_final_result, record_thread_identity, settle_plan_state,
    stamp_states_context, tick_step,
)

logger = logging.getLogger("deep_agents.runner")

# The fork-wave width: how many CONSECUTIVE delegated steps may run at
# once in one step hop (the rest of a longer wave queue behind the
# slots). Matches the loop's default max_concurrent_subagents fan-out;
# every wave member is itself a supervised spawner, so this caps the
# hop's direct children — their own sub-forks stay under their own
# semaphores.
MAX_PARALLEL_DELEGATED_STEPS = 4


class ThreadState(TypedDict, total=False):
    """The graph's shared state — every value serializable (they ride
    the checkpointer's checkpoints). Nodes return partial updates;
    LangGraph merges per key (last write wins — no reducers needed for
    a linear graph)."""

    goal: str
    data: Optional[Dict[str, Any]]
    prompt_keys: List[str]
    # The selected plan prompts RENDERED (prompt_key -> instruction
    # text, {{key}} args substituted) — fetched once at run start and
    # passed down to every step hop's conversation and every forked
    # child's input (the study instructions the user picked ride the
    # whole run, not just the planning call). Plain dict of str, so it
    # rides the checkpointer fine.
    prompt_texts: Dict[str, str]
    # The run-start resolution of the agent's INTERNAL prompt rows
    # (text.ai_prompts prompt_type 3 — seeded from the inline constants
    # by 15_deep_agent_prompts.sql): prompt_key -> instruction. A key
    # missing here = that consumer's built-in constant (fail-soft
    # sourcing; the constants stay the fallback by design).
    agent_prompts: Dict[str, str]
    model: Optional[str]
    max_steps: int
    system: Optional[str]
    temperature: Optional[float]
    # False removes the sandbox_* builtin tools from the execute node's
    # agent (the sandbox is ON by default — the runner mints one scratch
    # directory per run when this is True).
    sandbox_enabled: bool
    # At INPUT: the run-level thread id (the checkpointer's key) the
    # plan node reuses for create_thread. After the plan node: the
    # persisted plan's thread id, or None (degraded / no plan prompts —
    # the response contract: no thread unless the plan phase persisted).
    thread_id: Optional[str]
    # The RUNNING PLANNING NODE's state row (begin_thread — the ordinal-0
    # 'plan' row that exists from run start; the plan node stamps its
    # context onto it and closes it). None when that insert degraded.
    plan_state_id: Optional[int]
    # The PLAN itself never rides the state: the plan_store DB rows own
    # the living tree and every hop re-reads it fresh — a checkpointed
    # plan copy would go stale the moment update_plan mutates the tree
    # (and custom types in checkpoints need msgpack allowlisting). All
    # state values stay plain JSON-ish; see STATE SERIALIZABILITY in the
    # module docstring.
    # Non-None when a node failed fatally (the degraded execute lane):
    # finalize settles the thread with this failure reason; the ORIGINAL
    # exception rides the run context and run_thread re-raises it after
    # the graph completes. A single step hop's failure does NOT set
    # this — the chain advances and the run survives on the remaining
    # steps' findings.
    run_error: Optional[str]
    answer: str
    steps: int
    # COUNTS only — the tool-call transcript is LLM context data: it
    # rides the run context side channel, never the persisted state
    # (checkpoints and store items stay states; contexts referenced).
    # Accumulated across step hops (each hop returns the running total).
    n_tool_calls: int
    # The per-step evidence the step conversations meet through: one
    # bounded plain string per completed/failed hop ("<ordinal>. <title>
    # — <finding>"). Plain strings keep the checkpointer happy.
    findings: List[str]
    # CONTEXT COMPACTION (graph.compaction): the compact node's merged
    # digest of the findings compacted so far (None until the first
    # compaction) and how many raw finding lines the digest covers. A
    # finished step hop still appends to ``findings`` — the tail rides
    # BESIDE the digest until the next compact node folds it in (the
    # merge cycle: finished nodes merge into the latest compacted node,
    # never silently dropped).
    findings_digest: Optional[str]
    n_compacted: int
    # The router's next hop ("step" | "execute" | "finalize") — each
    # node computes it from the FRESH plan tree as it finishes, so
    # living-plan mutations steer the very next hop.
    next: str
    # The result node's fixed output: the context id of its kind
    # final-result row (the run's answer document — what the UI's Result
    # view displays). None until the result node ran (or it recorded
    # nothing: no answer and no error).
    result_context_id: Optional[int]


@dataclass
class _RunContext:
    """Per-run side channel for the NON-persistable pieces: the ORIGINAL
    execute exception (an exception object is not checkpoint-serializable;
    the state carries its string, the context carries the thing to
    re-raise) and the full AgentResult (its tool_calls transcript must
    not ride the state — the state carries n_tool_calls only)."""

    error: Optional[BaseException] = None
    result: Optional[AgentResult] = None


@dataclass
class ThreadRunResult:
    """The agent's answer plus the thread it ran under.

    ``n_tool_calls`` is the RUN-LEVEL count (accumulated across the step
    lane's hops and their forks — the per-conversation transcripts ride
    the run context side channel and are empty on reconstruction);
    ``result.subagent_runs`` is likewise empty on reconstruction.
    ``plan`` is the settled row list re-read from the plan_store AFTER
    the graph completes — it never rode the checkpointed state."""

    result: AgentResult
    thread_id: Optional[str] = None
    plan: List[StateRow] = field(default_factory=list)
    n_tool_calls: int = 0


# ----------------------------------------------------------------------
# Plan-node helpers: prompt assembly
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Prompt templates — the plan prompts and the standing system prompt
# accept {{key}} placeholders filled from the run's input args (the
# classification-nav scope pick and the focus question). Known keys
# ALWAYS substitute (empty string when the run carries no scope — no
# literal braces reach the model); unknown keys stay literal so a typo
# is visible instead of silently swallowed.
# ----------------------------------------------------------------------

async def fetch_prompt_texts(
    prompt_keys: List[str],
    data: Optional[Dict[str, Any]],
) -> Dict[str, str]:
    """The run's selected plan prompts RENDERED — prompt_key ->
    instruction text with the run's ``{{key}}`` args substituted. The
    texts the planner planned under, reused as the pass-down every
    forked child and every step hop receives (the SAME study
    instructions at execution time, not just at planning time)."""
    if not prompt_keys:
        return {}
    prompts = await fetch_plan_prompts(prompt_keys)
    args = template_args(data)
    return {p.prompt_key: render_template(p.instruction, args)
            for p in prompts}


def _fork_biz_context(
    goal: str,
    data: Optional[Dict[str, Any]],
    prompt_texts: Dict[str, str],
) -> Dict[str, Any]:
    """The PARENT'S PASS-DOWN for every forked subagent: the run's
    structured context plus the run goal and the rendered selected
    prompts — what the fork's work runs under, stamped on
    ``SubagentRequest.data`` and rendered into the child's first user
    message beside its fork goal."""
    context: Dict[str, Any] = dict(data or {})
    if goal:
        context["goal"] = goal
    if prompt_texts:
        context["prompt_instructions"] = prompt_texts
    return context


async def _propose_and_persist_plan(
    chat_complete: Callable[..., Any],
    goal: str,
    data: Optional[Dict[str, Any]],
    prompt_keys: List[str],
    *,
    model: Optional[str],
    thread_id: str,
    agent_prompts: Optional[Dict[str, str]] = None,
    plan_state_id: Optional[int] = None,
    llm_context_id: Optional[str] = None,
) -> Tuple[Optional[ThreadPlan], int]:
    """The plan node's engine: propose + persist the plan through the
    independent proposal session (:class:`planning.planner.Planner`).
    The plan node is the HARDCODED first node of every run —
    *prompt_keys* (the request's plan prompts, prompt_type 2) are
    optional FLAVOR, not the gate: without them the built-in planner
    prompt lays the plan out from the goal + structured context (scope,
    skills, analysis items) alone. The session owns the
    propose → validate → correct round-trip (ANY rejection — bad JSON,
    missing titles, ops-named steps — goes back to the model with the
    specific complaint until the reply compiles into accepted thread
    states or the correction rounds run out).

    Returns (plan, llm_calls): the plan is None when the proposal never
    validated (PlannerError after the rounds) or persistence failed —
    logged, never raised (the run degrades to the plain execute lane);
    *llm_calls* is the session's LLM call count either way (the plan
    node's context metric)."""
    prompts: List[Any] = []
    if prompt_keys:
        try:
            prompts = await fetch_plan_prompts(prompt_keys)
        except Exception as exc:  # noqa: BLE001 — plan with the default
            logger.warning("plan prompts fetch failed, planning with the "
                           "built-in instruction: %s", exc)

    # The plan prompts are DYNAMIC TEMPLATES: their {{key}} placeholders
    # (sec_type / code / name / question) fill from the run's input —
    # the classification-nav scope pick and the focus question. The
    # planner's system prompt is the DB-managed internal row when one is
    # active (15_deep_agent_prompts.sql), else the built-in constant.
    args = template_args(data)
    planner = Planner(
        chat_complete, goal=goal, data=data,
        plan_instructions=[
            (p.prompt_key, render_template(p.instruction, args))
            for p in prompts],
        model=model,
        system_prompt=((agent_prompts or {}).get(AGENT_PROMPT_PLAN_SYSTEM)
                       or PLAN_SYSTEM_PROMPT),
    )
    steps: Optional[List[PlannedStep]] = None
    try:
        steps = await planner.propose()
    except Exception as exc:  # noqa: BLE001 — a malformed plan is not fatal
        logger.warning("plan proposal failed (%s), running without a "
                       "plan", exc)

    plan: Optional[ThreadPlan] = None
    if steps is not None:
        plan_payload = {
            "goal": goal,
            # Which prompt texts the plan was laid out under — prompt_key +
            # the version that was current at run time (provenance: an edited
            # prompt bumps its version, so the payload says which revision
            # drove this run's plan).
            "plan_prompts": [
                {"prompt_key": p.prompt_key, "version": p.version}
                for p in prompts
            ],
            "steps": [
                {
                    "node": s.node, "title": s.title, "notes": s.notes,
                    # The fork goal rides the plan JSON payload verbatim —
                    # the audit trail of what the planner delegated.
                    **({"delegate": {"goal": s.delegate}}
                       if s.delegate else {}),
                }
                for s in steps
            ],
        }
        try:
            plan = await create_thread(
                thread_id, plan_payload, steps,
                plan_state_id=plan_state_id, llm_context_id=llm_context_id)
        except Exception as exc:  # noqa: BLE001 — unpersisted plan = no plan
            logger.warning("plan persistence failed (%s), running without "
                           "a plan", exc)
    return plan, planner.calls


def build_thread_graph(
    provider: ZhipuChatProvider,
    mcp: McpToolClient,
    run_ctx: _RunContext,
    *,
    checkpointer: Any = None,
    store: Optional[BaseStore] = None,
):
    """Compile the plan → (steps as nodes | compact) → finalize → result
    StateGraph with BOTH persistence primitives (the checkpointer's
    per-superstep snapshots and the deep-agent store's node states /
    context registry). The step lane is ONE looping node — each hop is
    its own superstep executing ONE planned step, so the plan the plan
    node writes literally is the node chain LangGraph walks, and the
    router between hops re-reads the living plan tree (replans steer
    the next hop; when the accumulated findings approach the LLM
    context limit the router detours through the compact node first).
    The live agent objects are closed over by the nodes
    (never carried in state — see the STATE SERIALIZABILITY note in the
    module docstring)."""
    # ONE sandbox session per RUN shared by every step hop (the sandbox
    # mints a remote session on its first tool call; without this cell
    # each step would start an empty one and lose the earlier steps'
    # files).
    sandbox_cell: List[Any] = [None]

    def run_sandbox(state: ThreadState) -> Optional["Sandbox"]:
        if sandbox_cell[0] is None and state.get("sandbox_enabled", True):
            sandbox_cell[0] = Sandbox()
        return sandbox_cell[0]

    async def plan_node(state: ThreadState) -> Dict[str, Any]:
        thread_id = state.get("thread_id") or ""
        writer = NodeRunWriter(store, thread_id, NODE_PLAN,
                               model=state.get("model"))
        await writer.begin(entry_checkpoint_id())
        plan_state_id = state.get("plan_state_id")
        # The running planning node's row (begin_thread) points at THIS
        # node's one context — its proposal requests join the plan task's
        # LLM-request view like any step's.
        if plan_state_id is not None and writer.context_id is not None:
            try:
                await stamp_states_context(
                    [int(plan_state_id)], writer.context_id)
            except Exception as exc:  # noqa: BLE001 — bookkeeping
                logger.warning("planning-node context stamp failed: %s", exc)
        # This node's request-log chain: its LLM calls (proposal +
        # correction rounds) record under chain_id = the context id, so
        # the UI's per-node/per-task request view finds them. The same
        # id keys the plan's context entries (one node, one chained
        # context — the payloads link the conversation's requests).
        with bind_request_chain(writer.context_id):
            plan, n_calls = await _propose_and_persist_plan(
                provider.chat_complete, state.get("goal", ""),
                state.get("data"),
                list(state.get("prompt_keys") or []),
                model=state.get("model"),
                thread_id=thread_id or uuid.uuid4().hex,
                agent_prompts=dict(state.get("agent_prompts") or {}),
                plan_state_id=plan_state_id,
                llm_context_id=writer.context_id,
            )
        end_request_chain(writer.context_id)
        n_states = len(plan.states) if plan is not None else 0
        # A proposal that never validated after spending LLM calls =
        # failed (the run degrades to the plain execute lane, it does
        # not die — the same fail-soft contract as plan_store).
        status = ("completed" if plan is not None
                  else "failed" if n_calls else "skipped")
        await writer.finish(status, n_llm_calls=n_calls,
                            n_states=n_states)
        # Close the RUNNING PLANNING NODE's row (begin_thread's — the
        # record the run started with): completed with the step count,
        # failed with the reason. Must land BEFORE the step lane reads
        # the tree (its runnable filter would otherwise pick the
        # in_progress planning row up as a step); fail-soft — the
        # blanket finalize/stop settle owns the row when this cannot.
        if plan_state_id is not None:
            try:
                await settle_plan_state(
                    thread_id,
                    "completed" if plan is not None else "failed",
                    note=(f"laid out {n_states} steps"
                          if plan is not None else None),
                    error=(None if plan is not None
                           else "plan did not persist (proposal or "
                                "persistence failed)"),
                    llm_context_id=writer.context_id,
                )
            except Exception as exc:  # noqa: BLE001 — finalize settles it
                logger.warning("planning-node settle failed: %s", exc)
        # Only the routing facts cross into the checkpointed state — the
        # plan object itself stays local (the plan_store owns the living
        # tree; every hop re-reads it fresh).
        return {"thread_id": plan.thread_id if plan is not None else None,
                # The first route: the step lane when the plan
                # persisted, the degraded execute lane otherwise.
                "next": "step" if plan is not None else "execute"}

    async def step_node(state: ThreadState) -> Dict[str, Any]:
        """ONE planned research lane per hop: tick in_progress
        (runner-side), run the step's engine (a fresh conversation for
        an inline step; a supervised subagent for a delegated one), settle
        the row with the finding, append the finding to the state, and
        route by the fresh plan tree. A hop failure settles the step
        failed and the chain ADVANCES — one bad research step never
        discards the run.

        THE PARALLEL WAVE: when the head of the runnable lane is
        delegated or parallel-marked, every CONSECUTIVE eligible step
        joins one parallel wave — sibling fork goals are fixed at plan
        time and parallel marks carry the planner's independence
        assertion, so neither can depend on sibling findings; the wave
        stops at the first unmarked inline step (the dependency
        barrier) or a delegate read that fails. Members run
        concurrently under MAX_PARALLEL_DELEGATED_STEPS slots (inline
        members' update_plan writes serialized by one lock); findings
        append in ORDINAL order whatever the completion order, so the
        lane reads exactly as it would have serially."""
        thread_id = state.get("thread_id") or ""
        try:
            tree = await list_thread_states(thread_id)
        except Exception as exc:  # noqa: BLE001 — cannot even read the plan
            logger.warning("step lane: plan read failed: %s", exc)
            return {"next": "finalize",
                    "run_error": f"plan read failed: {exc}"}
        runnable = sorted(
            (s for s in tree
             if s.status in ("pending", "in_progress")),
            key=lambda s: (s.ordinal, s.state_id))
        if not runnable:
            return {"next": "finalize"}
        step = runnable[0]
        common = dict(
            model=state.get("model"),
            # Per-conversation cap: generous since every request now
            # carries the provider's per-request token ceiling (the
            # runaway guard lives THERE — core.provider — not in step
            # counts). The run-level bound is the plan itself (≤
            # MAX_PLAN_STEPS hops); this clamp only keeps one
            # conversation from eating the whole run (live runs showed
            # 12 starving delegated data pulls; 20 still bit deep
            # studies).
            max_steps=max(4, min(int(state.get("max_steps") or 12), 32)),
            temperature=state.get("temperature"),
        )

        async def _fetch_delegate(
                state_row: StateRow) -> Tuple[Optional[str], bool]:
            try:
                return await fetch_step_delegate(state_row.state_id)
            except Exception as exc:  # noqa: BLE001 — inline fallback
                logger.warning("delegate fetch failed for step %s: %s",
                               state_row.state_id, exc)
                return None, False

        findings = list(state.get("findings") or [])
        n_tool_calls = int(state.get("n_tool_calls") or 0)
        n_llm_calls = int(state.get("steps") or 0)
        # The fork pass-down: the run's business context + the selected
        # prompts' rendered texts — every child this hop spawns (and
        # every sub-sub child below it) receives it beside its goal.
        prompt_texts = dict(state.get("prompt_texts") or {})
        biz_context = _fork_biz_context(
            state.get("goal", ""), state.get("data"), prompt_texts)
        # The internal prompt rows resolved at run start (missing key =
        # the built-in constant, resolved at each use below).
        agent_prompts = dict(state.get("agent_prompts") or {})

        # ------------------------------------------------------------------
        # One delegated step's execution — the wave member and (wave of
        # one) the plain delegated hop. Never raises: every failure
        # settles this member's row failed and returns its finding, so
        # one bad fork never takes the wave (or the lane) down.
        # ------------------------------------------------------------------
        async def _delegated_hop(step_row: StateRow,
                                 delegate: str) -> Tuple[str, str, int, int]:
            writer = NodeRunWriter(
                store, thread_id, f"step-{step_row.state_id}",
                model=state.get("model"), llm=False,
                state_id=step_row.state_id)
            await writer.begin(entry_checkpoint_id())
            try:
                await tick_step(step_row.state_id, "in_progress",
                                thread_id=thread_id, node=step_row.node,
                                llm_context_id=writer.context_id)
            except Exception as exc:  # noqa: BLE001 — bookkeeping
                logger.warning("step in_progress tick failed (%s): %s",
                               step_row.state_id, exc)
            # The fork pass-down: the run's business context + the
            # selected prompts' rendered texts — every child this hop
            # spawns (and every sub-sub child below it) receives it
            # beside its goal.
            broker = ForkBroker(thread_id=thread_id)
            try:
                # A fork step: NO main conversation — the subagent IS
                # the engine. The step hop carries no context of its
                # own; the child mints its own (registry + requests all
                # hang off the child's id).
                input_hook, output_hook, run_wrapper = subagent_hooks(
                    thread_id, step_row.state_id,
                    store=store, parent_context_id=None,
                    model=state.get("model"),
                    state_by_run=broker.state_by_run,
                    context_by_run=broker.context_by_run,
                    biz_context=biz_context,
                    agent_prompts=agent_prompts)
                agent = DeepAgent(
                    provider, mcp,
                    allow_subagents=True,
                    sandbox=run_sandbox(state),
                    subagent_input_hook=input_hook,
                    subagent_output_hook=output_hook,
                    subagent_run_wrapper=run_wrapper,
                    **common)
                broker.agent = agent
                finding = await agent.run_delegation(
                    delegate, max_steps=common["max_steps"])
                child = (agent.subagent_runs() or [None])[-1]
                status = ("completed" if child is not None
                          and child.status == "completed" else "failed")
                n_llm = child.steps if child is not None else 0
                n_tool = child.tool_call_count if child is not None else 0
                await writer.finish(
                    status, n_states=1,
                    error=None if status == "completed"
                    else f"delegation failed: "
                         f"{child.error if child is not None else '?'}")
            except Exception as exc:  # noqa: BLE001 — advance, don't die
                logger.warning("step %s (%s) failed, the chain advances: %s",
                               step_row.state_id, step_row.title, exc)
                finding = f"STEP FAILED — {type(exc).__name__}: {exc}"
                status = "failed"
                n_llm = n_tool = 0
                await writer.finish("failed",
                                    error=f"{type(exc).__name__}: {exc}",
                                    n_states=1)
            finally:
                end_request_chain(writer.context_id)
            try:
                await tick_step(step_row.state_id, status,
                                thread_id=thread_id,
                                node=step_row.node,
                                llm_context_id=writer.context_id,
                                note=" ".join(finding.split())[:500] or None)
            except Exception as exc:  # noqa: BLE001 — finalize settles it
                logger.warning("step settle tick failed (%s): %s",
                               step_row.state_id, exc)
            return status, finding, n_llm, n_tool

        # ------------------------------------------------------------------
        # One INLINE step's execution — its OWN conversation (the
        # standing system prompt + the step contract; the run goal +
        # structured context + the selected prompts' texts + step
        # directive + accumulated findings as the user message). The
        # wave member for a parallel-marked step and (wave of one) the
        # plain sequential hop. *plan_lock* serializes concurrent wave
        # members' update_plan writes (None on the sequential lane).
        # Never raises — same contract as _delegated_hop.
        # ------------------------------------------------------------------
        async def _inline_hop(
                step_row: StateRow,
                plan_lock: Optional[asyncio.Lock],
        ) -> Tuple[str, str, int, int]:
            writer = NodeRunWriter(
                store, thread_id, f"step-{step_row.state_id}",
                model=state.get("model"), llm=True,
                state_id=step_row.state_id)
            await writer.begin(entry_checkpoint_id())
            try:
                await tick_step(step_row.state_id, "in_progress",
                                thread_id=thread_id, node=step_row.node,
                                llm_context_id=writer.context_id)
            except Exception as exc:  # noqa: BLE001 — bookkeeping
                logger.warning("step in_progress tick failed (%s): %s",
                               step_row.state_id, exc)
            try:
                system_args = template_args(state.get("data"))
                base = render_template(
                    state.get("system") or DEFAULT_SYSTEM_PROMPT,
                    system_args)
                step_addendum = (agent_prompts.get(AGENT_PROMPT_STEP_ADDENDUM)
                                 or STEP_SYSTEM_ADDENDUM)
                broker = ForkBroker(thread_id=thread_id)
                input_hook, output_hook, run_wrapper = subagent_hooks(
                    thread_id, step_row.state_id,
                    store=store, parent_context_id=writer.context_id,
                    model=state.get("model"),
                    state_by_run=broker.state_by_run,
                    context_by_run=broker.context_by_run,
                    biz_context=biz_context,
                    agent_prompts=agent_prompts)
                agent = DeepAgent(
                    provider, mcp,
                    system=f"{base}\n\n{step_addendum}",
                    builtin_tools=main_plan_tools(
                        thread_id, writer.context_id, broker.cancel,
                        plan_lock),
                    sandbox=run_sandbox(state),
                    subagent_input_hook=input_hook,
                    subagent_output_hook=output_hook,
                    subagent_run_wrapper=run_wrapper,
                    **common)
                broker.agent = agent
                # The step row points at ITS hop's conversation (the
                # per-task LLM-request view joins on it); a delegated
                # step keeps NULL — its child row carries the context.
                if writer.context_id is not None:
                    try:
                        await stamp_states_context(
                            [step_row.state_id], writer.context_id)
                    except Exception as exc:  # noqa: BLE001 — bookkeeping
                        logger.warning("step context stamp failed: %s", exc)
                with bind_request_chain(writer.context_id):
                    result = await agent.run(step_user_message(
                        state.get("goal", ""), state.get("data"),
                        step_row, None, findings, prompt_texts,
                        digest=state.get("findings_digest"),
                        n_compacted=int(state.get("n_compacted") or 0)))
                finding = result.answer
                status = "completed"
                n_llm = result.steps
                n_tool = len(result.tool_calls)
                await writer.finish(
                    "completed", n_llm_calls=result.steps,
                    n_tool_calls=len(result.tool_calls), n_states=1,
                    answer_chars=len(result.answer))
            except Exception as exc:  # noqa: BLE001 — advance, don't die
                logger.warning("step %s (%s) failed, the chain advances: %s",
                               step_row.state_id, step_row.title, exc)
                finding = f"STEP FAILED — {type(exc).__name__}: {exc}"
                status = "failed"
                n_llm = n_tool = 0
                await writer.finish("failed",
                                    error=f"{type(exc).__name__}: {exc}",
                                    n_states=1)
            finally:
                end_request_chain(writer.context_id)
            try:
                await tick_step(step_row.state_id, status,
                                thread_id=thread_id,
                                node=step_row.node,
                                llm_context_id=writer.context_id,
                                note=" ".join(finding.split())[:500] or None)
            except Exception as exc:  # noqa: BLE001 — finalize settles it
                logger.warning("step settle tick failed (%s): %s",
                               step_row.state_id, exc)
            return status, finding, n_llm, n_tool

        head_delegate, head_parallel = await _fetch_delegate(step)
        # Wave eligibility: a DELEGATED step (its goal is fixed at plan
        # time — it cannot depend on sibling findings) or an explicitly
        # parallel-marked INLINE step (the planner asserted
        # independence). An unmarked inline step is the dependency
        # barrier: it may need every earlier finding, so the wave stops.
        async def _wave_flags(
                row: StateRow) -> Tuple[Optional[str], bool]:
            if row.state_id == step.state_id:
                return head_delegate, head_parallel
            return await _fetch_delegate(row)

        if head_delegate is not None or head_parallel:
            # THE PARALLEL WAVE: collect the maximal run of CONSECUTIVE
            # delegated-or-parallel runnable steps, then execute them
            # concurrently under the wave slots — a delegated member
            # runs its fork engine, an inline member its own
            # conversation (their update_plan writes serialized by one
            # lock). Findings append in ordinal order after the gather,
            # so the evidence lane (and the answer pick below) is
            # exactly the serial lane's.
            wave: List[StateRow] = [step]
            delegates: Dict[int, Optional[str]] = {
                step.state_id: head_delegate}
            for candidate in runnable[1:]:
                goal, parallel = await _wave_flags(candidate)
                if goal is None and not parallel:
                    break
                wave.append(candidate)
                delegates[candidate.state_id] = goal
            if len(wave) > 1:
                logger.info("parallel wave: %d step(s) %s run concurrently",
                            len(wave), [w.ordinal for w in wave])
            wave_slots = asyncio.Semaphore(MAX_PARALLEL_DELEGATED_STEPS)
            plan_lock: Optional[asyncio.Lock] = (
                asyncio.Lock() if any(
                    delegates[m.state_id] is None for m in wave) else None)

            async def _wave_member(
                    member: StateRow) -> Tuple[str, str, int, int]:
                async with wave_slots:
                    goal = delegates[member.state_id]
                    if goal is not None:
                        return await _delegated_hop(member, goal)
                    return await _inline_hop(member, plan_lock)

            outcomes = await asyncio.gather(
                *(_wave_member(member) for member in wave),
                return_exceptions=True)
            # A member that still raised (nothing in the hops should)
            # becomes its own failed result — the wave's other members and
            # the lane's advance never depend on one broken step.
            settled: List[Tuple[str, str, int, int]] = []
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    if isinstance(outcome, asyncio.CancelledError):
                        raise outcome
                    logger.warning("parallel wave member raised: %s", outcome)
                    settled.append(
                        ("failed",
                         f"STEP FAILED — {type(outcome).__name__}: {outcome}",
                         0, 0))
                else:
                    settled.append(outcome)
            answer_finding: Optional[str] = None
            for member, (m_status, m_finding, m_llm, m_tool) in zip(
                    wave, settled):
                findings.append(finding_line(member.ordinal, member.title,
                                             m_finding))
                n_llm_calls += m_llm
                n_tool_calls += m_tool
                if m_status == "completed":
                    # The run's answer is the LAST completed step's
                    # finding — within the wave, the highest-ordinal
                    # completed member (later hops override this).
                    answer_finding = m_finding
        else:
            # The plain sequential inline hop — no wave, no lock.
            status, finding, n_llm, n_tool = await _inline_hop(step, None)
            findings = findings + [finding_line(step.ordinal, step.title,
                                                finding)]
            n_llm_calls += n_llm
            n_tool_calls += n_tool
            answer_finding: Optional[str] = (
                finding if status == "completed" else None)

        update: Dict[str, Any] = {
            "findings": findings,
            "steps": n_llm_calls,
            "n_tool_calls": n_tool_calls,
        }
        # The run's answer is the LAST COMPLETED step's finding — a
        # failed tail step must not overwrite the best document the run
        # produced (its failure lives in its row and finding line).
        if answer_finding is not None:
            update["answer"] = " ".join(answer_finding.split())
        # The router's input: is anything still runnable in the FRESH
        # tree (this hop's own replan included)? — and does the NEXT
        # hop's context approach the LLM limit (the compact detour)?
        try:
            tree = await list_thread_states(thread_id)
            runnable = sorted(
                (s for s in tree
                 if s.status in ("pending", "in_progress")),
                key=lambda s: (s.ordinal, s.state_id))
            if not runnable:
                update["next"] = "finalize"
            else:
                nxt = runnable[0]
                update["next"] = (
                    "compact" if should_compact(
                        model=state.get("model"),
                        goal=state.get("goal", ""),
                        data=state.get("data"),
                        prompt_texts=prompt_texts,
                        digest=state.get("findings_digest"),
                        findings=findings,
                        directive=f"{nxt.ordinal}. {nxt.title}")
                    else "step")
        except Exception as exc:  # noqa: BLE001 — cannot re-read: stop
            logger.warning("step lane: plan re-read failed: %s", exc)
            update["next"] = "finalize"
        return update

    async def compact_node(state: ThreadState) -> Dict[str, Any]:
        """The CONTEXT-COMPACTION node — the step lane's detour when the
        accumulated findings approach the LLM context limit
        (``compaction.should_compact`` at the router): ONE LLM call
        merges the prior digest + the raw findings tail into a fresh
        bounded digest, the merged raw lines clear, and a visible
        ``compact`` state row slots into the plan tree right after the
        last settled step (pending rows shift up — the canvas chain
        shows the compaction where it ran). Fail-soft at every rung: a
        failed LLM call keeps the raw findings untouched (the render
        aging stays the never-fail backstop) and the chain advances."""
        thread_id = state.get("thread_id") or ""
        findings = [f for f in (state.get("findings") or []) if f]
        digest = state.get("findings_digest")
        try:
            tree = await list_thread_states(thread_id)
        except Exception as exc:  # noqa: BLE001 — cannot even read the plan
            logger.warning("compact node: plan read failed: %s", exc)
            return {"next": "finalize",
                    "run_error": f"plan read failed: {exc}"}
        pending = any(s.status in ("pending", "in_progress") for s in tree)
        if not findings and not digest:
            # Nothing to merge — the router only detours here with
            # evidence; route straight back at the lane.
            return {"next": "step" if pending else "finalize"}

        # The audit row: a top-level node 'compact' state right after
        # the last settled step. Fail-soft — the compaction itself must
        # never depend on the row landing.
        settled = [s.ordinal for s in tree
                   if s.status not in ("pending", "in_progress")]
        after_ordinal = max(settled) if settled else 0
        compact_state_id: Optional[int] = None
        compact_ordinal = after_ordinal + 1
        try:
            rows = await add_plan_states(
                thread_id,
                [PlannedStep(
                    node=NODE_COMPACT,
                    title="context compaction — merge findings "
                          "into the digest")],
                after_ordinal=after_ordinal)
            if rows:
                compact_state_id = rows[0].state_id
                compact_ordinal = rows[0].ordinal
        except Exception as exc:  # noqa: BLE001 — audit trail only
            logger.warning("compact state row insert failed: %s", exc)
        writer = NodeRunWriter(
            store, thread_id,
            f"{NODE_COMPACT}-{compact_ordinal}"
            if compact_state_id is not None else NODE_COMPACT,
            model=state.get("model"), state_id=compact_state_id)
        await writer.begin(entry_checkpoint_id())
        if compact_state_id is not None and writer.context_id is not None:
            try:
                await stamp_states_context(
                    [compact_state_id], writer.context_id)
            except Exception as exc:  # noqa: BLE001 — bookkeeping
                logger.warning("compact context stamp failed: %s", exc)
            try:
                await tick_step(compact_state_id, "in_progress",
                                thread_id=thread_id, node=NODE_COMPACT,
                                llm_context_id=writer.context_id)
            except Exception as exc:  # noqa: BLE001 — bookkeeping
                logger.warning("compact in_progress tick failed: %s", exc)

        n_compacted = int(state.get("n_compacted") or 0)
        new_digest: Optional[str] = None
        error: Optional[str] = None
        try:
            with bind_request_chain(writer.context_id):
                new_digest = await compact_findings(
                    provider.chat_complete,
                    goal=state.get("goal", ""),
                    digest=digest, findings=findings,
                    model=state.get("model"))
        except Exception as exc:  # noqa: BLE001 — keep the raw findings
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("compaction failed — raw findings kept (the "
                           "aging stays the backstop): %s", exc)
        finally:
            end_request_chain(writer.context_id)
        await writer.finish(
            "failed" if error else "completed",
            n_llm_calls=1, n_states=1 if compact_state_id else 0,
            error=error,
            answer_chars=len(new_digest or ""))
        if compact_state_id is not None:
            note = (f"merged {len(findings)} finding(s) into the digest "
                    f"({n_compacted} → {n_compacted + len(findings)} "
                    f"lines covered)" if error is None else error)
            try:
                await tick_step(
                    compact_state_id,
                    "failed" if error else "completed",
                    thread_id=thread_id, node=NODE_COMPACT,
                    llm_context_id=writer.context_id, note=note)
            except Exception as exc:  # noqa: BLE001 — finalize settles it
                logger.warning("compact settle tick failed: %s", exc)

        update: Dict[str, Any] = {
            "steps": int(state.get("steps") or 0) + 1,
        }
        if new_digest is not None:
            # THE MERGE: the raw lines are folded into the digest and
            # cleared; later hops carry digest + fresh tail until the
            # next compaction folds that tail in turn.
            update["findings_digest"] = new_digest
            update["findings"] = []
            update["n_compacted"] = n_compacted + len(findings)
        # Route by the FRESH tree — the lane continues where it left
        # off, now under the smaller evidence block.
        try:
            tree = await list_thread_states(thread_id)
            update["next"] = ("step" if any(
                s.status in ("pending", "in_progress") for s in tree)
                else "finalize")
        except Exception as exc:  # noqa: BLE001 — cannot re-read: stop
            logger.warning("compact node: plan re-read failed: %s", exc)
            update["next"] = "finalize"
        return update

    async def execute_node(state: ThreadState) -> Dict[str, Any]:
        """The DEGRADED lane — the plan never persisted (proposal or
        persistence failure): the old one-conversation node with no
        plan tools, the pre-thread fail-soft backstop."""
        thread_id = state.get("thread_id") or ""
        writer = NodeRunWriter(store, thread_id, NODE_EXECUTE,
                               model=state.get("model"))
        await writer.begin(entry_checkpoint_id())
        try:
            agent = DeepAgent(
                provider, mcp,
                system=render_template(
                    state.get("system") or DEFAULT_SYSTEM_PROMPT,
                    template_args(state.get("data"))),
                model=state.get("model"),
                max_steps=int(state.get("max_steps") or 12),
                temperature=state.get("temperature"),
                sandbox=(Sandbox() if state.get("sandbox_enabled", True)
                         else None))
            with bind_request_chain(writer.context_id):
                result = await agent.run(state.get("goal", ""),
                                         data=state.get("data"))
            run_ctx.result = result
            await writer.finish(
                "completed", n_llm_calls=result.steps,
                n_tool_calls=len(result.tool_calls),
                answer_chars=len(result.answer))
            return {"answer": result.answer, "steps": result.steps,
                    "n_tool_calls": len(result.tool_calls)}
        except Exception as exc:  # noqa: BLE001 — error-as-state routing
            run_ctx.error = exc
            await writer.finish("failed", error=f"{type(exc).__name__}: {exc}")
            return {"run_error": f"{type(exc).__name__}: {exc}"}
        finally:
            end_request_chain(writer.context_id)

    async def result_node(state: ThreadState) -> Dict[str, Any]:
        """The HARDCODED final-result node — never model-planned, always
        the graph's LAST node (after finalize settles the thread). Its
        output is FIXED: one context row of kind final-result carrying
        the run's answer document (the LAST completed step's finding —
        normally the plan's final_view synthesis step) or the failure
        reason, the
        payload the UI's Result view displays for the thread. A run
        cancelled mid-graph never reaches this node (no final-result
        row — the UI says the run left no recorded result)."""
        thread_id = state.get("thread_id")
        if not thread_id:
            return {}
        # No LLM work here — llm=False: no context, no registry item.
        writer = NodeRunWriter(store, thread_id, NODE_RESULT, llm=False)
        await writer.begin(entry_checkpoint_id())
        try:
            context_id = await record_final_result(
                thread_id,
                answer=state.get("answer") or None,
                error=state.get("run_error"),
            )
        except Exception as exc:  # noqa: BLE001 — fail-soft by contract
            logger.warning("final-result context persist failed: %s", exc)
            await writer.finish("failed", error=f"{type(exc).__name__}: {exc}")
            return {}
        await writer.finish(
            "completed" if context_id is not None else "skipped")
        return {"result_context_id": context_id}

    async def finalize_node(state: ThreadState) -> Dict[str, Any]:
        thread_id = state.get("thread_id")
        if not thread_id:
            return {}
        # No LLM work here — llm=False: no context, no registry item.
        writer = NodeRunWriter(store, thread_id, NODE_FINALIZE, llm=False)
        await writer.begin(entry_checkpoint_id())
        try:
            snapshot = await settle_thread(
                thread_id, failure=state.get("run_error"))
        except Exception as exc:  # noqa: BLE001 — best-effort settle
            logger.warning("thread finalize failed: %s", exc)
            await writer.finish("failed", error=f"{type(exc).__name__}: {exc}")
            return {}
        await writer.finish("completed", n_states=len(snapshot))
        # The settled snapshot never rides the state — run_thread
        # re-reads the plan_store after the graph completes (plain-value
        # checkpoints; a failed settle still leaves the rows readable
        # there, where the old state key would have gone empty).
        return {}

    def route_after_node(state: ThreadState) -> str:
        return str(state.get("next") or "finalize")

    builder = StateGraph(ThreadState)
    builder.add_node("plan", plan_node)
    builder.add_node("step", step_node)
    builder.add_node("compact", compact_node)
    builder.add_node("execute", execute_node)
    builder.add_node("finalize", finalize_node)
    builder.add_node("result", result_node)
    builder.add_edge(START, "plan")
    builder.add_conditional_edges(
        "plan", route_after_node,
        {"step": "step", "execute": "execute", "finalize": "finalize"})
    builder.add_conditional_edges(
        "step", route_after_node,
        {"step": "step", "compact": "compact", "finalize": "finalize"})
    builder.add_conditional_edges(
        "compact", route_after_node,
        {"step": "step", "finalize": "finalize"})
    builder.add_edge("execute", "finalize")
    builder.add_edge("finalize", "result")
    builder.add_edge("result", END)
    return builder.compile(checkpointer=checkpointer, store=store)


async def settle_thread(
    thread_id: str, *, failure: Optional[str] = None
) -> List[StateRow]:
    """Settle the thread's rows (in_progress → failed, pending → skipped)
    and return the settled plan-order snapshot. The finalize node runs
    this at the graph's end; the server's /stop path runs it directly
    when a run was cancelled mid-graph (the graph never reaches
    finalize then — the settle keeps the audit trail closed)."""
    await finalize_thread(thread_id, failure=failure)
    return await list_thread_states(thread_id)


def _checkpoint_conn_string() -> str:
    """The saver's conn string: the SAME database/.env params the
    db_commons helpers read, with search_path pinned to the dedicated
    ``langgraph`` schema so the saver's unqualified DDL/queries land
    there (never public)."""
    from _common.db_commons._helpers import _get_conn_params

    params = _get_conn_params()
    auth = f"{quote(str(params['user']), safe='')}:" \
           f"{quote(str(params['password']), safe='')}"
    options = quote("-csearch_path=langgraph", safe="")
    return (f"postgresql://{auth}@{params['host']}:{params['port']}"
            f"/{params['database']}?options={options}")


def _checkpoint_serde() -> JsonPlusSerializer:
    """The saver's serializer with an EXPLICIT msgpack allowlist for the
    plan_store dataclasses. Today's state values are all plain (no
    custom type rides a checkpoint), but LEGACY checkpoints — written
    before the plan objects left the state — carry
    ThreadPlan/PlannedStep/StateRow ext blobs; without the allowlist a
    future LangGraph strict-msgpack default would silently degrade them
    to raw dicts when an old thread resumes. The CLASSES (not
    dotted-path tuples) normalize to ``(defining module, name)`` keys,
    so the allowlist cannot drift from where the dataclasses live."""
    return JsonPlusSerializer(allowed_msgpack_modules=(
        PlannedStep, StateRow, ThreadPlan,
    ))


async def run_thread(
    provider: ZhipuChatProvider,
    mcp: McpToolClient,
    *,
    goal: str,
    data: Optional[Dict[str, Any]] = None,
    model: Optional[str] = None,
    max_steps: int = 12,
    system: Optional[str] = None,
    temperature: Optional[float] = None,
    enable_sandbox: bool = True,
    thread_id: Optional[str] = None,
) -> ThreadRunResult:
    """One thread: plan (ALWAYS — the hardcoded first node; the request's
    plan prompts are optional flavor) → execute → finalize → result, as
    one LangGraph graph invocation under a thread_id. Without a
    persisted plan (proposal/persistence failure — fail-soft) the run
    behaves as before and carries no thread. ``thread_id`` is
    caller-supplied when the caller needs to know it before the run
    ends (the server registers each run under it so /stop can cancel
    it); a None picks a fresh uuid-hex, the pre-stop contract.
    ``enable_sandbox`` mints the run's code-sandbox scratch directory
    (default on; False removes the sandbox_* tools for the run).

    Before the graph starts, ``begin_thread`` writes the run's RUNNING
    PLANNING NODE — the ordinal-0 'plan' state row that makes the run
    visible (summary history + thread read) from its first instant; the
    plan node closes it when the proposal lands (settle_plan_state)."""
    prompt_keys = [
        key for key in ((data or {}).get("prompts") or [])
        if isinstance(key, str) and key
    ]
    thread_id = thread_id or uuid.uuid4().hex
    run_ctx = _RunContext()
    saver_cm = None
    # The RUNNING PLANNING NODE — the run's record exists from THIS
    # moment: the summary history's row and a non-404 thread read while
    # the plan LLM call is still thinking (the UI opens the Generate
    # click's graph straight onto it; the /ws channel's thread_started
    # event lands in the same instants). The plan node closes the row
    # when the proposal lands. Fail-soft like every plan_store call.
    plan_state_id: Optional[int] = None
    try:
        plan_state_id = await begin_thread(thread_id)
    except Exception as exc:  # noqa: BLE001 — run starts unrecorded
        logger.warning("planning-node insert failed (run starts "
                       "unrecorded): %s", exc)
    # The classification-nav scope pick links to the thread id AT RUN
    # START (text.deep_agent_identities — every generate with a scope
    # sets the link, plan persisted or not); the standing system prompt
    # resolves here too: the caller's `system` wins, else the DB's
    # active system-type row (prompt_type 0), else the built-in default
    # — both DB/default texts render as templates in the execute node.
    # Both fail-soft: bookkeeping and prompt sourcing never fail a run.
    identity = identity_from_scope(data)
    if identity is not None:
        try:
            await record_thread_identity(thread_id, identity)
        except Exception as exc:  # noqa: BLE001 — the link is bookkeeping
            logger.warning("thread identity persist failed: %s", exc)
    if system is None:
        try:
            system = await fetch_system_prompt()
        except Exception as exc:  # noqa: BLE001 — degrade to the default
            logger.warning("system prompt fetch failed, using the "
                           "built-in default: %s", exc)
    # The agent's INTERNAL prompt rows (prompt_type 3 — seeded from the
    # inline constants by 15_deep_agent_prompts.sql) resolve ONCE at run
    # start; every composition point (planner system, step addendum,
    # forked child base + addenda) reads its key from this bundle and
    # falls back to the built-in constant when the key is missing.
    try:
        agent_prompts = await fetch_agent_prompts()
    except Exception as exc:  # noqa: BLE001 — the constants cover it
        logger.warning("agent prompt fetch failed, using the built-in "
                       "constants: %s", exc)
        agent_prompts = {}
    # The selected plan prompts RENDERED, once per run — the pass-down
    # every step hop's conversation and every forked child receives.
    # Fail-soft: without them the run plans under the built-in
    # instruction and the hops simply carry no prompt block.
    try:
        prompt_texts = await fetch_prompt_texts(prompt_keys, data)
    except Exception as exc:  # noqa: BLE001 — plan without prompt texts
        logger.warning("plan prompt texts fetch failed, running without "
                       "the pass-down block: %s", exc)
        prompt_texts = {}
    try:
        checkpointer = None
        try:
            saver_cm = AsyncPostgresSaver.from_conn_string(
                _checkpoint_conn_string(), serde=_checkpoint_serde())
            checkpointer = await saver_cm.__aenter__()
            await checkpointer.setup()
        except Exception as exc:  # noqa: BLE001 — degrade, never fail
            logger.warning("langgraph checkpointer unavailable — running "
                           "without checkpoints: %s", exc)
            if saver_cm is not None:
                with contextlib.suppress(Exception):
                    await saver_cm.__aexit__(None, None, None)
                saver_cm, checkpointer = None, None
        # The store beside the checkpointer: the deep-agent LangGraph
        # store (node states + context registry over SQL-first tables).
        # Its abatch opens a connection per batch, so construction
        # cannot fail here; every node write is fail-soft in the writer.
        store: Optional[BaseStore] = DeepAgentStore()

        graph = build_thread_graph(provider, mcp, run_ctx,
                                   checkpointer=checkpointer, store=store)
        # The step lane is one looping node — one superstep per planned
        # step hop — so the default recursion limit (25) cannot hold a
        # 12-step plan with replans. Bound it generously past the tree
        # cap (update_plan may grow it to authorities.MAX_TOTAL_STEPS =
        # 24; the runaway guard is the per-request token ceiling, not
        # this counter): plan + hops + replans + finalize + result.
        config: Dict[str, Any] = {"recursion_limit": 160}
        if checkpointer is not None:
            config["configurable"] = {"thread_id": thread_id}
        try:
            values = await graph.ainvoke(
                {
                    "goal": goal,
                    "data": data,
                    "prompt_keys": prompt_keys,
                    "prompt_texts": prompt_texts,
                    "agent_prompts": agent_prompts,
                    "model": model,
                    "max_steps": max_steps,
                    "system": system,
                    "temperature": temperature,
                    "sandbox_enabled": enable_sandbox,
                    "thread_id": thread_id,
                    "plan_state_id": plan_state_id,
                },
                config,
            )
        except asyncio.CancelledError:
            # The stop path (the server's /stop cancels the run task)
            # unwinds the graph mid-flight — finalize never runs, so the
            # runner-lane node items plan/execute wrote stay 'running'
            # forever and the UI's thread view shows a phantom live chip.
            # Close them here while the store is still in scope; the
            # STATE rows settle separately in the caller's stop handler
            # (runner.settle_thread).
            with contextlib.suppress(Exception):
                await settle_node_items(
                    store, thread_id,
                    failure="stopped by user (POST /stop)")
            raise
    except asyncio.CancelledError:
        # Same unwind as above (this catch covers the pre-ainvoke setup
        # window). The planning row stays in_progress on purpose: the
        # stop handler's settle_thread closes it as 'stopped by user'.
        raise
    except BaseException:
        # The graph ENGINE failed before/outside the nodes — nothing will
        # ever settle the running planning node row, and a row stuck
        # in_progress reads as a live run forever. Close it fail-soft,
        # then let the original failure propagate (the pre-begin_thread
        # contract).
        with contextlib.suppress(Exception):
            await settle_plan_state(
                thread_id, "failed",
                error="run failed before its plan node settled")
        raise
    finally:
        if saver_cm is not None:
            with contextlib.suppress(Exception):
                await saver_cm.__aexit__(None, None, None)

    # The execute failure (if any) settles in finalize first; the
    # ORIGINAL exception then propagates to the caller, same contract
    # as the pre-graph runner.
    if run_ctx.error is not None:
        raise run_ctx.error
    values = values or {}
    # The full AgentResult (with its tool_calls transcript) rode the run
    # context; the persisted state only ever carried the count.
    if run_ctx.result is not None:
        result = run_ctx.result
    else:
        result = AgentResult(
            answer=str(values.get("answer") or ""),
            steps=int(values.get("steps") or 0),
            tool_calls=[],
        )
    # The settled plan never rode the state (plain-value checkpoints):
    # re-read the rows finalize settled from the plan_store — the same
    # list_thread_states snapshot, fail-soft to none like every read.
    # Prefer the state's thread id (the plan node may have minted one
    # when the run started without).
    plan_rows: List[StateRow] = []
    settled_thread_id = str(values.get("thread_id") or thread_id or "")
    if settled_thread_id:
        try:
            plan_rows = await list_thread_states(settled_thread_id)
        except Exception as exc:  # noqa: BLE001 — result shape, not data
            logger.warning("settled plan re-read failed: %s", exc)
    return ThreadRunResult(
        result=result,
        thread_id=values.get("thread_id"),
        plan=plan_rows,
        n_tool_calls=(len(run_ctx.result.tool_calls)
                      if run_ctx.result is not None
                      else int(values.get("n_tool_calls") or 0)),
    )
