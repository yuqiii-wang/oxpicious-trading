"""llm_agents.deep_agents.server — FastAPI wrapper around DeepAgent.

Always-on HTTP bridge for the deep agent, the data_viz API's transport
for the analysis-summary generation (replaces the per-request CLI spawn:
the spawn paid a WSL cold start per ask and had to smuggle the payload
through a temp file because of the Windows ~32k command-line limit —
here the whole request IS one JSON body).

  POST /run   {goal, data?, model?, max_steps?, system?, temperature?,
               plan_template_id?}
              → one thread (runner.run_thread): the plan node proposes
                the plan JSON from the request's plan prompts
                (llm.ai_prompts prompt_type 2) and persists it as
                llm.deep_agent_states rows — UNLESS plan_template_id
                names a SAVED PLAN TEMPLATE (the Thread Graph Save
                button's placeholder-templated plan), in which case the
                plan materializes from the template with the run's
                input args, zero planning LLM calls; the execute node is the
                DeepAgent tool-calling loop over the read-only DB MCP
                daemon (127.0.0.1:8765/mcp), ticking each plan step via
                its update_plan_step tool; finalize settles the thread;
                the HARDCODED result node — the graph's last node — then
                persists the run's answer as the thread's kind
                final-result context (the UI Result view's display
                source).
              → {answer, steps, n_tool_calls, model, thread_id?, plan?,
                stopped?}.
              ``data`` is the caller's arbitrary JSON object; the agent
              sees it as a structured-context block on the user message.
              ``thread_id``/``plan`` are absent (null/[]) when the run
              had no plan prompts or persistence degraded. ``stopped``
              is true when the run was cancelled by POST /stop before
              it finished (empty answer, the plan snapshot settled with
              the stop).
              THE ``[this is test]`` SELF-TEST (2026-11-14): a goal
              starting with that prefix bypasses the provider config —
              the run loads the offline dummy LLM + the feature tool
              seam (selftest.DummySelfTestRun), walks the SHOWCASE
              chain (plan → trend → volume-with-resume → spawn step →
              fork step with its own sub node) MARKET-WIDE (the
              caller's code scope is dropped — no identity row, the
              history entry shows under every Summary History pick),
              and the ANSWER is the auto-run assertion battery's
              rendered PASS/FAIL report (the walked chains stay in the
              history for the Thread Graph; a mid-walk drift settles as
              a FAIL report, not a 500). BESIDE the main chain the
              self-test runs every shared ROUTER/TOOL-USAGE walk —
              spawn / fork / fork-drift / fork-sub / join / probe /
              background / reserve / cut / repeat / repair / syntax /
              the four resume variants — each its own chain kept in the
              history, its id listed in the response's
              ``walk_chain_ids``. The walk goal is the caller's text
              after the prefix (or the canonical 沪深300 dataset study)
              suffixed with the 【全场景自检】 marker that keys the
              showcase plan.
  POST /stop  {thread_id?} — cancel the in-flight run(s): the named
              thread, or every active run when thread_id is absent (the
              API bridge sends the absent form — its Stop button stops
              every active run; how many may run at once is the
              service_config max_parallel_runs cap, enforced bridge-side).
              The cancelled /run settles its thread (runner.settle_thread:
                in_progress → failed 'stopped by user', pending → skipped)
                and returns stopped: true. → {stopped, thread_ids}.
              THE CONFIRMED STOP (2026-11-13): the response resolves only
              AFTER the cancelled run(s) finished unwinding and settling
              (bounded by STOP_SETTLE_WAIT_SEC) — when it reaches the UI
              the chain rows are already terminal, so one refresh draws
              the settled state and the running node greys out exactly at
              the confirmed stop, never off the bare request.
  POST /threads/:thread_id/steps — EXTERNAL TOPOLOGY CHANGE: add steps
                to a RUNNING thread's main lane from outside the
                conversations (operator tooling / another service), body
                {add_steps: [{node, title, notes?, delegate_goal?}],
                after_ordinal?} — the same vocabulary and caps as the
                model's own update_plan adds, add-only and top-lane-only:
                history rows never move (later rows shift ordinals only),
                other paths' future nodes (pending steps, spawn subtrees)
                are kept, and the running graph executes the new nodes on
                its next hop (the router re-reads the living tree between
                hops). 409 when the thread is not running here (or its
                plan has not persisted yet), 400 with the actionable
                refusal on a validation/cap breach, 200 with the added
                rows. The inserts publish state_created per row — the
                UI's live canvas rerenders the new nodes instantly (the
                /ws chain).
  POST /threads/:thread_id/states/:state_id/spawn — the UI node action
                (2026-11-15, the Thread Graph's spawn button): one
                fresh-context child delegation under the clicked node of
                a SETTLED run, body {goal, max_steps?, provider?} — the
                SpawnHooks registration face (child_kind 0 row, pass-down,
                plan/stats/kg kits, the child's own hooks) and a full
                DeepAgent run as a background task; the response carries
                the child's state/context ids and the UI polls the
                detail. 409 on an active chain, 404 unknown state.
  POST /threads/:thread_id/nodes/:node/spawn — the RUN-NODE spawn (the
                failed-output-node request): the same action hanging off
                a RUNNER-LANE SEAT (the output node — NO state row), the
                canvas node key's s<k>: prefix resolving the owning
                segment; the child parents under the chain ROOT's plan
                state row (the canvas draws it as a spawn spur off the
                plan node). Spawn ONLY on a state-less seat — fork
                continues a recorded step conversation, which a
                graph-level seat has none of. 409 active chain, 404
                unknown segment/chain, 400 bad node key.
  POST /threads/:thread_id/states/:state_id/fork — the inherit-context
                twin (the fork button on every agent step): the child
                CONTINUES the clicked agent step's recorded conversation
                (the NEXT request's as-sent messages — the fork point's
                reply + tool results included; the chain's last request
                forks from just before its reply) under the directive
                {goal, request_id, max_steps?} — child_kind 1, the
                fork_<topic> slug, the handback-reserve contract on.
                400 when the request id is not on the state's chain.
  GET /active → the in-flight runs' thread ids (empty when idle) — the
              bridge forwards this to the UI's live-view poll so the
              thread graph opens on the click, before any plan row
              persists.
  WS /ws     → the deep-agent EVENT STREAM: one JSON text frame per
              thread/state lifecycle event (thread_started /
              state_created / state_updated / node_finished /
              states_settled / result_recorded / thread_settled —
              published by the planning store, the node-lane writer
              (EVERY node finish announces itself) and this module as
              the run progresses), an idle {"type":"heartbeat"} frame
              every WS_HEARTBEAT_SECONDS (the end-to-end liveness
              signal the bridge measures and forwards to browsers),
              a handshake hello carrying the in-flight thread ids, and
              a "pong" reply to a client's "ping" probe. A reader task
              consumes client frames and reaps the connection on
              disconnect, so uvicorn's shutdown never waits on an idle
              relay.
              The data_viz API bridge holds ONE such connection and
              fans every frame out to the browser sockets it serves at
              /api/ai/events (api/services/ai-events.service.ts) — the
              push channel that makes the Generate click's Summary
              History record and the live graph updates instant. The
              handshake's first frame is a hello carrying the in-flight
              thread ids (a bridge that reconnected mid-run re-syncs
              from it; events are notifications, the DB stays the
              source of truth).
  GET /health → liveness probe.

Network-only (LLM POST + MCP session, no pandas/cudf) — same light
resource gate as the CLI. One MCP session per request (sessions are
single-run by design); the provider is constructed once at startup and
shared — its transport is a plain async JSON POSTer, safe across
concurrent runs.

The heartbeat service name IS the module path (``python -m
llm_agents.deep_agents.server`` — the stream watchdog's spawn contract),
mirroring llm_agents.mcp.
"""
from __future__ import annotations

# Network-only server (LLM POST + MCP session, no pandas/cudf) — same
# light resource gate as the deep_agents CLI.
from _common.pre_check import pre_check

pre_check(min_sys_gb=4, require_gpu=False)

import logging
import argparse  # noqa: E402
import asyncio  # noqa: E402
import contextlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import uuid  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple, Union  # noqa: E402

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from pydantic import ConfigDict, BaseModel, Field  # noqa: E402

from llm_agents.deep_agents.run_input import RunInput, ScopePick

from _common.service_heartbeat import register_service  # noqa: E402
from llm_agents.deep_agents import events  # noqa: E402
from llm_agents.deep_agents.langgraph import runner  # noqa: E402  (settle_thread)
from llm_agents.deep_agents.langgraph.super_run import run_thread  # noqa: E402
from llm_agents.deep_agents.llm_config import (  # noqa: E402
    build_chat_provider, load_llm_config,
)
from llm_agents.deep_agents.limits import run_limits  # noqa: E402
from llm_agents.deep_agents.selftest import DummySelfTestRun  # noqa: E402
from llm_agents.deep_agents.core.mcp import (  # noqa: E402
    DEFAULT_MCP_URL, McpToolClient,
)
from llm_agents.deep_agents.core.provider import ZhipuChatProvider  # noqa: E402
from llm_agents.deep_agents.core.sandbox import Sandbox  # noqa: E402
from llm_agents.deep_agents.core.delegation import (  # noqa: E402
    DELEGATION_GUIDANCE,
)
from llm_agents.deep_agents.core.data_definitions import (  # noqa: E402
    SubagentRequest, SubagentRun,
)
from llm_agents.deep_agents.langgraph.hooks import SpawnHooks  # noqa: E402
from llm_agents.deep_agents.core.loop import DeepAgent  # noqa: E402
from llm_agents.deep_agents.planning.session import SpawnPassDown  # noqa: E402
from _common.db_commons import get_db_connection_async  # noqa: E402
from _common.sql_views import render_sql_view  # noqa: E402

logger = logging.getLogger("deep_agents.server")

SERVICE = "llm_agents.deep_agents.server"
DEFAULT_PORT = 8766

# The ENV-KEY fallback provider, constructed once at startup (the
# retrying transport is per-process). Every RUN resolves its own
# provider first — static.llm_config at agent start
# (llm_config.load_llm_config) — and only a missing row / failed load
# runs on this one.
_provider: Optional[ZhipuChatProvider] = None

class ActiveRunRegistry:
    """The daemon's in-flight run registry (2026-11-07 — the former
    ``_active_runs`` / ``_active_started`` module globals): thread_id →
    the task executing one /run, plus each run's registration time (the
    event loop's monotonic clock, opaque ordering info for the /active
    endpoint — the UI's live-view poll reads the endpoint to open the
    graph the moment the click lands, ~20-80s before the plan phase
    persists any rows). /stop cancels the tasks; each pops itself in a
    finally when it finishes (stopped, failed or done)."""

    def __init__(self) -> None:
        self._runs: Dict[str, "asyncio.Task[RunResponse]"] = {}
        self._started: Dict[str, float] = {}

    def register(self, thread_id: str, task: "asyncio.Task[RunResponse]",
                 started_at: float) -> None:
        self._runs[thread_id] = task
        self._started[thread_id] = started_at

    def pop(self, thread_id: str) -> None:
        self._runs.pop(thread_id, None)
        self._started.pop(thread_id, None)

    def is_active(self, thread_id: str) -> bool:
        return thread_id in self._runs

    def get(self, thread_id: str) -> Optional["asyncio.Task[RunResponse]"]:
        return self._runs.get(thread_id)

    def snapshot(self) -> Dict[str, "asyncio.Task[RunResponse]"]:
        """A copy — /stop cancels the copy so the registry never mutates
        during iteration."""
        return dict(self._runs)

    def ids(self) -> List[str]:
        return sorted(self._runs)

    def started_at(self, thread_id: str) -> float:
        return self._started.get(thread_id, 0.0)


# The process-wide registry (one daemon process).
RUNS = ActiveRunRegistry()

# The failure note settle_thread writes over steps still in_progress
# when the run was stopped (the audit trail's "why it ended").
STOPPED_FAILURE = "stopped by user (POST /stop)"


class RunRequest(BaseModel):
    """One deep-agent run: the goal + whatever structured data the
    caller wants the run to see (+ the CLI's optional overrides)."""

    # Unknown request fields are a HARD error (422), never a silent
    # drop: a STALE daemon (running startup-time code while the sources
    # moved on) used to accept a fresh field like ``provider`` and
    # quietly run the wrong provider — the field vanished and the run
    # misbehaved with no signal (observed live 2026-11-13). Loud failure
    # surfaces the staleness; the stream watchdog's stale-process check
    # (database/stream_watchdog.py) is the structural fix that restarts
    # such a daemon on its own.
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)
    data: Optional[Dict[str, Any]] = None
    model: Optional[str] = None
    # The provider override ('zhipu' | 'dummy' — build_chat_provider's
    # labels): an explicit request wins over the active secret_config
    # row, the same way model does. 'dummy' runs the offline test
    # double — in-process canned replies, or HTTP-sim (the real
    # transport against the running dummy llm server) when the row's
    # api_url applies; a run named dummy never spends zhipu tokens.
    provider: Optional[str] = None
    # The step budget defaults from the static deep_agent_config at
    # REQUEST time (run_limits() — config edits apply without a daemon
    # restart); ge/le stay the hard API sanity bound. Raised 12 → 20
    # (and the cap 60 → 100) alongside the per-request token ceiling:
    # the runaway guard is max_tokens now, so the budgets stay
    # generous.
    max_steps: int = Field(
        default_factory=lambda: run_limits().max_conversation_steps,
        ge=1, le=100)
    system: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # A SAVED PLAN TEMPLATE (a type-plan row of
    # llm.deep_agent_langgraph_templates) to plan from instead of the
    # LLM — the Thread Graph Save button's placeholder-templated plan,
    # materialized by the plan session with the run's input args (zero
    # planning LLM calls; fail-soft — an unusable template falls back
    # to the normal proposal session).
    plan_template_id: Optional[int] = Field(default=None, ge=1)


class PlanStepState(BaseModel):
    """One plan step's final state — the thread snapshot rows."""

    state_id: int
    ordinal: int
    node: str
    title: str
    status: str


class RunResponse(BaseModel):
    """The final answer plus how the agent got there. ``thread_id`` is
    the run's CHAIN ROOT (the plan thread — the id the run was
    registered under); ``super_thread_id`` is the same chain id (the
    plan/custom/result threads all share it — the UI resolves the
    whole chain through either). ``plan`` is the settled CHAIN
    snapshot (every thread's rows in chain order). ``stopped`` is
    true when POST /stop cancelled the run before it finished (empty
    answer; the plan snapshot, when a plan had persisted, is the
    settled one)."""

    answer: str
    steps: int
    n_tool_calls: int
    model: str
    thread_id: Optional[str] = None
    super_thread_id: Optional[str] = None
    plan: List[PlanStepState] = Field(default_factory=list)
    stopped: bool = False
    # The ``[this is test]`` self-test's ROUTER-WALK chains (the spawn /
    # fork / fork-drift / join / probe walks that STAY in the history
    # beside the main chain) — a leave-no-trace driver (the standing
    # prefix probe) purges them after its assertion pass; every other
    # consumer ignores the field.
    walk_chain_ids: List[str] = Field(default_factory=list)


class StopRequest(BaseModel):
    """Stop the named in-flight run, or every active one when absent."""

    thread_id: Optional[str] = None


class StopResponse(BaseModel):
    """How many runs the stop actually cancelled (0 = none was running).
    The response itself is the stop confirmation: it resolves only after
    the cancelled run(s) unwound and settled their chains (bounded by
    STOP_SETTLE_WAIT_SEC), so a client may draw the settled chain from
    one read issued right after it."""

    stopped: int
    thread_ids: List[str] = Field(default_factory=list)


# The boot-time ORPHAN SWEEP's failure note — written over the dead
# runs' still-pending/in_progress rows so the UI reads them as ended
# (a stale beat of a DIFFERENT kind: the run data, not the process).
ORPHAN_FAILURE = "orphaned by a daemon restart — settled at boot"


async def _settle_orphan_chains() -> int:
    """Settle every chain still UNSETTLED at daemon START — open
    pending/in_progress state rows OR running runner-lane node items
    (the widened discovery). The fresh process owns ZERO runs, so each
    unsettled chain belongs to a dead daemon life (killed mid-run by a
    crash, OOM kill or a forced recycle — e.g. the stream watchdog's
    stale-process recycle) — its rows used to read as a live run
    forever and the UI kept the live chip + Stop button up (observed
    2026-11-13: six zombie chains dating back to Nov 7; the same day's
    items-half caught the mirror flavor — a chain whose state rows were
    all gone while a node item still ran 'running' forever). Fail-soft:
    a failed sweep only leaves the old zombies, never blocks the boot."""
    from llm_agents.deep_agents.langgraph.state_store import DeepAgentStore
    from llm_agents.deep_agents.planning.store import PLAN_STORE

    settled = 0
    try:
        orphans = await PLAN_STORE.list_unsettled_super_threads()
    except Exception as exc:  # noqa: BLE001
        logger.warning("orphan sweep read failed: %s", exc)
        return 0
    store: Optional[DeepAgentStore] = None
    for sid in orphans:
        try:
            await runner.settle_thread(sid, failure=ORPHAN_FAILURE)
            if store is None:
                store = DeepAgentStore()
            # The item settle walks the chain's member threads — PLUS
            # the sid's own namespace: an items-only orphan (its state
            # rows purged/never written) resolves to an EMPTY members
            # list, and without this the stuck item survived every
            # sweep, the discovery kept finding the chain, and the UI's
            # live chip never cleared.
            members = await PLAN_STORE.fetch_chain_threads(sid)
            for member in ([sid, *members] if sid not in members
                           else members):
                await store.settle_node_items(member, failure=ORPHAN_FAILURE)
            settled += 1
            logger.warning("[orphan sweep] %s settled (dead daemon life's "
                           "chain)", sid)
        except Exception as exc:  # noqa: BLE001 — fail-soft per chain
            logger.warning("orphan sweep: %s settle failed: %s", sid, exc)
    return settled


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _provider
    _provider = ZhipuChatProvider()
    # The boot sweep runs BEFORE serving: this process owns zero runs,
    # so every unsettled chain is an orphan of a dead life.
    settled = await _settle_orphan_chains()
    if settled:
        logger.warning("[orphan sweep] %d orphaned chain(s) settled at "
                       "boot", settled)
    yield


app = FastAPI(title="deep-agents", lifespan=_lifespan)


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": SERVICE}


class ActiveRun(BaseModel):
    """One in-flight run as the /active endpoint reports it — the
    thread id exists from the /run registration onward (before any
    plan row persists)."""

    thread_id: str
    started_at: float


class ActiveResponse(BaseModel):
    """Every currently-registered run (empty = none)."""

    active: List[ActiveRun] = Field(default_factory=list)


@app.get("/active", response_model=ActiveResponse)
async def active() -> ActiveResponse:
    """The in-flight runs' thread ids — the bridge forwards this to the
    UI's live-view poll, which opens the thread graph on the first id
    (the graph itself 404-polls until the plan rows persist)."""
    return ActiveResponse(active=[
        ActiveRun(thread_id=thread_id, started_at=RUNS.started_at(thread_id))
        for thread_id in RUNS.ids()
    ])


class ExternalStepIn(BaseModel):
    """One externally added step — the same shape as update_plan's
    add_steps entries (validated by the same parser: business node
    names, title required, optional notes / spawn goal)."""

    node: Optional[str] = None
    title: str = Field(min_length=1)
    notes: Optional[Union[str, Dict[str, Any]]] = None
    delegate_goal: Optional[str] = None


class ExternalStepsRequest(BaseModel):
    """The external-topology body: steps to add, and where (omit
    after_ordinal to append at the plan's end)."""

    add_steps: List[ExternalStepIn] = Field(min_length=1)
    after_ordinal: Optional[int] = Field(default=None, ge=1)


class AddedStepOut(BaseModel):
    """One step the external add minted (the response's rows)."""

    state_id: int
    ordinal: int
    node: str
    title: str
    status: str


class ExternalStepsResponse(BaseModel):
    added: List[AddedStepOut] = Field(default_factory=list)


@app.post("/threads/{thread_id}/steps",
          response_model=ExternalStepsResponse)
async def add_thread_steps(thread_id: str,
                           req: ExternalStepsRequest) -> ExternalStepsResponse:
    """External topology change on a RUNNING run (see the module
    header). The liveness gate is THIS process's run registry — the one
    daemon that owns the graphs. Any chain member id resolves the run;
    the steps land on the chain thread that still has runnable rows
    (the custom or result graph's lane — the root's plan row is never
    the target); the guarantees (history kept, other paths' future
    nodes kept, pickup next hop) live in authorities.external_add_steps."""
    if not RUNS.is_active(thread_id):
        return JSONResponse(
            status_code=409,
            content={"detail": "thread is not running on this daemon "
                               "(settled, unknown, or another process)"})
    from llm_agents.deep_agents.planning.authorities import (
        ExternalPlanError, PlanAuthority,
    )
    from llm_agents.deep_agents.planning.store import (PLAN_STORE)
    # Resolve the chain's ACTIVE lane: the member with pending or
    # in_progress rows (fall back to the named thread — a run still in
    # its plan phase has none yet, and the add then 400s in the
    # authority's pending-rows check, the old behavior).
    lane_id = thread_id
    try:
        for member in await PLAN_STORE.fetch_chain_threads(thread_id):
            rows = await PLAN_STORE.list_thread_states(member)
            if any(s.status in ("pending", "in_progress") for s in rows):
                lane_id = member
                break
    except Exception as exc:  # noqa: BLE001 — degrade to the named thread
        logger.warning("chain lane resolve failed (%s): %s", thread_id, exc)
    raw = [s.model_dump(exclude_none=True) for s in req.add_steps]
    try:
        added = await PlanAuthority.external_add_steps(
            lane_id, raw, after_ordinal=req.after_ordinal)
    except ExternalPlanError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    return ExternalStepsResponse(added=[
        AddedStepOut(state_id=s.state_id, ordinal=s.ordinal, node=s.node,
                     title=s.title, status=s.status)
        for s in added
    ])


# ---------------------------------------------------------------------------
# THE NODE ACTIONS (2026-11-15 — the Thread Graph's fork/spawn buttons):
# run ONE delegation out of a SETTLED run's node, driven from the UI.
#
#   POST /threads/:thread_id/states/:state_id/spawn  {goal, max_steps?}
#   POST /threads/:thread_id/states/:state_id/fork   {goal, request_id,
#                                                     max_steps?}
#
# The SPAWN lane mints a fresh-context child state row under the clicked
# node's state (child_kind 0, the same row shape every model-called
# spawn registers) and runs it under the FULL supervision policy — the
# SpawnHooks machinery (the run's pass-down → the child row → the plan/
# stats/kg kits → the child's own hooks) and the DeepAgent loop — so the
# new node joins the canvas and records agent steps exactly like a
# run-native spawn's. The FORK lane is the inherit-context twin: the
# child CONTINUES the clicked agent step's recorded conversation (the
# NEXT request's as-sent messages — the fork point's reply and its tool
# results included; the chain's last request forks from just before its
# reply) under the user's directive — child_kind 1, the fork_<topic>
# slug, the handback-reserve contract on.
#
# Both run as background tasks on THIS daemon (one delegation is a
# minutes-long LLM conversation; the /run runs' RUNS registry stays
# separate): the response carries the child's ids immediately (the row
# exists — the input hook ran synchronously) and the UI polls the thread
# detail until the child state row settles. 409 when the chain is an
# ACTIVE run on this daemon (a live run's tree is its lane's business —
# the external-add endpoint above is the running-run surface); 404
# unknown state; 400 a fork request id outside the state's chain.
# ---------------------------------------------------------------------------


class NodeSpawnRequest(BaseModel):
    """One UI SPAWN action's body — the fresh-context child's goal."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)
    max_steps: Optional[int] = Field(default=None, ge=1, le=100)
    # The provider override ('zhipu' | 'dummy' — build_chat_provider's
    # labels), the same contract RunRequest.provider holds: an explicit
    # action runs that provider on its own defaults (the dummy test
    # double never spends zhipu tokens).
    provider: Optional[str] = None


class NodeForkRequest(BaseModel):
    """One UI FORK action's body — the continuation directive plus the
    agent step (request id) to continue from."""

    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)
    request_id: int = Field(ge=1)
    max_steps: Optional[int] = Field(default=None, ge=1, le=100)
    provider: Optional[str] = None


class NodeActionResult(BaseModel):
    """The background delegation's registration — the child's ids. The
    UI polls the thread detail until the child state row settles."""

    kind: str
    state_id: Optional[int] = None
    context_id: Optional[str] = None
    thread_id: Optional[str] = None
    parent_state_id: Optional[int] = None
    status: str = "running"


# The node actions' in-flight registry: child state_id → its runner task
# (pops itself in the runner's finally).
NODE_ACTIONS: Dict[int, "asyncio.Task[None]"] = {}


async def _fetch_plan_passdown(root_thread_id: str) -> Optional[Dict[str, Any]]:
    """The chain ROOT's plan input context payload ({args, goal, …}) —
    the UI spawn's pass-down source (sql view
    llm_agents/deep_agents/server/plan_run_input). Fail-soft None (a
    degraded/legacy thread without a plan row)."""
    try:
        conn = await get_db_connection_async()
    except Exception as exc:  # noqa: BLE001 — degrade to the bare goal
        logger.warning("node action pass-down connect failed: %s", exc)
        return None
    try:
        view = render_sql_view(
            "llm_agents/deep_agents/server/plan_run_input",
            thread_id=str(root_thread_id))
        rows = await conn.fetch(view.sql, *view.params)
    except Exception as exc:  # noqa: BLE001 — degrade to the bare goal
        logger.warning("node action pass-down read failed: %s", exc)
        return None
    finally:
        await conn.close()
    if not rows or rows[0]["payload"] is None:
        return None
    payload = rows[0]["payload"]
    return dict(payload) if isinstance(payload, dict) else None


async def _fetch_fork_messages(chain_id: Optional[str],
                               request_id: int) -> Optional[List[Any]]:
    """The fork point's CONTINUATION transcript (sql view
    llm_agents/deep_agents/server/fork_transcript): the chain's
    message-bearing request rows in order — a chained row stores only
    its NEW messages (_chain_incremental), so the full transcript is
    reconstructed by walking the rows (a non-incremental row replaces
    the list) up to the fork point's NEXT message row (its delta = the
    fork point's reply + tool results; a fork on the chain's last
    message row continues from just before that reply). None when the
    request is not on this chain (or the chain is unstamped)."""
    if not chain_id:
        return None
    try:
        conn = await get_db_connection_async()
    except Exception as exc:  # noqa: BLE001 — surfaced as a 400
        logger.warning("node fork transcript connect failed: %s", exc)
        return None
    try:
        view = render_sql_view(
            "llm_agents/deep_agents/server/fork_transcript",
            chain_id=str(chain_id))
        rows = await conn.fetch(view.sql, *view.params)
    except Exception as exc:  # noqa: BLE001 — surfaced as a 400
        logger.warning("node fork transcript read failed: %s", exc)
        return None
    finally:
        await conn.close()
    if not rows:
        return None
    # The fork point's position among the chain's message rows.
    point_idx: Optional[int] = None
    for i, row in enumerate(rows):
        if int(row["request_id"]) == int(request_id):
            point_idx = i
            break
    if point_idx is None:
        return None
    # Accumulate through the fork point's NEXT message row (or the
    # fork point itself when it is the chain's last).
    cut = point_idx + 1 if point_idx + 1 < len(rows) else point_idx
    full: List[Any] = []
    for row in rows[:cut + 1]:
        raw = row["messages"]
        if raw is None:
            continue
        msgs = raw if isinstance(raw, list) else json.loads(str(raw))
        if row["incremental"] and full:
            full.extend(msgs)
        else:
            full = list(msgs)
    return full or None


async def _resolve_action_runtime(
    provider_label: Optional[str] = None,
) -> Any:
    """The node action's provider + model — the same resolution a /run
    uses (static.llm_config's active row at action start; the lifespan
    env-key fallback). An explicit *provider_label* wins over the row
    (the RunRequest.provider contract — a test action names 'dummy' and
    never spends zhipu tokens). Returns (provider, model)."""
    cfg = (await load_llm_config()
           if not provider_label else None)
    if cfg is not None:
        provider = build_chat_provider(
            cfg.provider, api_key=cfg.api_key, api_url=cfg.api_url)
        model = cfg.model_name or ZhipuChatProvider.DEFAULT_MODEL
    elif provider_label:
        provider = build_chat_provider(provider_label.strip().lower())
        model = ZhipuChatProvider.DEFAULT_MODEL
    else:
        assert _provider is not None  # lifespan ran before any request
        provider = _provider
        model = ZhipuChatProvider.DEFAULT_MODEL
    return provider, model


async def _run_node_child(run: SubagentRun, hooks: SpawnHooks, *,
                          provider: ZhipuChatProvider, model: str,
                          fork_child: bool) -> None:
    """One UI node action's child conversation — the spawner's
    ``_execute_subagent`` face without a parent loop: the request-stamped
    child DeepAgent under the action's OWN MCP session + sandbox cell,
    the run wrapper binding the child's request chain, the output hook
    settling the child row (completed/failed) whatever the outcome. The
    task pops itself from NODE_ACTIONS."""
    state_id = run.state_id
    try:
        async with McpToolClient(url=DEFAULT_MCP_URL) as mcp:
            child = DeepAgent(
                provider, mcp,
                model=model,
                max_steps=run.request.max_steps,
                system=(run.request.system + DELEGATION_GUIDANCE
                        if run.request.depth < run_limits().max_spawn_depth
                        else run.request.system),
                allow_subagents=True,
                spawn_depth=run.request.depth,
                node_role=run.request.role,
                fork_child=fork_child,
                builtin_tools=run.request.builtin_tools,
                sandbox=Sandbox(),
                subagent_input_hook=run.request.subagent_input_hook,
                subagent_output_hook=run.request.subagent_output_hook,
                subagent_run_wrapper=run.request.subagent_run_wrapper,
            )
            wrapper = hooks.run_wrapper(run)
            if wrapper is not None:
                with wrapper:
                    result = await child.run(
                        run.request.goal, data=run.request.data,
                        user_addendum=run.request.user_addendum,
                        inherited_messages=run.request.inherit_transcript)
            else:
                result = await child.run(
                    run.request.goal, data=run.request.data,
                    user_addendum=run.request.user_addendum,
                    inherited_messages=run.request.inherit_transcript)
            run.status = "completed"
            run.answer = result.answer
            run.steps = result.steps
            run.tool_call_count = len(result.tool_calls)
    except asyncio.CancelledError:
        run.status = "cancelled"
        raise
    except Exception as exc:  # noqa: BLE001 — the row settles failed
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
    finally:
        with contextlib.suppress(Exception):
            await hooks.output_hook(run)
        if state_id is not None:
            NODE_ACTIONS.pop(state_id, None)


async def _register_and_run_spawn(
    parent_row: Any, root: str, goal: str, budget: int,
    provider_label: Optional[str] = None,
) -> Tuple[SubagentRun, Any]:
    """The shared SPAWN registration (both the state-keyed and the
    node-keyed endpoint bodies): the pass-down from the chain root's
    plan context, the SpawnHooks registration under *parent_row* (the
    child row + stamped request exist before the background work), the
    runner task started, NODE_ACTIONS registered. Returns (run,
    parent_row)."""
    from llm_agents.deep_agents.langgraph.state_store import DeepAgentStore

    provider, model = await _resolve_action_runtime(provider_label)
    store = DeepAgentStore()
    payload = await _fetch_plan_passdown(root) or {}
    args = payload.get("args")
    biz_ctx = SpawnPassDown.pack(
        str(payload.get("goal") or ""),
        args if isinstance(args, dict) else None,
        payload.get("prompts_loaded") or {},
        payload.get("prompts_catalog") or {},
    ).to_context()
    hooks = SpawnHooks(
        str(parent_row.thread_id), parent_row.state_id, store=store,
        model=model,
        state_by_run={}, context_by_run={},
        biz_context=biz_ctx)
    run = SubagentRun(
        run_id=uuid.uuid4().hex[:8],
        request=SubagentRequest(
            goal=goal, max_steps=budget, background=False, depth=1),
        background=False,
    )
    await hooks.input_hook(run)
    task = asyncio.create_task(_run_node_child(
        run, hooks, provider=provider, model=model, fork_child=False))
    if run.state_id is not None:
        NODE_ACTIONS[run.state_id] = task
    return run, parent_row


async def _node_action(
    thread_id: str, state_id: int, kind: str, goal: str,
    max_steps: Optional[int], request_id: Optional[int],
    provider_label: Optional[str] = None,
) -> NodeActionResult:
    """The shared spawn/fork endpoint body: resolve the clicked state
    row, guard the chain, register the child row synchronously (the
    input hook — the response's ids exist before the background work),
    then start the runner task."""
    from llm_agents.deep_agents.langgraph.state_store import DeepAgentStore
    from llm_agents.deep_agents.planning.store import PLAN_STORE

    row = await PLAN_STORE.fetch_state(int(state_id))
    if row is None:
        raise HttpLike(404, f"no state row {state_id}")
    row_thread = str(row.thread_id)
    # The row's chain root (the first member in chain order — StateRow
    # carries no super stamp; the members read resolves it from any id).
    try:
        members = await PLAN_STORE.fetch_chain_threads(row_thread)
    except Exception as exc:  # noqa: BLE001 — degrade to the own thread
        logger.warning("node action chain read failed (%s): %s",
                       state_id, exc)
        members = [row_thread]
    root = members[0] if members else row_thread
    # The path thread must name the row's chain (the UI opens runs by
    # the root; any member id also resolves).
    if thread_id not in {row_thread, root, *members}:
        raise HttpLike(404, f"state {state_id} is not on thread chain "
                            f"'{thread_id}'")
    if RUNS.is_active(root) or RUNS.is_active(row_thread):
        raise HttpLike(409, "the run this node belongs to is still "
                            "active on this daemon — stop it first")
    provider, model = await _resolve_action_runtime(provider_label)
    store = DeepAgentStore()
    state_by_run: Dict[str, int] = {}
    context_by_run: Dict[str, Optional[str]] = {}
    budget = max_steps or run_limits().max_conversation_steps
    if kind == "fork":
        messages = await _fetch_fork_messages(row.llm_context_id,
                                              int(request_id or 0))
        if not messages:
            raise HttpLike(
                400, f"request {request_id} carries no conversation on "
                     f"state {state_id}'s chain — fork rides a recorded "
                     "inference step of this node")
        hooks = SpawnHooks(
            row_thread, row.state_id, store=store, model=model,
            state_by_run=state_by_run, context_by_run=context_by_run)
        run = SubagentRun(
            run_id=uuid.uuid4().hex[:8],
            request=SubagentRequest(
                goal=goal, max_steps=budget, background=False,
                depth=1, inherit_transcript=list(messages)),
            background=False,
        )
        # The synchronous registration: the child row + its stamped
        # request (the response's ids exist before the work starts).
        await hooks.input_hook(run)
        task = asyncio.create_task(_run_node_child(
            run, hooks, provider=provider, model=model, fork_child=True))
        if run.state_id is not None:
            NODE_ACTIONS[run.state_id] = task
        return NodeActionResult(
            kind=kind, state_id=run.state_id,
            context_id=run.llm_context_id,
            thread_id=row_thread, parent_state_id=row.state_id,
            status="running")
    run, parent = await _register_and_run_spawn(
        row, root, goal, budget, provider_label)
    return NodeActionResult(
        kind=kind, state_id=run.state_id, context_id=run.llm_context_id,
        thread_id=str(parent.thread_id), parent_state_id=parent.state_id,
        status="running")


class HttpLike(Exception):
    """An endpoint-local status-carrying error — rendered by the same
    JSON-shape handler contract the HttpError bridge side uses."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@app.post("/threads/{thread_id}/states/{state_id}/spawn",
          response_model=NodeActionResult)
async def spawn_thread_node(thread_id: str, state_id: int,
                            req: NodeSpawnRequest) -> NodeActionResult:
    """One UI SPAWN action — a fresh-context child under the clicked
    node (see the block comment above)."""
    try:
        return await _node_action(thread_id, state_id, "spawn",
                                  req.goal, req.max_steps, None,
                                  req.provider)
    except HttpLike as exc:
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": exc.detail})


@app.post("/threads/{thread_id}/states/{state_id}/fork",
          response_model=NodeActionResult)
async def fork_thread_node(thread_id: str, state_id: int,
                           req: NodeForkRequest) -> NodeActionResult:
    """One UI FORK action — a continuation child from the clicked agent
    step's recorded conversation (see the block comment above)."""
    try:
        return await _node_action(thread_id, state_id, "fork",
                                  req.goal, req.max_steps, req.request_id,
                                  req.provider)
    except HttpLike as exc:
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": exc.detail})


# The canvas node key: bare for the plan segment's nodes, `s<k>:`-prefixed
# for k>0 segments (s1:output = the custom graph's output seat, s2:output
# = the result's) — the same ids the merged thread detail carries.
_NODE_KEY_RE = re.compile(r"^(?:(s(\d+)):)?([a-z_][a-z0-9_:-]{0,80})$")


@app.post("/threads/{thread_id}/nodes/{node}/spawn",
          response_model=NodeActionResult)
async def spawn_thread_run_node(thread_id: str, node: str,
                                req: NodeSpawnRequest) -> NodeActionResult:
    """The RUN-NODE SPAWN (2026-11-15, the failed-output-node request): a
    UI spawn action hanging off a RUNNER-LANE SEAT — the output node has
    NO deep_agent_states row, so the state-keyed route cannot serve it.
    The canvas node key's `s<k>:` prefix resolves the OWNING segment;
    the child parents under the CHAIN ROOT's plan state row (the run's
    plan-level spawn — the canvas draws it as a spawn spur off the plan
    node). Spawn is the only action a state-less seat carries: fork
    continues a recorded step conversation, which a graph-level seat has
    none of."""
    from llm_agents.deep_agents.planning.store import PLAN_STORE

    match = _NODE_KEY_RE.match(str(node))
    if match is None:
        return JSONResponse(status_code=400, content={
            "detail": f"bad node key '{node}'"})
    try:
        seg_idx = int(match.group(2) or 0)
        members = await PLAN_STORE.fetch_chain_threads(str(thread_id))
        if not members:
            raise HttpLike(404, f"no deep-agent chain for '{thread_id}'")
        if seg_idx >= len(members):
            raise HttpLike(404, f"segment {seg_idx} out of range "
                                f"({len(members)} segments)")
        root = members[0]
        if RUNS.is_active(root):
            raise HttpLike(409, "the run is still active on this daemon — "
                                "stop it first")
        # The parent: the chain root's plan state row (the plan session's
        # graph-level row), falling back to the first top-level row.
        rows = await PLAN_STORE.list_thread_states(root)
        parent = next((r for r in rows if r.node == "plan"), None) \
            or (rows[0] if rows else None)
        if parent is None:
            raise HttpLike(404, "the chain has no settled top-level row "
                                "to parent the spawn under")
        budget = req.max_steps or run_limits().max_conversation_steps
        run, parent_row = await _register_and_run_spawn(
            parent, root, req.goal, budget, req.provider)
    except HttpLike as exc:
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": exc.detail})
    return NodeActionResult(
        kind="spawn", state_id=run.state_id,
        context_id=run.llm_context_id,
        thread_id=str(parent_row.thread_id),
        parent_state_id=parent_row.state_id,
        status="running")


@app.post("/threads/{thread_id}/nodes/{node}/fork",
          response_model=NodeActionResult)
async def fork_thread_run_node(thread_id: str, node: str,
                               req: NodeForkRequest) -> NodeActionResult:
    """The RUN-NODE FORK (2026-11-15, "should show both fork and spawn";
    topology reworked the same day — "forked node on the main branch,
    failed node on side branch"): the fork action on a RUNNER-LANE SEAT
    — the seat has no state row, but its node item carries the seat
    conversation's OWN chain, and the fork point request (an inference
    row of the seat's agent steps) is validated ON that chain.

    THE MAIN-LINE TOPOLOGY (driven from the backend state rows, the
    drift-fork precedent): the continuation lands as a TOP-LEVEL row on
    the OWNING segment's thread (parent NULL, ordinal after the last
    top-level row, child_kind 1) — the canvas's lane walk renders it IN
    the lane; the pre-fork attempt materializes as the seat's kind-2
    old-chain stub UNDER the fork row wearing the seat's status/error —
    the failed node on the side branch. The canvas derives the whole
    shape from the rows; nothing is UI-side."""
    from llm_agents.deep_agents.langgraph.state_store import DeepAgentStore
    from llm_agents.deep_agents.langgraph.hooks.spawns import (
        _fork_node_slug, _old_node_slug,
    )
    from llm_agents.deep_agents.langgraph.state_store import (
        DeepAgentStore, new_context_id,
    )
    from llm_agents.deep_agents.planning.store import PLAN_STORE

    match = _NODE_KEY_RE.match(str(node))
    if match is None:
        return JSONResponse(status_code=400, content={
            "detail": f"bad node key '{node}'"})
    try:
        seg_idx = int(match.group(2) or 0)
        bare_node = match.group(3)
        members = await PLAN_STORE.fetch_chain_threads(str(thread_id))
        if not members:
            raise HttpLike(404, f"no deep-agent chain for '{thread_id}'")
        if seg_idx >= len(members):
            raise HttpLike(404, f"segment {seg_idx} out of range "
                                f"({len(members)} segments)")
        root = members[0]
        if RUNS.is_active(root):
            raise HttpLike(409, "the run is still active on this daemon — "
                                "stop it first")
        # The seat's OWN chain + its settled face (node_chain.sql) — the
        # fork point must be a request ON it, and the seat's status/
        # error ride the old-chain stub.
        owner = members[seg_idx]
        # THE FINAL-RESULT REFRESH (2026-11-16, "since the forked node is
        # the final output, the result should also get refreshed"): a
        # fork off the chain's LAST segment's OUTPUT seat continues THE
        # FINAL ANSWER's own conversation — when the child completes,
        # its answer REPLACES the run's final-result document (the
        # Result view and the Thread Graph's seat context read exactly
        # that row; a middle seat's fork refreshes nothing — the result
        # graph already consumed the pre-fork handoff).
        refresh_final = bare_node == "output" and seg_idx == len(members) - 1
        conn = await get_db_connection_async()
        try:
            view = render_sql_view(
                "llm_agents/deep_agents/server/node_chain",
                thread_id=str(owner), node=bare_node)
            chain_rows = await conn.fetch(view.sql, *view.params)
        finally:
            await conn.close()
        if not chain_rows or chain_rows[0]["chain_id"] is None:
            raise HttpLike(404, f"the seat '{node}' recorded no "
                                "conversation to fork from")
        seat_chain = str(chain_rows[0]["chain_id"])
        seat_status = str(chain_rows[0]["seat_status"] or "skipped")
        seat_error = chain_rows[0]["seat_error"]
        messages = await _fetch_fork_messages(seat_chain,
                                              int(req.request_id))
        if not messages:
            raise HttpLike(
                400, f"request {req.request_id} carries no conversation "
                     f"on the '{node}' seat's chain — fork rides a "
                     "recorded inference step of this node")
        budget = req.max_steps or run_limits().max_conversation_steps
        provider, model = await _resolve_action_runtime(req.provider)
        store = DeepAgentStore()
        # THE MAIN-LINE ROW: the continuation as a top-level fork row on
        # the owning segment (the canvas lane renders it after the
        # segment's last step).
        context_id = new_context_id()
        from llm_agents.deep_agents.planning.plan_json import MAX_TITLE_LEN
        fork_row_id = await PLAN_STORE.create_toplevel_fork(
            str(owner), _fork_node_slug(req.goal),
            req.goal.strip()[:MAX_TITLE_LEN], llm_context_id=context_id)
        # THE FAILED NODE ON THE SIDE BRANCH: the pre-fork attempt as
        # the seat's kind-2 old-chain stub under the fork row, settled
        # with the seat's OWN status + error.
        stub_title = (f"[old chain] {bare_node} attempt ({seat_status})"
                      + (f": {str(seat_error)[:160]}" if seat_error else ""))
        stub_id = await PLAN_STORE.create_child_state(
            str(owner), fork_row_id, _old_node_slug(),
            stub_title[:MAX_TITLE_LEN], child_kind=2)
        await PLAN_STORE.finish_child_state(
            stub_id, seat_status if seat_status in ("failed", "skipped",
                                                    "completed")
            else "skipped",
            error=str(seat_error)[:800] if seat_error else None,
            thread_id=str(owner), node=_old_node_slug())
        # The preregistered lane: the hooks mount the kits/hints/system
        # under the EXISTING row (no second row).
        state_by_run: Dict[str, int] = {}
        context_by_run: Dict[str, Optional[str]] = {}
        hooks = SpawnHooks(
            str(owner), fork_row_id, store=store, model=model,
            state_by_run=state_by_run, context_by_run=context_by_run,
            preregistered=True)
        run = SubagentRun(
            run_id=uuid.uuid4().hex[:8],
            request=SubagentRequest(
                goal=req.goal, max_steps=budget, background=False,
                depth=1, inherit_transcript=list(messages)),
            background=False,
        )
        run.state_id = fork_row_id
        run.llm_context_id = context_id
        await hooks.input_hook(run)

        async def _run_fork_child() -> None:
            """The run-node fork's child runner — the shared body, plus
            the FINAL-RESULT refresh: a COMPLETED continuation of the
            chain's last output seat replaces the run's final-result
            document with its answer (fail-soft — the child row is
            already settled; only the refresh is lost)."""
            await _run_node_child(
                run, hooks, provider=provider, model=model,
                fork_child=True)
            if (refresh_final and run.status == "completed"
                    and run.answer):
                try:
                    await PLAN_STORE.record_final_result(
                        str(owner), answer=run.answer, replace=True)
                except Exception as exc:  # noqa: BLE001 — refresh-only
                    logger.warning(
                        "final-result refresh after the output-seat "
                        "fork (%s) failed: %s", fork_row_id, exc)

        task = asyncio.create_task(_run_fork_child())
        NODE_ACTIONS[fork_row_id] = task
    except HttpLike as exc:
        return JSONResponse(status_code=exc.status_code,
                            content={"detail": exc.detail})
    return NodeActionResult(
        kind="fork", state_id=run.state_id,
        context_id=run.llm_context_id,
        thread_id=str(owner),
        # parent_state_id NULL — the continuation is a TOP-LEVEL row on
        # the owning segment (the main line); the failed pre-fork
        # attempt hangs as the kind-2 stub UNDER it (the side branch).
        parent_state_id=None,
        status="running")



@app.exception_handler(Exception)
async def agent_error_handler(_req: Request, exc: Exception) -> JSONResponse:
    # Any agent-loop failure reaches the bridge as a JSON body, not a
    # blank 500 "Internal Server Error" — the UI surfaces the message.
    # The step-limit RuntimeError is the designed case; the Exception
    # catch-all covers the non-Runtime escapes (e.g. an ImportError from
    # a dependency upgraded under a running daemon). Cancellation is a
    # BaseException and never lands here — /run_one settles it as
    # stopped before it could.
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def _plan_states(rows: List[Any]) -> List[PlanStepState]:
    return [
        PlanStepState(
            state_id=s.state_id, ordinal=s.ordinal, node=s.node,
            title=s.title, status=s.status,
        )
        for s in rows
    ]


async def _run_one(req: RunRequest, thread_id: str) -> RunResponse:
    """One /run's engine — the cancellable unit (POST /stop cancels the
    task running this). A cancellation unwinds the graph at its current
    await (the in-flight LLM/MCP call dies with it, so no further LLM
    spend); the fail-soft settle keeps the thread's audit trail closed
    and the response reports stopped instead of failing.

    The provider + model resolve AT AGENT START: static.llm_config's
    active row (llm_config.load_llm_config — one DB read per run, never
    cached past it) names the provider (build_chat_provider — zhipu or
    the dummy test double) and becomes a fresh key/endpoint/model; no
    row or a failed load falls back to the lifespan-built env-key
    provider and the class DEFAULT_MODEL. An explicit RunRequest.model
    / RunRequest.provider still wins over the config's — a request
    naming a DIFFERENT provider than the row runs that provider on its
    own defaults (the row's key/endpoint/model do not leak across).

    THE ``[this is test]`` SELF-TEST (selftest.DummySelfTestRun): a
    goal carrying the prefix bypasses the whole config resolution — the
    run loads the offline dummy LLM + the feature tool seam + the
    recording System One engine, walks the canonical dummy langgraph
    chain MARKET-WIDE (``market_wide_data`` strips the caller's code
    scope before the walk), and the response's ANSWER is the
    auto-triggered assertion battery's PASS/FAIL report (see
    ``_run_chain``)."""
    self_test = DummySelfTestRun.detect(req.goal)
    if self_test is not None:
        logger.info("[this is test] self-test run (goal head: %r)",
                    req.goal[:60])
        # MARKET-WIDE (2026-11-14): the caller's code scope never rides
        # the self-test walk — dropped before run_thread so no
        # deep_agent_identities row lands and the history entry shows
        # under every Summary History subject pick. The MAIN chain is
        # the SHOWCASE (the fork/spawn routing scripted around the
        # recording real engine; the scripted sandbox serves the spawn
        # child's and the grandchild's runs), so the graph the UI opens
        # holds every scenario kind as nodes.
        req.data = self_test.market_wide_data(req.data)
        return await _run_chain(
            req, thread_id, self_test.build_provider(),
            mcp=self_test.build_mcp(),
            sandbox=self_test.build_sandbox(),
            goal=self_test.effective_goal,
            model=self_test.model_label,
            decision_engine=self_test.build_showcase_engine(),
            self_test=self_test)
    cfg = await load_llm_config()
    if cfg is not None or req.provider:
        label = (req.provider
                 or (cfg.provider if cfg is not None
                     else "")).strip().lower()
        row_applies = cfg is not None and (
            not req.provider
            or req.provider.strip().lower() == cfg.provider)
        provider = build_chat_provider(
            label,
            api_key=cfg.api_key if row_applies else None,
            api_url=cfg.api_url if row_applies else None)
    else:
        # No row, no request override — the lifespan-built env-key
        # provider (its key resolved once at startup).
        assert _provider is not None  # lifespan ran before any request
        provider = _provider
        row_applies = False
    model = (req.model
             or (cfg.model_name if row_applies else None)
             or ZhipuChatProvider.DEFAULT_MODEL)
    async with McpToolClient(url=DEFAULT_MCP_URL) as mcp:
        return await _run_chain(
            req, thread_id, provider, mcp=mcp, goal=req.goal,
            model=model, decision_engine=None, self_test=None)


async def _run_chain(
    req: RunRequest, thread_id: str, provider: ZhipuChatProvider, *,
    mcp: McpToolClient, goal: str, model: str,
    decision_engine: Optional[Any], self_test: Optional[DummySelfTestRun],
    sandbox: Optional[Any] = None,
) -> RunResponse:
    """The execute-and-assemble body every run shares (hoisted out of
    ``_run_one`` so the REAL-MCP context-managed session — the normal
    path — and the self-test's feature seam don't duplicate it): one
    ``run_thread`` call, the cancellation settle, and the response
    assembly. The self-test path passes the duck-typed doubles for
    *provider* / *mcp* / *decision_engine* (the same seam contract
    ``run_thread`` declares — the offline double satisfies the one
    ``chat_complete`` / ``tool_schemas`` + ``call_tool`` face) plus the
    scripted *sandbox* (the showcase chain's spawn-child and
    grandchild runs).
    ``self_test`` set (the ``[this is test]`` path) swaps the
    response's ANSWER for the assertion battery's rendered report —
    ``full_report`` runs the showcase chain's battery PLUS every shared
    walk and the decision battery (their chains kept in the history and
    their ids riding ``walk_chain_ids``) — a mid-walk exception (a
    dataset drift) settles as a FAIL report naming the error, never a
    500: the report IS the assert output."""
    try:
        outcome = await run_thread(
            provider, mcp, goal=goal, data=req.data,
            model=model, max_steps=req.max_steps,
            system=req.system, temperature=req.temperature,
            thread_id=thread_id,
            plan_template_id=req.plan_template_id,
            decision_engine=decision_engine, sandbox=sandbox)
    except asyncio.CancelledError:
        plan_rows: List[Any] = []
        try:
            # Chain-aware: the stop settle closes every thread the
            # run minted (plan / custom / result).
            plan_rows = await runner.settle_thread(
                thread_id, failure=STOPPED_FAILURE)
        except Exception as exc:  # noqa: BLE001 — fail-soft settle
            logger.warning("stopped-thread settle failed: %s", exc)
        return RunResponse(
            answer="", steps=0, n_tool_calls=0,
            model=model,
            thread_id=thread_id if plan_rows else None,
            super_thread_id=thread_id,
            plan=_plan_states(plan_rows), stopped=True)
    except Exception as exc:  # noqa: BLE001 — the self-test's FAIL lane
        if self_test is None:
            raise
        report = self_test.failure_report(exc)
        return RunResponse(
            answer=report.render(), steps=0, n_tool_calls=0,
            model=model, thread_id=thread_id,
            super_thread_id=thread_id, plan=[])
    if self_test is not None:
        report = await self_test.full_report(outcome)
        return RunResponse(
            answer=report.render(), steps=outcome.result.steps,
            n_tool_calls=outcome.n_tool_calls,
            model=model,
            thread_id=outcome.thread_id,
            super_thread_id=outcome.super_thread_id or outcome.thread_id,
            plan=_plan_states(outcome.plan),
            walk_chain_ids=self_test.walk_chain_ids,
        )
    return RunResponse(
        answer=outcome.result.answer, steps=outcome.result.steps,
        n_tool_calls=outcome.n_tool_calls,
        model=model,
        thread_id=outcome.thread_id,
        super_thread_id=outcome.super_thread_id or outcome.thread_id,
        plan=_plan_states(outcome.plan),
    )


@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest) -> RunResponse:
    assert _provider is not None  # lifespan ran before any request
    # The thread_id is minted HERE (not inside run_thread) so the run is
    # registered — and stoppable — from its first await onward.
    thread_id = uuid.uuid4().hex
    req.data = RunInput.validate_boundary(req.data)
    task = asyncio.create_task(_run_one(req, thread_id))
    RUNS.register(thread_id, task, asyncio.get_running_loop().time())
    # The run's birth announcement — the /ws channel's first event: the
    # UI creates + loads the Generate click's Summary History record on
    # it (the running planning node row lands in the same instants
    # through run_thread's begin_thread). The chain id rides along so
    # clients can group the later threads' state events (the custom /
    # result graphs publish under their own thread ids).
    events.publish(events.DeepAgentEvent(
        type=events.EventType.THREAD_STARTED, thread_id=thread_id,
        super_thread_id=thread_id))
    try:
        response = await task
    except BaseException:
        # The engine itself failed (the nodes' own fail-soft lanes never
        # reach here) — the run is over either way; announce it so no
        # client keeps waiting on a live view.
        events.publish(events.DeepAgentEvent(
            type=events.EventType.THREAD_SETTLED, thread_id=thread_id,
            super_thread_id=thread_id, outcome="failed"))
        raise
    finally:
        RUNS.pop(thread_id)
    events.publish(events.DeepAgentEvent(
        type=events.EventType.THREAD_SETTLED, thread_id=thread_id,
        super_thread_id=thread_id,
        outcome=("stopped" if response.stopped
                 else "completed" if response.answer else "failed")))
    return response


# The idle /ws heartbeat: an app-level {"type":"heartbeat"} frame sent
# to every subscriber when no event flowed for this long. The channel
# is notifications-only and runs can be hours apart, so without it the
# socket can sit silent long enough for an intermediary to drop it
# half-open — the bridge measures the gap and reconnects when a
# heartbeat goes missing (data_viz ai-events.service.ts).
WS_HEARTBEAT_SECONDS = 20.0


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    """The deep-agent event stream (see the module header). One relay
    loop per connection: the handshake hello (with the in-flight thread
    ids), then every bus event as a compact JSON text frame, PLUS an
    idle heartbeat frame every WS_HEARTBEAT_SECONDS so the connection
    proves itself end to end (daemon → bridge → browser) even when no
    run is happening.

    A READER task runs beside the relay: it consumes client frames —
    ``ping`` gets an immediate ``pong`` (the bridge's active probe) and
    a JSON ``{"type":"ping","probe_id"}`` (a page's run heartbeat,
    relayed by the bridge) gets the probe_id echoed back so the asking
    browser can match its pong; anything else is ignored (the channel
    is one-way by design) — and
    it is what notices a vanished client, so a dead connection is
    reaped at once instead of on the next event (which may be hours
    away), and uvicorn's graceful shutdown completes promptly instead
    of waiting forever on a relay parked in queue.get(). Send failures
    and the disconnect race exit through the same finally.
    """
    await socket.accept()
    queue = events.subscribe()
    closed = asyncio.Event()

    async def reader() -> None:
        try:
            while True:
                message = (await socket.receive_text()).strip()
                # Bare "ping" (the bridge's own probe) → bare pong; a
                # JSON {"type":"ping","probe_id"} (a page's run
                # heartbeat, relayed by the bridge) → the id echoed
                # back so the asking browser can match its probe when
                # the pong broadcasts to every subscriber.
                if message == "ping":
                    await socket.send_text(events.encode(
                        {"type": "pong", "ts": events.now_iso()}))
                    continue
                if not message.startswith("{"):
                    continue
                try:
                    payload = json.loads(message)
                except ValueError:
                    continue
                if not (isinstance(payload, dict)
                        and payload.get("type") == "ping"):
                    continue
                reply: Dict[str, Any] = {"type": "pong",
                                         "ts": events.now_iso()}
                probe_id = payload.get("probe_id")
                if isinstance(probe_id, str) and probe_id:
                    reply["probe_id"] = probe_id[:64]
                await socket.send_text(events.encode(reply))
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — any transport fault = closed
            pass
        finally:
            closed.set()

    reader_task = asyncio.create_task(reader())

    async def next_event() -> Optional[Dict[str, Any]]:
        """The next bus event, or None on idle-timeout (send a
        heartbeat) or disconnect (the reader's close signal fires the
        race immediately — the relay must not sit in queue.get() past a
        vanished client or a shutdown)."""
        getter = asyncio.ensure_future(queue.get())
        closer = asyncio.ensure_future(closed.wait())
        done = set()
        try:
            done, _ = await asyncio.wait(
                {getter, closer}, timeout=WS_HEARTBEAT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (closer, getter):
                if task not in done:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        if closer in done:
            return None
        if getter in done:
            return getter.result()
        return None  # idle timeout

    try:
        await socket.send_text(events.encode({
            "type": "hello", "active": RUNS.ids(),
            "ts": events.now_iso()}))
        while not closed.is_set():
            event = await next_event()
            if event is None:
                if closed.is_set():
                    break
                event = {"type": "heartbeat", "ts": events.now_iso()}
            await socket.send_text(events.encode(event))
    except Exception:  # noqa: BLE001 — the socket died mid-relay
        pass
    finally:
        closed.set()
        events.unsubscribe(queue)
        reader_task.cancel()
        with contextlib.suppress(BaseException):
            await reader_task
        with contextlib.suppress(Exception):
            await socket.close()


# How long /stop waits for a cancelled run's unwind to finish before
# answering anyway (see stop()). The normal unwind — the CancelledError
# propagation plus the whole-chain settle — lands well inside a second;
# the window only bounds a wedged unwind (a sync block the event loop
# cannot interrupt), after which the cancel stays in effect and the
# settle lands late (the UI's poll loop catches it).
STOP_SETTLE_WAIT_SEC = 10.0


@app.post("/stop", response_model=StopResponse)
async def stop(req: StopRequest) -> StopResponse:
    if req.thread_id is not None:
        task = RUNS.get(req.thread_id)
        targets: Dict[str, Any] = (
            {req.thread_id: task} if task is not None else {})
    else:
        targets = RUNS.snapshot()
    for run_task in targets.values():
        run_task.cancel()
    # THE CONFIRMED STOP — wait for the cancellations to land before
    # answering: each cancelled /run_one unwinds at its current await and
    # its CancelledError handler settles the whole chain (state rows
    # failed/skipped through settle_thread, node lanes through the
    # graphs' unwind handlers), so when this response reaches the client
    # the rows are already terminal and ONE refresh draws the settled
    # chain. The node therefore leaves its live appearance only at the
    # confirmed instant — never on the client's own initiative (the UI
    # must not grey the running node off a stop request the server has
    # not confirmed yet).
    if targets:
        await asyncio.wait(
            list(targets.values()), timeout=STOP_SETTLE_WAIT_SEC)
    return StopResponse(stopped=len(targets), thread_ids=sorted(targets))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="oxpicious deep-agents HTTP server (FastAPI)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    # Liveness heartbeat — same contract as llm_agents.mcp: a
    # monitor-area resident now (2026-11 resident migration; the
    # monitor's supervisor starts/revives it, scope passed explicitly
    # since the service left MONITORED_SERVICES). Must stay registered
    # for the server's whole life; the exit hooks record the stop on
    # shutdown.
    register_service(SERVICE, scope="database", sub_scope="stream")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
