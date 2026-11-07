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
  POST /stop  {thread_id?} — cancel the in-flight run(s): the named
              thread, or every active run when thread_id is absent (the
              API bridge sends the absent form — its Stop button stops
              every active run; how many may run at once is the
              service_config max_parallel_runs cap, enforced bridge-side).
              The cancelled /run settles its thread (runner.settle_thread:
                in_progress → failed 'stopped by user', pending → skipped)
                and returns stopped: true. → {stopped, thread_ids}.
  POST /threads/:thread_id/steps — EXTERNAL TOPOLOGY CHANGE: add steps
                to a RUNNING thread's main lane from outside the
                conversations (operator tooling / another service), body
                {add_steps: [{node, title, notes?, delegate_goal?}],
                after_ordinal?} — the same vocabulary and caps as the
                model's own update_plan adds, add-only and top-lane-only:
                history rows never move (later rows shift ordinals only),
                other paths' future nodes (pending steps, fork subtrees)
                are kept, and the running graph executes the new nodes on
                its next hop (the router re-reads the living tree between
                hops). 409 when the thread is not running here (or its
                plan has not persisted yet), 400 with the actionable
                refusal on a validation/cap breach, 200 with the added
                rows. The inserts publish state_created per row — the
                UI's live canvas rerenders the new nodes instantly (the
                /ws chain).
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
import uuid  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402
from typing import Any, Dict, List, Optional, Union  # noqa: E402

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
from llm_agents.deep_agents.core.mcp import (  # noqa: E402
    DEFAULT_MCP_URL, McpToolClient,
)
from llm_agents.deep_agents.core.provider import ZhipuChatProvider  # noqa: E402

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


class StopRequest(BaseModel):
    """Stop the named in-flight run, or every active one when absent."""

    thread_id: Optional[str] = None


class StopResponse(BaseModel):
    """How many runs the stop actually cancelled (0 = none was running)."""

    stopped: int
    thread_ids: List[str] = Field(default_factory=list)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    global _provider
    _provider = ZhipuChatProvider()
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
    names, title required, optional notes / fork goal)."""

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
    own defaults (the row's key/endpoint/model do not leak across)."""
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
    mcp = McpToolClient(url=DEFAULT_MCP_URL)
    async with mcp:
        try:
            outcome = await run_thread(
                provider, mcp, goal=req.goal, data=req.data,
                model=model, max_steps=req.max_steps,
                system=req.system, temperature=req.temperature,
                thread_id=thread_id,
                plan_template_id=req.plan_template_id)
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
    return StopResponse(stopped=len(targets), thread_ids=sorted(targets))


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser(
        description="oxpicious deep-agents HTTP server (FastAPI)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()

    # Liveness heartbeat for database/stream_watchdog.py — same contract
    # as llm_agents.mcp: the watchdog starts this service when no
    # heartbeat row exists and restarts it when the beat goes stale.
    # Must stay registered for the server's whole life; the exit hooks
    # record the stop on shutdown.
    register_service(SERVICE)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
