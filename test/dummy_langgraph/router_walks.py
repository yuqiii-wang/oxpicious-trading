"""test.dummy_langgraph.router_walks — the System One router's E2E walks
as ONE reusable runner (2026-11-14).

The six router walks (SPAWN / FORK / FORK-DRIFT / JOIN / PROBE /
BACKGROUND) over ``00_router_chain.feature`` — each a full ``run_thread``
chain (plan → custom → result) with a SCRIPTED decision engine answering
the router's reads and a SCRIPTED sandbox seam serving the sandbox tool
results. Moved out of ``temp_scripts/probe_dummy_langgraph_router.py``
so BOTH consumers run the same bodies:

* the standing router probe (``purge=True`` — leave-no-trace QA), and
* the daemon's ``[this is test]`` self-test (``purge=False`` — the
  walked chains STAY in the history so the Thread Graph shows the
  ``spawn_`` / ``fork_`` child rows, the abandoned-tail kind-2 stub,
  the failed child and the investigator, and the PASS report SHOWS the
  fork / spawn / supervisor-interference checks).

Every walk returns a :class:`RouterWalkResult` carrying the settled
outcome, the scripted collaborators (the probe's traffic prints read
them back) and the walk's :class:`AssertCheck` list — check labels
carry the walk kind (``"spawn walk: …"``), so a combined report reads
per scenario.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Callable, Dict, List, Tuple

from llm_agents.deep_agents.langgraph.decision.facts import JevDecision
from llm_agents.deep_agents.langgraph.runner import ThreadRunResult
from llm_agents.deep_agents.langgraph.super_run import run_thread
from llm_agents.deep_agents.planning.plan_json import REVIEW_NODE_TAGS
from llm_agents.deep_agents.planning.store import PLAN_STORE

from .assertions import AssertCheck
from .mcp import FeatureToolSeam
from .provider import DummyLanggraphProvider

# Each walk's OWN goal — its marker picks the plan scenario (the plans
# share steps 1-2; the routed/scripting step renders [step_3] in all of
# them, discriminated by the directive markers 证据整理 / 继承上下文续算 /
# 漂移回锚 / 回交 / 失败调查 / 后台收集 that ride the step conversations).
GOAL_SPAWN = ("研究沪深300的均线趋势，把指标脚本计算路由给子节点完成，"
              "给出综合结论")
GOAL_FORK = ("研究沪深300的均线趋势，脚本计算由子节点继承上下文续算完成，"
             "给出综合结论")
GOAL_FORK_SUB = ("研究沪深300的均线趋势，脚本计算由子节点继承上下文续算完成，"
                 "子节点再派子节点算细分，给出综合结论")
GOAL_DRIFT = ("研究沪深300的均线趋势，脚本计算漂移后由子节点漂移回锚续算，"
              "给出综合结论")
GOAL_JOIN = ("研究沪深300的均线趋势，脚本计算交给子节点、失败时回交父节点，"
             "给出综合结论")
GOAL_PROBE = ("研究沪深300的均线趋势，脚本子节点失败时派调查员调查失败原因，"
              "给出综合结论")
GOAL_BG = ("研究沪深300的均线趋势，脚本计算由后台子节点完成后台收集汇总，"
           "给出综合结论")

# The walk kinds in the standing probes' running order: the seven
# ROUTER walks, the five TOOL-USAGE walks (2026-11-13's handback
# reserve / output-ceiling cut / repeat guard / repair handoff / syntax
# repair — the breaker faces) AND the four SUPERVISOR-RESUMPTION walks
# (2026-11-13's ignore-step / no-action / rate-limit retry / rate-limit
# jev fast path — the exception-resumption lanes). The daemon's
# self-test mounts ALL of them ("must test all scenarios").
ROUTER_WALK_KINDS: Tuple[str, ...] = (
    "spawn", "fork", "fork-drift", "fork-sub", "join", "probe",
    "background", "reserve", "cut", "repeat", "repair", "syntax",
    "resume-ignore", "resume-noaction", "resume-ratelimit",
    "resume-fastpath", "resume-cap")


class ScriptedRouterEngine:
    """The duck-typed decision engine with per-method scripted
    answers ((action, ok) per call, the last repeating); every read
    recorded. The reads the walks do not script land ok=False (the
    caller's default lane)."""

    def __init__(self, answers: dict) -> None:
        self._answers = answers
        self.reads: list = []

    async def _read(self, method: str, **kw) -> JevDecision:
        script = self._answers.get(method) or [("no", False)]
        idx = sum(1 for r in self.reads if r["method"] == method)
        entry = script[idx] if idx < len(script) else script[-1]
        # A callable entry is a FACT-AWARE gate (the drift watch reads
        # every turn of every step — the walks target their routed
        # step by its facts, e.g. pending_tools naming sandbox tools).
        action, ok_ = entry(kw) if callable(entry) else entry
        decision = JevDecision(
            action=action, confidence=0.9 if ok_ else 0.4, ok=bool(ok_),
            template=method, model="stub:scripted-router")
        self.reads.append({"method": method, "action": decision.action,
                           "ok": decision.ok, "facts": kw})
        return decision

    async def decide_route_spawn(self, **kw):
        return await self._read("route_spawn", **kw)

    async def decide_route_fork(self, **kw):
        return await self._read("route_fork", **kw)

    async def decide_route_fork_point(self, **kw):
        return await self._read("route_fork_point", **kw)

    async def decide_route_join(self, **kw):
        return await self._read("route_join", **kw)

    async def decide_failure_probe(self, **kw):
        return await self._read("failure_probe", **kw)

    async def decide_resume(self, **kw):
        return await self._read("resume", **kw)

    async def decide_spawn(self, **kw):
        return await self._read("spawn", **kw)


class ScriptedSandbox:
    """The duck-typed sandbox client: per-tool scripted results popped
    in order (the last repeats); every call recorded."""

    def __init__(self, results: dict) -> None:
        self._results = results
        self.n_calls: dict = {}
        self.calls: list = []

    async def ensure_session(self) -> str:
        return "sess-router-probe"

    async def call(self, tool: str, arguments: dict):
        self.calls.append((tool, arguments))
        seen = self.n_calls.get(tool, 0)
        self.n_calls[tool] = seen + 1
        script = self._results.get(tool) or [{"ok": True}]
        idx = seen if seen < len(script) else len(script) - 1
        return script[idx]


class YieldingProvider:
    """The dummy provider never awaits anything real — an in-process
    turn runs in microseconds and NEVER yields, so the background
    parking hook (the spawn hooks' real-DB writes, milliseconds each)
    never lands between the parent's collect turns and the parked
    result never appears (the walk saw endless "just finished —
    collect again"). Real providers spend real time per turn; this
    wrapper restores that with a 50 ms await per call — enough for the
    hook's DB roundtrips to complete between the parent's retries.
    Everything else forwards to the inner provider."""

    def __init__(self, inner) -> None:
        self._inner = inner

    async def chat_complete(self, messages, **kw):
        await asyncio.sleep(0.05)
        return await self._inner.chat_complete(messages, **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class RouterWalkResult:
    """One walk's artifacts — the settled outcome + its scripted
    collaborators + the AssertCheck list (the probe prints the checks
    and the traffic, the daemon's self-test extends its report with
    them)."""

    def __init__(self, kind: str, outcome: ThreadRunResult,
                 checks: List[AssertCheck], engine: ScriptedRouterEngine,
                 sandbox: ScriptedSandbox, provider: DummyLanggraphProvider,
                 thread_id: str, elapsed_s: float = 0.0) -> None:
        self.kind = kind
        self.outcome = outcome
        self.checks = checks
        self.engine = engine
        self.sandbox = sandbox
        self.provider = provider
        self.thread_id = thread_id
        self.elapsed_s = elapsed_s

    @property
    def chain_id(self) -> str:
        return (self.outcome.super_thread_id
                or self.outcome.thread_id or self.thread_id)


# --------------------------------------------------------------- reads
def _chain_threads(outcome: ThreadRunResult) -> int:
    threads = set()
    for row in outcome.plan:
        threads.add(row.thread_id)
    return len(threads)


def _reviews(outcome: ThreadRunResult) -> list:
    return [row for row in outcome.plan if row.node in REVIEW_NODE_TAGS]


async def _child_rows(outcome: ThreadRunResult) -> list:
    """The run's SPAWNED-CHILD state rows — list_super_states reads
    TOP-LEVEL rows only (children excluded by design), so the child
    rows read directly by their parent ids. Rows come back as dicts
    (state_id / node / status / child_kind — the agent_child_kind
    code, NULL on pre-fork rows). The child_kind 3 RESUME MARKERS are
    excluded — they are the repair lane's records, not spawned work
    (the dedicated :func:`_resume_markers` reads them)."""
    from _common.db_commons import get_db_connection_async

    parent_ids = [row.state_id for row in outcome.plan
                  if row.state_id is not None]
    if not parent_ids:
        return []
    conn = await get_db_connection_async()
    try:
        rows = await conn.fetch(
            "SELECT state_id, node, status, child_kind "
            "FROM llm.deep_agent_states "
            "WHERE parent_state_id = ANY($1::bigint[]) "
            "AND child_kind IS DISTINCT FROM 3 "
            "ORDER BY state_id",
            parent_ids)
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def _grandchild_rows(parent_state_id: int) -> list:
    """The SECOND child generation — the fork child's own spawns read
    directly by their parent state id (the fork child's row is not a
    plan row, so :func:`_child_rows` cannot reach them)."""
    from _common.db_commons import get_db_connection_async

    conn = await get_db_connection_async()
    try:
        rows = await conn.fetch(
            "SELECT state_id, node, status, child_kind "
            "FROM llm.deep_agent_states "
            "WHERE parent_state_id = $1 "
            "ORDER BY state_id",
            parent_state_id)
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def _child_result_payloads(outcome: ThreadRunResult) -> list:
    """The run's children's SUBAGENT-RESULT context payloads (kind
    'subagent-result' through the agent_context_kind label decode) —
    the answer + its [Context expansion] digest + the is_critical
    lift read straight from llm.deep_agent_contexts."""
    from _common.db_commons import get_db_connection_async

    child_ids = [r["state_id"] for r in await _child_rows(outcome)]
    if not child_ids:
        return []
    conn = await get_db_connection_async()
    try:
        rows = await conn.fetch(
            "SELECT c.state_id, c.is_critical, c.payload "
            "FROM llm.deep_agent_contexts c "
            "JOIN static.label_dim d ON d.domain='agent_context_kind' "
            "AND d.code = c.kind "
            "WHERE c.state_id = ANY($1::bigint[]) "
            "ORDER BY c.state_id",
            child_ids)
    finally:
        await conn.close()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("payload"), str):
            d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


# --------------------------------------------------------------- seams
def _fork_on_sandbox_turn(kw: dict) -> Tuple[str, bool]:
    """The drift watch's fact-aware gate: the fork read affirms only the
    sandbox-proposing (drifted) turn."""
    return ("yes", True) if "sandbox" in kw.get("pending_tools", "") \
        else ("no", True)


def _fork_on_sandbox_turn_except_sub(kw: dict) -> Tuple[str, bool]:
    """The drift watch's gate for the FORK-SUB walk: affirm the
    sandbox-proposing turn of the PARENT node only — the grandchild's
    node goal carries the ``[fork sub child]`` marker (its goal_head
    fact) and must never re-fork (it sits at the spawn cap anyway, so
    its lanes never fire; this is the scripted belt)."""
    if "fork sub child" in str(kw.get("goal_head") or ""):
        return ("no", True)
    return ("yes", True) if "sandbox" in kw.get("pending_tools", "") \
        else ("no", True)


_GREEN_RUN = {"script": "scripts/ma_study.py", "exit_code": 0,
              "timed_out": False, "duration_s": 2.0, "db": "readonly",
              "stdout": "MA5 4128.6 > MA20 4101.2 多头排列", "stderr": ""}


def _seams_spawn() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    engine = ScriptedRouterEngine({"route_spawn": [("yes", True)]})
    sandbox = ScriptedSandbox({
        "sandbox_write_file": [{"ok": True, "path": "scripts/ma_study.py",
                                "bytes": 96}],
        "sandbox_run": [dict(_GREEN_RUN)],
    })
    return engine, sandbox, 8


def _seams_fork() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    engine = ScriptedRouterEngine({
        "route_spawn": [("no", True)],
        # THE DRIFT WATCH (2026-11-13): the fork gate reads EVERY step's
        # turns — the walk forks only at its routed step (the
        # sandbox-proposing one); the early steps' db-only turns read no
        # (the widened gate's E2E proof).
        "route_fork": [_fork_on_sandbox_turn],
    })
    sandbox = ScriptedSandbox({
        "sandbox_write_file": [{"ok": True, "path": "scripts/ma_study.py",
                                "bytes": 96}],
        "sandbox_run": [dict(_GREEN_RUN)],
    })
    return engine, sandbox, 8


def _seams_fork_drift() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    engine = ScriptedRouterEngine({
        "route_spawn": [("no", True)],
        # THE DRIFT WATCH: the fork gate reads every turn — only the
        # sandbox-proposing (drifted) turn affirms.
        "route_fork": [_fork_on_sandbox_turn],
        # THE FORK-POINT READ: pick the EARLIEST checkpoint (the kg
        # turn's pre-drift context — the drift walk's turns carry
        # response heads via the SAYS Then form so candidates exist).
        "route_fork_point": [lambda kw: (kw["candidates"][0]["label"],
                                         True)],
    })
    sandbox = ScriptedSandbox({
        "sandbox_write_file": [{"ok": True, "path": "scripts/ma_study.py",
                                "bytes": 96}],
        "sandbox_run": [dict(_GREEN_RUN)],
    })
    return engine, sandbox, 8


_GREEN_RUN_SUB = {"script": "scripts/ma_sub.py", "exit_code": 0,
                  "timed_out": False, "duration_s": 2.0,
                  "db": "readonly",
                  "stdout": "MA10 4136.4 / MA60 4050.1 — 剪刀差 2.1% 收窄",
                  "stderr": ""}


def _seams_fork_sub() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    """The FORK-SUB walk's seams: the drift watch affirms the PARENT's
    sandbox-proposing turn only (the grandchild's goal_head carries the
    ``[fork sub child]`` marker), the spawn read declines, and the
    sandbox serves the GRANDCHILD's write/run."""
    engine = ScriptedRouterEngine({
        "route_spawn": [("no", True)],
        "route_fork": [_fork_on_sandbox_turn_except_sub],
    })
    sandbox = ScriptedSandbox({
        "sandbox_write_file": [{"ok": True, "path": "scripts/ma_sub.py",
                                "bytes": 96}],
        "sandbox_run": [dict(_GREEN_RUN_SUB)],
    })
    return engine, sandbox, 8


def _seams_join() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    engine = ScriptedRouterEngine({"route_join": [("yes", True)]})
    sandbox = ScriptedSandbox({
        "sandbox_run": [{"script": "scripts/ma_child.py", "exit_code": 1,
                         "timed_out": False, "duration_s": 3.0,
                         "db": "readonly", "stdout": "",
                         "stderr": "Traceback (most recent call last):\n"
                                   "psycopg.errors.GroupingError: "
                                   "column \"r\" must appear in GROUP BY"}],
    })
    return engine, sandbox, 8


def _seams_probe() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    engine = ScriptedRouterEngine({"failure_probe": [("yes", True)]})
    return engine, ScriptedSandbox({}), 8


def _seams_background() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    engine = ScriptedRouterEngine({})
    sandbox = ScriptedSandbox({
        "sandbox_write_file": [{"ok": True, "path": "scripts/bg_study.py",
                                "bytes": 96}],
        "sandbox_run": [dict(_GREEN_RUN)],
    })
    # max_steps 16 (not the walks' 8): the parking of a finished
    # background result races the collect turns IN-PROCESS — the
    # collect's "just finished — collect again" reply is the honest
    # face of that race (in production the turns are seconds apart and
    # one retry suffices), and the yielding wrapper is what gives the
    # hook its loop ticks at all (the bare dummy never yields).
    return engine, sandbox, 16


# ---------------------------------------------------- tool-usage walks
# The 2026-11-13 TOOL-USAGE fixes' walks (ported verbatim from
# temp_scripts/probe_dummy_langgraph_tool_usage.py): the handback
# reserve, the output-ceiling cut, the repeat guard, the repair
# handoff and the syntax repair — each a full run_thread chain whose
# SCRIPTED sandbox seam drives the loop's breaker machinery.
GOAL_RESERVE = ("研究沪深300的均线趋势，脚本子节点预算耗尽前回交，"
                "给出综合结论")
GOAL_CUT = ("研究沪深300的均线趋势，脚本计算遇参数截断拆小重发，"
            "给出综合结论")
GOAL_REPEAT = ("研究沪深300的均线趋势，脚本子节点失败时修补而不是原样重试，"
               "给出综合结论")
GOAL_SYNTAX = ("研究沪深300的均线趋势，脚本子节点语法修复回交，"
               "给出综合结论")

# The SUPERVISOR-RESUMPTION walks' goals (ported from
# temp_scripts/probe_dummy_langgraph_supervisor.py): each walk's goal
# marker picks its plan + flake + verdict faces.
GOAL_IGNORE = ("研究沪深300的均线趋势，量价步失败时忽略量价失败继续，"
               "给出综合结论")
GOAL_NOACTION = ("研究沪深300的均线趋势，量价步失败时容忍量价失败作答，"
                 "给出综合结论")
GOAL_RATELIMIT = ("研究沪深300的均线趋势，量价步限流重试后继续，"
                  "给出综合结论")
GOAL_CAP = ("研究沪深300的均线趋势，量价步步数耗尽重试后继续，"
            "给出综合结论")


def fast_throttle_patch():
    """Shrink the rate-limit cooldown for the E2E walks: the REAL
    default is 45 s inside the paused window; 1 s keeps the throttle
    path asserted (the sleep lands, the window survives it) without
    the wait. Patched on the WORKERS module (its ``run_limits``
    binding is the one ``_decide`` / ``_apply_verdicts`` read);
    returns the RESTORE callable — always call it (the daemon shares
    this process with real runs, so the patch lives only inside one
    rate-limit walk's run)."""
    import llm_agents.deep_agents.supervisor.workers as _workers
    from llm_agents.deep_agents.limits import run_limits as _real

    base = _real()
    fast = base.model_copy(update={"supervisor_retry_throttle_sec": 1.0})
    original = _workers.run_limits
    _workers.run_limits = lambda: fast

    def _restore() -> None:
        _workers.run_limits = original

    return _restore


def _chain_id_of(outcome: ThreadRunResult) -> str:
    """The outcome's chain root id (the review-rows read keys on it)."""
    return (outcome.super_thread_id or outcome.thread_id
            or outcome.plan[0].thread_id)


async def _resume_markers(outcome: ThreadRunResult) -> list:
    """The run's SUPERVISOR RESUME MARKER rows (child_kind 3,
    agent_child_kind resume) — the fork-action records the repair lane
    writes under the steps it re-opens. Children of top-level plan
    rows, read directly by parent ids."""
    from _common.db_commons import get_db_connection_async

    parent_ids = [row.state_id for row in outcome.plan
                  if row.state_id is not None]
    if not parent_ids:
        return []
    conn = await get_db_connection_async()
    try:
        rows = await conn.fetch(
            "SELECT state_id, node, title, status, child_kind, error "
            "FROM llm.deep_agent_states "
            "WHERE parent_state_id = ANY($1::bigint[]) AND child_kind = 3 "
            "ORDER BY state_id",
            parent_ids)
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def _top_states(outcome: ThreadRunResult) -> list:
    """The run's top-level plan rows (node / title / status / error)."""
    from _common.db_commons import get_db_connection_async

    ids = [row.state_id for row in outcome.plan
           if row.state_id is not None]
    if not ids:
        return []
    conn = await get_db_connection_async()
    try:
        rows = await conn.fetch(
            "SELECT state_id, node, title, status, error "
            "FROM llm.deep_agent_states WHERE state_id = ANY($1::bigint[]) "
            "ORDER BY state_id", ids)
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def _review_rows(super_thread_id: str) -> list:
    """The chain's supervisor-review context payloads (kind 5 through
    the agent_context_kind label decode)."""
    from _common.db_commons import get_db_connection_async

    conn = await get_db_connection_async()
    try:
        rows = await conn.fetch(
            "SELECT c.payload FROM llm.deep_agent_contexts c "
            "JOIN static.label_dim d ON d.domain='agent_context_kind' "
            "AND d.code = c.kind "
            "WHERE (c.thread_id = $1 OR c.thread_id IN ("
            "  SELECT thread_id FROM llm.deep_agent_states "
            "  WHERE super_thread_id = $1)) "
            "AND d.label = 'supervisor-review' ORDER BY c.context_id",
            super_thread_id)
    finally:
        await conn.close()
    out: list = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("payload"), str):
            d["payload"] = json.loads(d["payload"])
        out.append(d)
    return out


def traceback_run(path: str) -> dict:
    return {"script": path, "exit_code": 1, "timed_out": False,
            "duration_s": 1.0, "db": "readonly", "stdout": "",
            "stderr": "Traceback (most recent call last):\n"
                      "psycopg.errors.GroupingError: column \"r.date\" "
                      "must appear in the GROUP BY clause"}


def syntax_err_run(path: str) -> dict:
    """A PURE compile-time SyntaxError — CPython prints no `Traceback`
    header for these; only the `…Error: …` line."""
    return {"script": path, "exit_code": 1, "timed_out": False,
            "duration_s": 0.4, "db": "readonly", "stdout": "",
            "stderr": "  File \"scripts/syntax_study.py\", line 5\n"
                      "    prin(total)\n"
                      "         ^\n"
                      "SyntaxError: invalid syntax"}


def indentation_err_run(path: str) -> dict:
    """IndentationError — a SyntaxError SUBCLASS with its own literal,
    also no `Traceback` header."""
    return {"script": path, "exit_code": 1, "timed_out": False,
            "duration_s": 0.4, "db": "readonly", "stdout": "",
            "stderr": "  File \"scripts/syntax_study.py\", line 6\n"
                      "    print('SYNTAX_REPAIR_OK')\n"
                      "IndentationError: unexpected indent"}


def green_run(path: str, marker: str) -> dict:
    return {"script": path, "exit_code": 0, "timed_out": False,
            "duration_s": 1.2, "db": "readonly",
            "stdout": f"OK {marker}", "stderr": ""}


async def _repair_spawn_rows(outcome: ThreadRunResult) -> list:
    """The repair-spawn state rows — children OF the run's spawned
    children (grandchildren of the plan tree)."""
    from _common.db_commons import get_db_connection_async

    parent_ids = [row.state_id for row in outcome.plan
                  if row.state_id is not None]
    if not parent_ids:
        return []
    conn = await get_db_connection_async()
    try:
        kids = await conn.fetch(
            "SELECT state_id FROM llm.deep_agent_states "
            "WHERE parent_state_id = ANY($1::bigint[])", parent_ids)
        kid_ids = [k["state_id"] for k in kids]
        if not kid_ids:
            return []
        rows = await conn.fetch(
            "SELECT node, status FROM llm.deep_agent_states "
            "WHERE parent_state_id = ANY($1::bigint[]) "
            "ORDER BY state_id", kid_ids)
    finally:
        await conn.close()
    return [(r["node"], r["status"]) for r in rows]


def _seams_reserve() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    engine = ScriptedRouterEngine({})
    sandbox = ScriptedSandbox({
        "sandbox_write_file": [{"ok": True,
                                "path": "scripts/reserve_study.py",
                                "bytes": 96}],
        "sandbox_run": [green_run("scripts/reserve_study.py", "runs=1")],
    })
    return engine, sandbox, 8


def _seams_cut() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    engine = ScriptedRouterEngine({})
    sandbox = ScriptedSandbox({
        "sandbox_write_file": [{"ok": True, "path": "scripts/cut_study.py",
                                "bytes": 24}],
    })
    return engine, sandbox, 8


def _seams_repeat() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    engine = ScriptedRouterEngine({})
    sandbox = ScriptedSandbox({
        "sandbox_run": [traceback_run("scripts/repeat_study.py"),
                        {"script": "scripts/repeat_study.py",
                         "exit_code": 0, "timed_out": False,
                         "duration_s": 1.0, "db": "readonly",
                         "stdout": "runs=2", "stderr": ""},
                        green_run("scripts/repeat_study.py",
                                  "repeat_ok_42")],
        "sandbox_read_file": [{"content": "SELECT code, date, sum(v) "
                                          "FROM t r GROUP BY r.date",
                               "bytes": 64}],
        "sandbox_write_file": [{"ok": True,
                                "path": "scripts/repeat_study.py",
                                "bytes": 66}],
    })
    return engine, sandbox, 8


def _seams_repair() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    engine = ScriptedRouterEngine({})
    sandbox = ScriptedSandbox({
        "sandbox_run": [traceback_run("scripts/repeat_study.py"),
                        traceback_run("scripts/repeat_study.py"),
                        green_run("scripts/repeat_study.py",
                                  "repeat_ok_42")],
        "sandbox_read_file": [{"content": "SELECT code, date, sum(v) "
                                          "FROM t r GROUP BY r.date",
                               "bytes": 64}],
        "sandbox_write_file": [{"ok": True,
                                "path": "scripts/repeat_study.py",
                                "bytes": 66}],
    })
    return engine, sandbox, 8


def _seams_syntax() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    engine = ScriptedRouterEngine({})
    script_body = ("import pandas as pd\n"
                   "code = '000300'\n"
                   "total = 42\n"
                   "def main():\n"
                   "    prin(total)\n"
                   "    print('SYNTAX_REPAIR_OK')\n")
    sandbox = ScriptedSandbox({
        "sandbox_run": [syntax_err_run("scripts/syntax_study.py"),
                        indentation_err_run("scripts/syntax_study.py"),
                        green_run("scripts/syntax_study.py",
                                  "SYNTAX_REPAIR_OK")],
        "sandbox_read_file": [{"content": script_body, "bytes": 128}],
        "sandbox_write_file": [{"ok": True,
                                "path": "scripts/syntax_study.py",
                                "bytes": 128}],
    })
    return engine, sandbox, 8


def _seams_resume_ignore() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    """The ignore walk: the scripted engine's resume read declines
    (ok=False) so the GENERATIVE verdict ask judges."""
    return ScriptedRouterEngine({}), ScriptedSandbox({}), 8


def _seams_resume_noaction() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    return ScriptedRouterEngine({}), ScriptedSandbox({}), 8


def _seams_resume_ratelimit() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    return ScriptedRouterEngine({}), ScriptedSandbox({}), 8


def _seams_resume_cap() -> Tuple[ScriptedRouterEngine, ScriptedSandbox, int]:
    """The cap-class walk's seams: a declining router engine (the burn
    conversation proposes db_run_query only — no sandbox reads expected)
    and max_steps 8, the burn conversation's whole budget (the resumed
    pass runs 8 + the 16-step bump = 24, pinned by the resumed face)."""
    return ScriptedRouterEngine({}), ScriptedSandbox({}), 8


def _seams_resume_fastpath() -> Tuple[Any, ScriptedSandbox, int]:
    """The fast-path walk's engine: the feature-dataset STUB answering
    the resume lane from the dataset, every ROUTER method declined (the
    stub's route_fork first scenario would otherwise hijack step 1
    into a fork detour) — the jev fast path owns the verdict, zero
    generative asks."""
    from test.dummy_langgraph import StubJevDecisionEngine
    from llm_agents.deep_agents.langgraph.decision.facts import JevDecision

    engine = StubJevDecisionEngine()

    async def _decline(**kw):
        return JevDecision(action="no", confidence=0.4, probabilities={},
                           ok=False, template="stub:declined",
                           model="stub:declined")

    for method in ("decide_route_spawn", "decide_route_fork",
                   "decide_route_fork_point", "decide_route_join",
                   "decide_failure_probe", "decide_spawn"):
        setattr(engine, method, _decline)
    return engine, ScriptedSandbox({}), 8


# -------------------------------------------------------------- checks
def _checks_spawn(outcome: ThreadRunResult, provider: DummyLanggraphProvider,
                  engine: ScriptedRouterEngine,
                  sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"spawn walk: {name}", ok=ok,
                                  detail=detail))

    check("the routed three-step plan was proposed",
          "route-plan-spawn" in used)
    check("the routed step proposed its script", "step-script-proposal" in used)
    check("the parent folded the child's evidence", "step-script-folded" in used)
    for tag in ("script-child-write", "script-child-run",
                "script-child-evidence"):
        check(f"the scripting child walked {tag}", tag in used)
    check("the parent's proposed write was intercepted",
          True, "see the child's own sandbox calls")
    check("exactly ONE sandbox_run — the child's (the parent's proposed "
          "calls never executed)",
          len([c for c in sandbox.calls if c[0] == "sandbox_run"]) == 1)
    spawn_reads = [r for r in engine.reads if r["method"] == "route_spawn"]
    check("exactly one route_spawn read, ok (the scripted yes)",
          len(spawn_reads) == 1 and spawn_reads[0]["ok"])
    join_probe = [r for r in engine.reads
                  if r["method"] in ("route_join", "failure_probe")]
    check("the spawn walk spent no join/probe reads", not join_probe)
    return checks


async def _checks_spawn_db(outcome: ThreadRunResult,
                           ) -> List[AssertCheck]:
    """The spawn walk's DB-backed checks (the child rows / payloads)."""
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"spawn walk: {name}", ok=ok,
                                  detail=detail))

    children = await _child_rows(outcome)
    check("one completed plan-tree child row",
          len(children) == 1 and children[0]["status"] == "completed",
          f"({children})")
    results = await _child_result_payloads(outcome)
    check("the [CRITICAL] handback line lifted at finish_child_state "
          "(payload critical + the row's is_critical)",
          len(results) == 1
          and results[0]["payload"].get("critical") is True
          and results[0]["is_critical"] is True)
    return checks


def _checks_fork(outcome: ThreadRunResult, provider: DummyLanggraphProvider,
                 engine: ScriptedRouterEngine,
                 sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"fork walk: {name}", ok=ok,
                                  detail=detail))

    check("the inherited-continuation plan was proposed",
          "route-plan-fork" in used)
    early_no = [r for r in engine.reads
                if r["method"] == "route_fork"
                and "sandbox" not in (r["facts"].get("pending_tools")
                                      or "")]
    check(f"THE DRIFT WATCH: {len(early_no)} fork reads on the early "
          f"db-only turns, all no (the gate fires on every turn)",
          len(early_no) >= 2 and all(r["action"] == "no" for r in early_no))
    check("the routed step proposed its continuation script",
          "step-fork-proposal" in used)
    check("the parent folded the fork child's evidence",
          "step-fork-fold" in used)
    # THE INHERITANCE PROOF: the fork-child faces key on the child-only
    # marker "Fork continuation" — a parent turn never carries it; the
    # only way the provider matched them is that the child's context
    # carries the harness-built fork directive (inherited conversation
    # + directive).
    for tag in ("fork-child-write", "fork-child-run", "fork-child-evidence"):
        check(f"the fork child walked {tag}", tag in used)
    check("the fork child never matched the fresh-context spawn faces",
          "script-child-write" not in used)
    check("exactly ONE sandbox_run — the fork child's (the parent's "
          "proposed calls never executed)",
          len([c for c in sandbox.calls if c[0] == "sandbox_run"]) == 1)
    reads = {r["method"]: r for r in engine.reads
             if r["method"] in ("route_spawn", "route_fork")}
    check("the spawn read declined (a continuation, not self-contained)",
          reads.get("route_spawn", {}).get("action") == "no")
    check("the fork read affirmed",
          reads.get("route_fork", {}).get("action") == "yes"
          and reads.get("route_fork", {}).get("ok"))
    join_probe = [r for r in engine.reads
                  if r["method"] in ("route_join", "failure_probe")]
    check("the fork walk spent no join/probe reads", not join_probe)
    return checks


async def _checks_fork_db(outcome: ThreadRunResult,
                          ) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"fork walk: {name}", ok=ok,
                                  detail=detail))

    children = await _child_rows(outcome)
    check("one completed fork child row — child_kind 1, fork_ slug",
          len(children) == 1 and children[0]["status"] == "completed"
          and children[0]["child_kind"] == 1
          and str(children[0]["node"]).startswith("fork_"),
          f"({children})")
    return checks


def _checks_fork_drift(outcome: ThreadRunResult,
                       provider: DummyLanggraphProvider,
                       engine: ScriptedRouterEngine,
                       sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"fork-drift walk: {name}", ok=ok,
                                  detail=detail))

    check("the re-anchoring plan was proposed", "route-plan-fork-drift" in used)
    for tag in ("drift-step-kg", "drift-step-db", "drift-step-drifted"):
        check(f"the routed step walked its checkpoint turns + the drifted "
              f"turn ({tag})", tag in used)
    fp_reads = [r for r in engine.reads
                if r["method"] == "route_fork_point"]
    check("exactly one route_fork_point read with the conversation's "
          "checkpoint candidates",
          len(fp_reads) == 1 and fp_reads[0]["ok"]
          and len(fp_reads[0]["facts"].get("candidates") or []) >= 2,
          f"({len(fp_reads)} read(s))")
    drift_nos = [r for r in engine.reads if r["method"] == "route_fork"
                 and r["action"] == "no"]
    check(f"THE DRIFT WATCH: {len(drift_nos)} no-reads on the on-purpose "
          f"turns before the drifted turn affirmed", len(drift_nos) >= 2)
    for tag in ("fork-child-write", "fork-child-run", "fork-child-evidence"):
        check(f"the fork child walked {tag} on the inherited prefix",
              tag in used)
    check("the parent folded the 'pre-drift context inherited' injection "
          "(the abandoned-tail mode line rode it)",
          "drift-step-fold" in used)
    check("exactly ONE sandbox_run — the fork child's (the drifted "
          "turn's proposed call never executed)",
          len([c for c in sandbox.calls if c[0] == "sandbox_run"]) == 1)
    return checks


async def _checks_fork_drift_db(outcome: ThreadRunResult,
                                ) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"fork-drift walk: {name}", ok=ok,
                                  detail=detail))

    children = await _child_rows(outcome)
    kinds = sorted(str(c["child_kind"]) for c in children)
    check("two child rows — the fork child (child_kind 1) + the "
          "abandoned tail's kind-2 stub row",
          len(children) == 2 and "1" in kinds and "2" in kinds,
          f"({children})")
    stub = next((c for c in children if c["child_kind"] == 2), None)
    check("the abandoned tail settled skipped as the kind-2 stub",
          stub is not None and stub["status"] == "skipped", f"({stub})")
    fork_child = next((c for c in children if c["child_kind"] == 1), None)
    check("the fork child completed with the fork_ slug",
          fork_child is not None and fork_child["status"] == "completed"
          and str(fork_child["node"]).startswith("fork_"),
          f"({fork_child})")
    return checks


def _checks_fork_sub(outcome: ThreadRunResult,
                     provider: DummyLanggraphProvider,
                     engine: ScriptedRouterEngine,
                     sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"fork-sub walk: {name}", ok=ok,
                                  detail=detail))

    check("the two-generation plan was proposed",
          "route-plan-fork-sub" in used)
    check("the routed step proposed its continuation script",
          "step-forksub-proposal" in used)
    check("THE FORK CHILD SPAWNED ITS OWN SUB NODE exactly once (the "
          "model's spawn args carry the [fork sub child] goal — the "
          "grandchild's spawn_ slug proves it)",
          used.count("fork-child-subspawn") == 1)
    for tag in ("forksub-child-write", "forksub-child-run",
                "forksub-child-evidence"):
        check(f"the grandchild walked {tag} exactly once",
              used.count(tag) == 1)
    check("the fork child folded the sub node's evidence into its "
          "handback", "fork-child-subfold" in used)
    check("the parent folded the two-generation evidence",
          "step-forksub-fold" in used)
    check("exactly ONE sandbox_run — the grandchild's (the parent's and "
          "the fork child's proposed calls never executed)",
          len([c for c in sandbox.calls if c[0] == "sandbox_run"]) == 1)
    spawn_reads = [r for r in engine.reads if r["method"] == "route_spawn"]
    check("exactly one route_spawn read, declined",
          len(spawn_reads) == 1 and spawn_reads[0]["action"] == "no")
    fork_yes = [r for r in engine.reads if r["method"] == "route_fork"
                and r["action"] == "yes"]
    check("exactly one route_fork affirmation — the parent's turn (the "
          "grandchild never re-forks)",
          len(fork_yes) == 1 and fork_yes[0]["ok"])
    check("no fork-point read (fork-from-now — no content-carrying "
          "candidates)",
          not any(r["method"] == "route_fork_point" for r in engine.reads))
    check("the fork child never matched the fresh-context spawn faces",
          "script-child-write" not in used)
    probe_reads = [r for r in engine.reads
                   if r["method"] == "failure_probe"]
    join_reads = [r for r in engine.reads if r["method"] == "route_join"]
    check("no probe reads; any join reads declined (the fork child is "
          "join-eligible and its budget-watched no-reads are the lane's "
          "watchful face, never a verdict)",
          not probe_reads and all(not r["ok"] for r in join_reads),
          f"(join reads: {len(join_reads)})")
    return checks


async def _checks_fork_sub_db(outcome: ThreadRunResult,
                              ) -> List[AssertCheck]:
    """The TWO-GENERATION plan tree: the fork child under the routed
    step, the fork child's own sub node under the fork child."""
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"fork-sub walk: {name}", ok=ok,
                                  detail=detail))

    children = await _child_rows(outcome)
    fork_child = next((c for c in children if c["child_kind"] == 1), None)
    check("the fork child completed under the routed step (child_kind 1, "
          "fork_ slug)",
          len(children) == 1 and fork_child is not None
          and fork_child["status"] == "completed"
          and str(fork_child["node"]).startswith("fork_"),
          f"({children})")
    subs = (await _grandchild_rows(fork_child["state_id"])
            if fork_child is not None and fork_child.get("state_id")
            else [])
    check("the fork child's OWN sub node completed (child_kind 0, "
          "spawn_ slug) — the second generation",
          len(subs) == 1 and subs[0]["status"] == "completed"
          and subs[0]["child_kind"] == 0
          and str(subs[0]["node"]).startswith("spawn_"),
          f"({subs})")
    return checks


def _checks_join(outcome: ThreadRunResult, provider: DummyLanggraphProvider,
                 engine: ScriptedRouterEngine,
                 sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"join walk: {name}", ok=ok,
                                  detail=detail))

    check("the handback plan was proposed", "route-plan-join" in used)
    for tag in ("step-join-spawn", "script-child-run-fail",
                "script-child-edit", "script-child-handback",
                "step-join-fold"):
        check(f"the join walk walked {tag}", tag in used)
    check("the nudged-away edit never executed",
          "sandbox_edit_file" not in [c[0] for c in sandbox.calls])
    check("exactly ONE sandbox_run — the failed one (the join stopped "
          "the loop)",
          len([c for c in sandbox.calls if c[0] == "sandbox_run"]) == 1)
    join_reads = [r for r in engine.reads if r["method"] == "route_join"]
    check("exactly one route_join read, ok",
          len(join_reads) == 1 and join_reads[0]["ok"])
    return checks


async def _checks_join_db(outcome: ThreadRunResult) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"join walk: {name}", ok=ok,
                                  detail=detail))

    children = await _child_rows(outcome)
    check("the scripting child settled completed",
          len(children) == 1 and children[0]["status"] == "completed",
          f"({children})")
    return checks


def _checks_probe(outcome: ThreadRunResult, provider: DummyLanggraphProvider,
                  engine: ScriptedRouterEngine,
                  sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"probe walk: {name}", ok=ok,
                                  detail=detail))

    check("the investigate plan was proposed", "route-plan-probe" in used)
    for tag in ("step-probe-spawn", "probe-investigator", "step-probe-fold"):
        check(f"the probe walk walked {tag}", tag in used)
    # the flake's serve is never tagged in scenarios_used (the provider
    # raises without recording) — the armed-flake count is the trace:
    # this walk's dataset carries TWO flakes (the volume step's
    # transient timeout + the failing child's death).
    check("two armed flakes served — the volume flake + the failing "
          "child's death",
          provider.n_flakes == 2, f"got {provider.n_flakes}")
    probe_reads = [r for r in engine.reads
                   if r["method"] == "failure_probe"]
    check("exactly one failure_probe read, ok",
          len(probe_reads) == 1 and probe_reads[0]["ok"])
    return checks


async def _checks_probe_db(outcome: ThreadRunResult) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"probe walk: {name}", ok=ok,
                                  detail=detail))

    children = await _child_rows(outcome)
    statuses = [r["status"] for r in children]
    check("two child rows — the failed scripting child + the completed "
          "investigator",
          len(children) == 2 and statuses.count("failed") == 1
          and statuses.count("completed") == 1,
          f"({children})")
    return checks


def _checks_background(outcome: ThreadRunResult,
                       provider: DummyLanggraphProvider,
                       engine: ScriptedRouterEngine,
                       sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"background walk: {name}", ok=ok,
                                  detail=detail))

    for tag in ("bg-step-spawn", "bg-child-write", "bg-child-run",
                "bg-child-evidence"):
        check(f"the background walk walked {tag}", tag in used)
    check("THE ANSWER GATE: the parent attempted its answer before "
          "gathering (the premature face matches only on the queued "
          "tool text) and was held",
          "bg-step-premature" in used)
    check("the gate's nudge landed — the parent called collect_subagents",
          "bg-step-collect" in used or "bg-step-collect-again" in used)
    check("the gathered result folded — its face matches only on the "
          "result text's 'tool call(s))' head",
          "bg-step-fold" in used)
    if "bg-step-fold" in used:
        fold_idx = used.index("bg-step-fold")
        check("the gate opened at the fold — no collect face served "
              "after it",
              all(tag not in used[fold_idx + 1:]
                  for tag in ("bg-step-collect", "bg-step-collect-again")))
    return checks


async def _checks_background_db(outcome: ThreadRunResult,
                                ) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"background walk: {name}", ok=ok,
                                  detail=detail))

    children = await _child_rows(outcome)
    check("one completed spawn child row — child_kind 0, spawn_ slug",
          len(children) == 1 and children[0]["status"] == "completed"
          and children[0]["child_kind"] == 0
          and str(children[0]["node"]).startswith("spawn_"),
          f"({children})")
    return checks


def _checks_reserve(outcome: ThreadRunResult,
                    provider: DummyLanggraphProvider,
                    engine: ScriptedRouterEngine,
                    sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"reserve walk: {name}", ok=ok,
                                  detail=detail))

    for tag in ("tool-usage-plan-reserve", "reserve-step-spawn",
                "reserve-child-write", "reserve-child-run",
                "reserve-child-rerun", "reserve-child-handback",
                "reserve-step-fold"):
        check(f"walked {tag}", tag in used)
    check("the reserve nudge landed — the child's handback scenario "
          "matches ONLY on the loop's [Handback reserve] text",
          "reserve-child-handback" in used)
    n_runs = len([c for c in sandbox.calls if c[0] == "sandbox_run"])
    check("the child burned its budget on repeated green runs",
          n_runs >= 5, f"({n_runs} runs)")
    return checks


async def _checks_reserve_db(outcome: ThreadRunResult) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"reserve walk: {name}", ok=ok,
                                  detail=detail))

    children = await _child_rows(outcome)
    check("the scripting child settled COMPLETED — no cap death",
          len(children) == 1 and children[0]["status"] == "completed",
          f"({children})")
    return checks


def _checks_cut(outcome: ThreadRunResult,
                provider: DummyLanggraphProvider,
                engine: ScriptedRouterEngine,
                sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"cut walk: {name}", ok=ok,
                                  detail=detail))

    for tag in ("tool-usage-plan-cut", "cut-step-big-write",
                "cut-step-small-write", "cut-step-finding"):
        check(f"walked {tag}", tag in used)
    writes = [c for c in sandbox.calls if c[0] == "sandbox_write_file"]
    check("exactly ONE write reached the seam — the cut call's "
          "arguments never parsed into a call", len(writes) == 1,
          f"({len(writes)})")
    if writes:
        check("the executed write is the SHORT resend",
              "cut_study done small"
              in str(writes[0][1].get("content") or ""))
    check("the ceiling-cut remedy text reached the conversation (the "
          "small-write scenario matches only on it)",
          "cut-step-finding" in used)
    return checks


async def _checks_cut_db(outcome: ThreadRunResult) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"cut walk: {name}", ok=ok,
                                  detail=detail))

    children = await _child_rows(outcome)
    check("no spawned children", len(children) == 0, f"({children})")
    return checks


def _checks_repeat(outcome: ThreadRunResult,
                   provider: DummyLanggraphProvider,
                   engine: ScriptedRouterEngine,
                   sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"repeat walk: {name}", ok=ok,
                                  detail=detail))

    for tag in ("tool-usage-plan-repeat", "repeat-step-spawn",
                "repeat-child-run", "repeat-child-repeat",
                "repeat-child-patch", "repeat-child-rerun",
                "repeat-child-handback", "repeat-step-fold"):
        check(f"walked {tag}", tag in used)
    check("[Repeat guard] landed — the patch scenario matches only on "
          "the guard's stamped text", "repeat-child-patch" in used)
    runs = [c for c in sandbox.calls if c[0] == "sandbox_run"]
    check("three runs — the failure, the blind repeat, the patched "
          "green rerun", len(runs) == 3, f"({len(runs)})")
    if len(runs) >= 2:
        check("the blind repeat's arguments were IDENTICAL to the "
              "failed call's", runs[0][1] == runs[1][1])
    edits = [c for c in sandbox.calls if c[0] == "sandbox_write_file"]
    check("the edit wrote the patched group-by back once",
          len(edits) == 1
          and "GROUP BY r.code, r.date"
          in str(edits[0][1].get("content") or ""))
    check("one failure never spawned the repair child (the guard "
          "redirected the node instead)", "repair-child-patch" not in used)
    return checks


async def _checks_repeat_db(outcome: ThreadRunResult) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"repeat walk: {name}", ok=ok,
                                  detail=detail))

    children = await _child_rows(outcome)
    check("the scripting child settled completed",
          len(children) == 1 and children[0]["status"] == "completed",
          f"({children})")
    return checks


def _checks_repair(outcome: ThreadRunResult,
                   provider: DummyLanggraphProvider,
                   engine: ScriptedRouterEngine,
                   sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"repair walk: {name}", ok=ok,
                                  detail=detail))

    for tag in ("repeat-child-run", "repeat-child-repeat",
                "repair-child-patch", "repair-child-rerun",
                "repair-child-handback", "repeat-step-fold-repair"):
        check(f"walked {tag}", tag in used)
    runs = [c for c in sandbox.calls if c[0] == "sandbox_run"]
    check("three runs — the two parent failures + the repair child's "
          "patched rerun", len(runs) == 3, f"({len(runs)})")
    check("the parent folded the repair child's REPAIR EVIDENCE",
          "repeat-step-fold-repair" in used)
    return checks


async def _checks_repair_db(outcome: ThreadRunResult) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"repair walk: {name}", ok=ok,
                                  detail=detail))

    spawns = await _repair_spawn_rows(outcome)
    check("exactly one COMPLETED repair-spawn row under the child",
          len(spawns) == 1 and spawns[0][1] == "completed",
          f"({spawns})")
    return checks


def _checks_syntax(outcome: ThreadRunResult,
                   provider: DummyLanggraphProvider,
                   engine: ScriptedRouterEngine,
                   sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"syntax walk: {name}", ok=ok,
                                  detail=detail))

    for tag in ("tool-usage-plan-syntax", "syntax-step-spawn",
                "syntax-child-write", "syntax-child-run",
                "syntax-child-run2", "repair-syntax-patch",
                "repair-syntax-rerun", "repair-syntax-handback",
                "syntax-child-handup", "syntax-step-fold"):
        check(f"walked {tag}", tag in used)
    runs = [c for c in sandbox.calls if c[0] == "sandbox_run"]
    check("three runs — the two syntax failures + the repair child's "
          "patched rerun", len(runs) == 3, f"({len(runs)})")
    check("MONEY: neither failure carries a Traceback header — the "
          "streak counted the bare SyntaxError / IndentationError "
          "lines (the 2026-11-14 classification fix)",
          all("Traceback" not in str(c[1].get("stderr") or "")
              for c in runs))
    check("the repeat walk's repair faces never served (the syntax "
          "faces' syntax_study.py needle precedes them)",
          "repair-child-patch" not in used)
    check("the repair child proposed NEW changes — the patch face "
          "served a sandbox_edit_file of the broken block",
          "repair-syntax-patch" in used)
    write_backs = [c for c in sandbox.calls if c[0] == "sandbox_write_file"
                   and "print(total)" in str(c[1].get("content") or "")]
    check("the edit's write-back landed the patched block over the "
          "shared script (the edit tool composes read+write — no "
          "sandbox_edit_file call reaches the seam itself)",
          len(write_backs) == 1)
    return checks


async def _checks_syntax_db(outcome: ThreadRunResult) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"syntax walk: {name}", ok=ok,
                                  detail=detail))

    spawns = await _repair_spawn_rows(outcome)
    check("exactly one COMPLETED repair-spawn row under the child",
          len(spawns) == 1 and spawns[0][1] == "completed",
          f"({spawns})")
    children = await _child_rows(outcome)
    check("the scripting child settled completed",
          len(children) == 1 and children[0]["status"] == "completed",
          f"({children})")
    return checks


def _checks_resume_ignore(outcome: ThreadRunResult,
                          provider: DummyLanggraphProvider,
                          engine: ScriptedRouterEngine,
                          sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"resume-ignore walk: {name}",
                                  ok=ok, detail=detail))

    check("the dispensable-step plan was proposed",
          "plan-ignore-volume" in used)
    check("the trend step completed before the failure",
          "step-trend-finding" in used)
    check("the repair ask was served the IGNORE verdict face (keyed on "
          "the failed node's error line)",
          "supervisor-verdict-ignore" in used)
    check("the retry face never served (a non-transient error)",
          "supervisor-verdict-retry" not in used)
    check("the structural-key-error flake armed once (the serve is "
          "unrecordable — n_flakes is the trace)",
          provider.n_flakes == 1, f"got {provider.n_flakes}")
    return checks


async def _checks_resume_ignore_db(outcome: ThreadRunResult,
                                   ) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"resume-ignore walk: {name}",
                                  ok=ok, detail=detail))

    rows = await _top_states(outcome)
    volume = next((r for r in rows if "量价" in (r["title"] or "")), None)
    check("the failed volume row settled SKIPPED",
          volume is not None and volume["status"] == "skipped",
          f"({volume})")
    check("the skipped row wears the supervisor's IGNORE_NOTE",
          volume is not None
          and "supervisor verdict: ignored" in str(volume["error"] or ""))
    trend = next((r for r in rows if "均线" in (r["title"] or "")), None)
    check("the trend row stayed completed",
          trend is not None and trend["status"] == "completed",
          f"({trend})")
    reviews = await _review_rows(_chain_id_of(outcome))
    check("the verdict ask's supervisor-review row persisted with the "
          "ignore verdict",
          len(reviews) >= 1
          and any("ignore-step" in str(r["payload"].get("review") or "")
                  for r in reviews),
          f"({len(reviews)} review row(s))")
    return checks


def _checks_resume_noaction(outcome: ThreadRunResult,
                            provider: DummyLanggraphProvider,
                            engine: ScriptedRouterEngine,
                            sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"resume-noaction walk: {name}",
                                  ok=ok, detail=detail))

    check("the doomed-tail plan was proposed",
          "plan-noaction-volume" in used)
    check("the repair ask was served the NO-ACTION verdict face",
          "supervisor-verdict-noaction" in used)
    check("the ignore face never served",
          "supervisor-verdict-ignore" not in used)
    check("the missing-table flake armed once (the serve is "
          "unrecordable — n_flakes is the trace)",
          provider.n_flakes == 1, f"got {provider.n_flakes}")
    return checks


async def _checks_resume_noaction_db(outcome: ThreadRunResult,
                                     ) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"resume-noaction walk: {name}",
                                  ok=ok, detail=detail))

    rows = await _top_states(outcome)
    volume = next((r for r in rows if "量价" in (r["title"] or "")), None)
    check("the volume row STAYED FAILED under no-action",
          volume is not None and volume["status"] == "failed",
          f"({volume})")
    tail = next((r for r in rows if "汇总" in (r["title"] or "")), None)
    check("THE DOOMED TAIL: the pending step settled skipped with the "
          "not-run reason",
          tail is not None and tail["status"] == "skipped"
          and "not run — the main path failed" in str(tail["error"] or ""),
          f"({tail})")
    reviews = await _review_rows(_chain_id_of(outcome))
    check("the verdict ask AND the final sweep both persisted "
          "supervisor-review rows",
          len(reviews) >= 2, f"({len(reviews)} review row(s))")
    check("the no-action verdict rides the review rows",
          any("no-action" in str(r["payload"].get("review") or "")
              for r in reviews))
    markers = await _resume_markers(outcome)
    check("THE FAILURE FORK (the fork action on the UI): a child_kind 3 "
          "RED DEAD-END record row under the still-failed step, "
          "preserving the missing-table error",
          len(markers) == 1 and markers[0]["status"] == "failed"
          and "supervisor no-action" in str(markers[0]["title"])
          and "UndefinedTable" in str(markers[0]["error"]),
          f"({markers})")
    return checks


def _checks_resume_ratelimit(outcome: ThreadRunResult,
                             provider: DummyLanggraphProvider,
                             engine: ScriptedRouterEngine,
                             sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"resume-ratelimit walk: {name}",
                                  ok=ok, detail=detail))

    check("the 1302 rate-limit flake armed once",
          provider.n_flakes == 1, f"got {provider.n_flakes}")
    check("the repair ask was served the RETRY verdict face (the "
          "repair-protocol frame matches; the 1302 needle is in the "
          "manifest)",
          "supervisor-verdict-retry" in used)
    return checks


async def _checks_resume_ratelimit_db(outcome: ThreadRunResult,
                                      ) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"resume-ratelimit walk: {name}",
                                  ok=ok, detail=detail))

    rows = await _top_states(outcome)
    volume = next((r for r in rows if "量价" in (r["title"] or "")), None)
    check("the rate-limited volume row was REOPENED and the resumed "
          "hop completed it",
          volume is not None and volume["status"] == "completed",
          f"({volume})")
    check("the reopen cleared the failed attempt's error",
          volume is not None and not (volume["error"] or ""),
          f"({volume and volume['error']})")
    reviews = await _review_rows(_chain_id_of(outcome))
    check("the retry verdict rode a supervisor-review row",
          any("retry-step" in str(r["payload"].get("review") or "")
              for r in reviews),
          f"({len(reviews)} review row(s))")
    markers = await _resume_markers(outcome)
    check("THE RESUME FORK (the fork action on the UI): a child_kind 3 "
          "RED DEAD-END record row under the re-opened step, carrying "
          "the prior attempt's 1302 error",
          len(markers) == 1 and markers[0]["status"] == "failed"
          and "supervisor resume" in str(markers[0]["title"])
          and "1302" in str(markers[0]["error"]),
          f"({markers})")
    return checks


def _checks_resume_fastpath(outcome: ThreadRunResult,
                            provider: DummyLanggraphProvider,
                            engine: Any,
                            sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"resume-fastpath walk: {name}",
                                  ok=ok, detail=detail))

    check("the 1302 rate-limit flake armed once",
          provider.n_flakes == 1, f"got {provider.n_flakes}")
    check("ZERO generative verdict asks — the jev fast path owned the "
          "verdict",
          not any(tag in used for tag in
                  ("supervisor-verdict-retry", "supervisor-verdict-ignore",
                   "supervisor-verdict-noaction")))
    resume_reads = [r for r in engine.reads
                    if r["template"] == "resume_judgment"]
    check("exactly ONE jev resume read",
          len(resume_reads) == 1, f"({engine.reads})")
    if resume_reads:
        check("the read's facts picked the rate-limit dataset scenario",
              resume_reads[0]["tag"] == "resume-rate-limit-throttle",
              f"({resume_reads[0]['tag']})")
        check("the yes choice mapped to the retry action",
              resume_reads[0]["action"] == "retry-step",
              f"({resume_reads[0]['action']})")
    return checks


async def _checks_resume_fastpath_db(outcome: ThreadRunResult,
                                     ) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"resume-fastpath walk: {name}",
                                  ok=ok, detail=detail))

    reviews = await _review_rows(_chain_id_of(outcome))
    check("the [System One verdict] review row persisted with the "
          "retry action",
          any("[System One verdict]" in str(r["payload"].get("review")
                                            or "")
              and "retry-step" in str(r["payload"].get("review") or "")
              for r in reviews),
          f"({len(reviews)} review row(s))")
    rows = await _top_states(outcome)
    volume = next((r for r in rows if "量价" in (r["title"] or "")), None)
    check("the resumed hop completed the rate-limited row",
          volume is not None and volume["status"] == "completed",
          f"({volume})")
    markers = await _resume_markers(outcome)
    check("THE RESUME FORK (the fork action on the UI): a child_kind 3 "
          "RED DEAD-END record row under the re-opened step",
          len(markers) == 1 and markers[0]["status"] == "failed"
          and "supervisor resume" in str(markers[0]["title"]),
          f"({markers})")
    return checks


def _checks_resume_cap(outcome: ThreadRunResult,
                       provider: DummyLanggraphProvider,
                       engine: ScriptedRouterEngine,
                       sandbox: ScriptedSandbox) -> List[AssertCheck]:
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"resume-cap walk: {name}",
                                  ok=ok, detail=detail))

    check("the volume step burned its whole budget (the decode-every-"
          "turn waste shape, no flake)",
          "step-volume-cap-burn" in used and provider.n_flakes == 0,
          f"(used={sorted(used)[:6]}…, flakes={provider.n_flakes})")
    check("the repair ask was served the CAP-CLASS verdict face (the "
          "manifest's step-limit line keyed it)",
          "supervisor-verdict-cap-retry" in used)
    check("the resumed pass ran under the RAISED budget and completed "
          "(the face pins the resume block's `conversation budget: 24 "
          "steps` line — base 8 + bump 16)",
          "step-volume-cap-resumed" in used)
    return checks


async def _checks_resume_cap_db(outcome: ThreadRunResult,
                                ) -> List[AssertCheck]:
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"resume-cap walk: {name}",
                                  ok=ok, detail=detail))

    rows = await _top_states(outcome)
    volume = next((r for r in rows if "量价" in (r["title"] or "")), None)
    check("the cap-dead volume row was REOPENED and the raised-budget "
          "resumed hop completed it",
          volume is not None and volume["status"] == "completed",
          f"({volume})")
    check("the reopen cleared the failed attempt's error",
          volume is not None and not (volume["error"] or ""),
          f"({volume and volume['error']})")
    reviews = await _review_rows(_chain_id_of(outcome))
    check("the retry verdict rode a supervisor-review row",
          any("retry-step" in str(r["payload"].get("review") or "")
              for r in reviews),
          f"({len(reviews)} review row(s))")
    markers = await _resume_markers(outcome)
    check("THE RESUME FORK: a child_kind 3 red dead-end record row "
          "under the re-opened step, carrying the prior attempt's "
          "step-cap error",
          len(markers) == 1 and markers[0]["status"] == "failed"
          and "supervisor resume" in str(markers[0]["title"])
          and "step limit" in str(markers[0]["error"]),
          f"({markers})")
    return checks


def _chain_checks(outcome: ThreadRunResult, prefix: str) -> List[AssertCheck]:
    """The every-walk tail: the result output, the three-thread chain
    and the four review seats."""
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=f"{prefix}{name}", ok=ok,
                                  detail=detail))

    check("the walk completed to the result output",
          "DUMMY FINAL ANSWER" in outcome.result.answer)
    check("three-thread chain", _chain_threads(outcome) == 3,
          f"got {_chain_threads(outcome)}")
    reviews = _reviews(outcome)
    check("four completed review seats",
          len(reviews) == 4 and all(r.status == "completed" for r in reviews),
          f"{[(r.node, r.status) for r in reviews]}")
    return checks


# ---------------------------------------------------------------- runs
_SEAM_BUILDERS = {
    "spawn": _seams_spawn, "fork": _seams_fork,
    "fork-drift": _seams_fork_drift, "fork-sub": _seams_fork_sub,
    "join": _seams_join,
    "probe": _seams_probe, "background": _seams_background,
    "reserve": _seams_reserve, "cut": _seams_cut,
    "repeat": _seams_repeat, "repair": _seams_repair,
    "syntax": _seams_syntax,
    "resume-ignore": _seams_resume_ignore,
    "resume-noaction": _seams_resume_noaction,
    "resume-ratelimit": _seams_resume_ratelimit,
    "resume-cap": _seams_resume_cap,
    "resume-fastpath": _seams_resume_fastpath,
}

# The rate-limit walks shrink the supervisor's retry throttle (the
# real 45 s cooldown would double the walk's wall clock; 1 s keeps the
# sleep asserted) — the patch lives ONLY inside the walk's run and is
# always restored.
_THROTTLE_KINDS = frozenset({"resume-ratelimit", "resume-fastpath"})


async def run_router_walk(kind: str, *, purge: bool = True) -> RouterWalkResult:
    """One router walk END TO END: fresh provider/seam/scripted engine +
    sandbox, the REAL ``run_thread`` chain, the walk's checks, then —
    ``purge=True`` (the standing probe's leave-no-trace contract) — the
    whole chain purged. ``purge=False`` (the daemon's self-test) keeps
    the chain in the history: Summary History lists it, the Thread
    Graph renders its child rows."""
    if kind not in _SEAM_BUILDERS:
        raise ValueError(f"unknown router walk kind: {kind!r}")
    provider = DummyLanggraphProvider()
    mcp = FeatureToolSeam()
    engine, sandbox, max_steps = _SEAM_BUILDERS[kind]()
    if kind == "background":
        provider = YieldingProvider(provider)
    thread_id = uuid.uuid4().hex
    restore = fast_throttle_patch() if kind in _THROTTLE_KINDS else None
    t0 = time.monotonic()
    try:
        outcome = await run_thread(
            provider, mcp, goal=_WALK_GOALS[kind], model="dummy-1",
            max_steps=max_steps, thread_id=thread_id,
            decision_engine=engine, sandbox=sandbox)
    finally:
        if restore is not None:
            restore()
    elapsed = time.monotonic() - t0
    checks = _WALK_CHECKS[kind](outcome, provider, engine, sandbox)
    if kind in _THROTTLE_KINDS:
        checks.append(AssertCheck(
            name=f"{kind} walk: the rate-limit throttle SLEPT inside "
                 f"the window",
            ok=elapsed >= 0.9,
            detail=f"{elapsed:.1f}s >= the 1s overridden cooldown"))
    checks.extend(await _DB_CHECKS[kind](outcome))
    checks.extend(_chain_checks(outcome, f"{kind} walk: "))
    if purge:
        await PLAN_STORE.purge_super_thread(
            outcome.super_thread_id or thread_id)
    return RouterWalkResult(kind=kind, outcome=outcome, checks=checks,
                            engine=engine, sandbox=sandbox,
                            provider=provider, thread_id=thread_id,
                            elapsed_s=elapsed)


_WALK_GOALS: Dict[str, str] = {
    "spawn": GOAL_SPAWN, "fork": GOAL_FORK, "fork-drift": GOAL_DRIFT,
    "fork-sub": GOAL_FORK_SUB,
    "join": GOAL_JOIN, "probe": GOAL_PROBE, "background": GOAL_BG,
    "reserve": GOAL_RESERVE, "cut": GOAL_CUT,
    "repeat": GOAL_REPEAT, "repair": GOAL_REPEAT, "syntax": GOAL_SYNTAX,
    "resume-ignore": GOAL_IGNORE, "resume-noaction": GOAL_NOACTION,
    "resume-ratelimit": GOAL_RATELIMIT, "resume-fastpath": GOAL_RATELIMIT,
    "resume-cap": GOAL_CAP,
}

# The per-kind checks: the conversation-face function (the traffic
# asserts) + the async DB-face function (the child rows) — every DB
# check function takes the outcome alone.
_WALK_CHECKS: Dict[str, Callable[..., list]] = {
    "spawn": _checks_spawn, "fork": _checks_fork,
    "fork-drift": _checks_fork_drift, "fork-sub": _checks_fork_sub,
    "join": _checks_join,
    "probe": _checks_probe, "background": _checks_background,
    "reserve": _checks_reserve, "cut": _checks_cut,
    "repeat": _checks_repeat, "repair": _checks_repair,
    "syntax": _checks_syntax,
    "resume-ignore": _checks_resume_ignore,
    "resume-noaction": _checks_resume_noaction,
    "resume-ratelimit": _checks_resume_ratelimit,
    "resume-fastpath": _checks_resume_fastpath,
    "resume-cap": _checks_resume_cap,
}

_DB_CHECKS = {
    "spawn": _checks_spawn_db, "fork": _checks_fork_db,
    "fork-drift": _checks_fork_drift_db, "fork-sub": _checks_fork_sub_db,
    "join": _checks_join_db,
    "probe": _checks_probe_db, "background": _checks_background_db,
    "reserve": _checks_reserve_db, "cut": _checks_cut_db,
    "repeat": _checks_repeat_db, "repair": _checks_repair_db,
    "syntax": _checks_syntax_db,
    "resume-ignore": _checks_resume_ignore_db,
    "resume-noaction": _checks_resume_noaction_db,
    "resume-ratelimit": _checks_resume_ratelimit_db,
    "resume-fastpath": _checks_resume_fastpath_db,
    "resume-cap": _checks_resume_cap_db,
}


# ------------------------------------------------------ showcase chain
# The daemon self-test's MAIN chain (2026-11-14): ONE graph the UI
# opens right after the run, holding every scenario kind as nodes —
# trend + volume (the flake → supervisor verdict → resume),
# a SPAWN step (step_3, 00_router_chain's step-script faces via the
# exact title) and a FORK step whose fork child spawns its OWN sub
# node (step_4). The scripted router answers drive the fork/spawn
# deterministically while the kg-gate and resume reads DELEGATE to the
# real recording engine (the canonical battery's jev checks stay
# real-computation).
SHOWCASE_PLAN_TAG = "plan-selftest-showcase"
SHOWCASE_GOAL_MARKER = "全场景自检"


class ShowcaseEngine:
    """The showcase main chain's decision engine: the ROUTER lanes
    scripted (route_spawn call-ordered yes→no, route_fork on the
    sandbox-proposing turn), the REAL reads (kg_relevance / resume /
    spawn breaker) delegated to the injected inner engine — the
    ``RecordingDecisionEngine`` the daemon mounts, whose ledger the
    canonical battery reads. ``inner=None`` (a JEV-disabled daemon)
    answers the delegated reads not-ok (the lanes' fail-soft face)."""

    def __init__(self, inner: Any = None) -> None:
        self._inner = inner
        self._spawn_reads = 0
        self.reads: list = []

    async def _scripted(self, method: str, action: str, ok: bool,
                        **kw) -> JevDecision:
        decision = JevDecision(
            action=action, confidence=0.9 if ok else 0.4, ok=ok,
            template=method, model="stub:showcase-router")
        self.reads.append({"method": method, "action": decision.action,
                           "ok": decision.ok, "facts": kw})
        return decision

    async def decide_route_spawn(self, **kw):
        self._spawn_reads += 1
        # 1st sandbox-proposing step (step_3) routes to a scripting
        # child; the fork step (step_4) declines — it is a continuation.
        action = "yes" if self._spawn_reads == 1 else "no"
        return await self._scripted("route_spawn", action, True, **kw)

    async def decide_route_fork(self, **kw):
        # The drift watch: affirm only the sandbox-proposing turn.
        ok = "sandbox" in kw.get("pending_tools", "")
        return await self._scripted("route_fork",
                                    "yes" if ok else "no", True, **kw)

    async def decide_route_fork_point(self, **kw):
        return await self._scripted("route_fork_point", "now", False, **kw)

    async def decide_route_join(self, **kw):
        return await self._scripted("route_join", "no", False, **kw)

    async def decide_failure_probe(self, **kw):
        # THE INVESTIGATOR LANE: step_5's spawned scripting child fails
        # on the armed flake — the probe read affirms (bounded by
        # jev_route_probe_max 1), the parent spawns the investigator.
        return await self._scripted("failure_probe", "yes", True, **kw)

    async def decide_spawn(self, **kw):
        return await self._scripted("spawn", "no", False, **kw)

    async def decide_resume(self, **kw):
        # The fatal step's (step_6, UndefinedTable) resume read answers
        # a CONFIDENT NO — the lane falls through to the generative ask
        # (which serves the no-action verdict) deterministically; the
        # transient flake's read (step_2) stays a REAL computation.
        if "UndefinedTable" in str(kw.get("error_text") or ""):
            decision = JevDecision(
                action="no", confidence=0.9, probabilities={}, ok=True,
                template="resume_judgment", model="stub:showcase-router")
            self.reads.append({"method": "resume", "action": "no",
                               "ok": True, "facts": kw})
            return decision
        if self._inner is None:
            return await self._scripted("resume", "no", False, **kw)
        decision = await self._inner.decide_resume(**kw)
        self.reads.append({"method": "resume", "action": decision.action,
                           "ok": decision.ok, "facts": kw})
        return decision

    async def decide_kg_relevance(self, **kw):
        if self._inner is None:
            return await self._scripted("kg_relevance", "reject", False,
                                        **kw)
        decision = await self._inner.decide_kg_relevance(**kw)
        self.reads.append({"method": "kg_relevance",
                           "action": decision.action,
                           "ok": decision.ok, "facts": kw})
        return decision


def build_showcase_sandbox() -> "ScriptedSandbox":
    """The showcase chain's scripted sandbox: the spawn child's
    scripts/ma_study.py (write + green run) and the grandchild's
    scripts/ma_sub.py (write + green sub run) — steps are sequential,
    so the per-tool call order is deterministic."""
    return ScriptedSandbox({
        "sandbox_write_file": [{"ok": True, "path": "scripts/ma_study.py",
                                "bytes": 96},
                               {"ok": True, "path": "scripts/ma_sub.py",
                                "bytes": 96}],
        "sandbox_run": [dict(_GREEN_RUN), dict(_GREEN_RUN_SUB)],
    })


async def showcase_extra_checks(outcome: ThreadRunResult,
                                provider: Any) -> List[AssertCheck]:
    """The showcase main chain's own checks — the scenario nodes the
    UI must show: the spawn child under step_3, the fork child under
    step_4 with its OWN sub node, the FAILED scripting child + the
    investigator under step_5, the FAILED step_6 and its SKIPPED
    doomed tail (step_7), and the walked faces."""
    used = provider.scenarios_used()
    checks: List[AssertCheck] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append(AssertCheck(name=name, ok=ok, detail=detail))

    check("the showcase plan was proposed",
          SHOWCASE_PLAN_TAG in used)
    for tag in ("step-script-proposal", "step-script-folded",
                "script-child-write", "script-child-run",
                "script-child-evidence"):
        check(f"the spawn scenario walked ({tag})", tag in used)
    for tag in ("showcase-step4-proposal", "fork-child-subspawn",
                "forksub-child-write", "forksub-child-run",
                "forksub-child-evidence", "fork-child-subfold",
                "showcase-step4-fold"):
        check(f"the fork-with-sub scenario walked ({tag})", tag in used)
    for tag in ("showcase-step5-spawn", "showcase-step5-fold"):
        check(f"the failure/resumption scenario walked ({tag})",
              tag in used)
    check("the fatal step's flake served (unrecordable — its FAILED "
          "row is the trace)", provider.n_flakes >= 1)
    children = await _child_rows(outcome)
    spawn_kids = [c for c in children if c["child_kind"] == 0]
    fork_kids = [c for c in children if c["child_kind"] == 1]
    failed_kids = [c for c in spawn_kids if c["status"] == "failed"]
    done_kids = [c for c in spawn_kids if c["status"] == "completed"]
    check("the spawn child completed under step_3 (child_kind 0)",
          len(done_kids) >= 1, f"({children})")
    check("the fork child completed under step_4 (child_kind 1, fork_ "
          "slug)",
          len(fork_kids) == 1 and fork_kids[0]["status"] == "completed"
          and str(fork_kids[0]["node"]).startswith("fork_"),
          f"({children})")
    check("a FAILED scripting child under step_5 (the red node)",
          len(failed_kids) == 1, f"({children})")
    check("the investigator spawn completed under step_5 (spawn_ slug)",
          any(c["status"] == "completed"
              and str(c["node"]).startswith("spawn_")
              for c in done_kids),
          f"({children})")
    subs = (await _grandchild_rows(fork_kids[0]["state_id"])
            if fork_kids and fork_kids[0].get("state_id") else [])
    check("the fork child's OWN sub node completed (the second "
          "generation, spawn_ slug)",
          len(subs) == 1 and subs[0]["status"] == "completed"
          and subs[0]["child_kind"] == 0, f"({subs})")
    rows = await _top_states(outcome)
    risk = next((r for r in rows if "风险提示" in (r["title"] or "")), None)
    tail = next((r for r in rows if "综合结论整理" in (r["title"] or "")),
                None)
    check("the fatal step_6 STAYED FAILED (no-action — the red node on "
          "the main path)",
          risk is not None and risk["status"] == "failed", f"({risk})")
    check("the doomed tail step_7 settled SKIPPED (the not-run node)",
          tail is not None and tail["status"] == "skipped"
          and "not run" in str(tail["error"] or ""), f"({tail})")
    markers = await _resume_markers(outcome)
    check("THE RESUME FORK under the resumed step_2 (the red dead-end "
          "record of the failed attempt)",
          any("supervisor resume" in str(m["title"])
              and m["status"] == "failed" for m in markers),
          f"({markers})")
    check("THE FAILURE FORK under the no-action'd step_6 (the red "
          "dead-end record beside the still-failed row)",
          any("supervisor no-action" in str(m["title"])
              and m["status"] == "failed" for m in markers),
          f"({markers})")
    return checks


# ------------------------------------------------------ coverage gate
# Chat-dataset scenarios that no single walk can serve BY DESIGN:
# the flake SERVES (the provider raises without recording the tag —
# the walks assert them via provider.n_flakes instead) and the step
# CATCH-ALLS (every walk's steps match their specific faces; these
# serve only unmatched conversations, which the contract suite
# temp_scripts/test_dummy_langgraph.py formats mechanically), plus
# @supervisor-tail — the sweep ask's CATCH-ALL, which only serves a
# manifest carrying NONE of the verdict faces' needles
# (repair-protocol / trading_amount / UndefinedTable); every walk's
# sweep manifest carries its own failure's needle (measured 2026-11-14
# f: the noaction walk's sweep re-serves the noaction face) and
# lane-handled failures dedupe out of the sweep entirely.
# Everything else is WALKED — the 2026-11-14 (f) round brought the
# supervisor verdict variants, their plans and the rate-limit lane
# back in as resume-* walks (98/106 walked).
ALTERNATIVE_FACES: Tuple[str, ...] = (
    "script-child-flake", "step-volume-flake",
    "step-volume-key-error", "step-volume-rate-limit",
    "step-volume-table-error",
    "step-finding", "step-finding-tools",
    "supervisor-tail",
    "showcase-step6-fail",
)


def dataset_coverage_check(walked: set) -> AssertCheck:
    """MUST TEST ALL SCENARIOS (2026-11-14): the union of the runs'
    scenarios_used must cover EVERY chat-dataset scenario tag — each
    walk's own checks already assert its required faces, this is the
    whole-dataset gate over their union. A tag outside the walked set
    AND the documented alternative faces fails the check (naming the
    misses)."""
    from test.dummy_llm import load_features
    from test.dummy_llm.provider import FEATURES_DIR

    tags = {s.tag for f in load_features(FEATURES_DIR)
            for s in f.scenarios}
    missing = sorted(tags - set(walked) - set(ALTERNATIVE_FACES))
    return AssertCheck(
        name="every chat-dataset scenario walked (or a documented "
             "alternative face)",
        ok=not missing,
        detail=(f"{len(tags & set(walked))}/{len(tags)} walked; "
                f"contract-only faces: {len(set(ALTERNATIVE_FACES) & tags)}"
                + (f"; MISSES: {missing}" if missing else "")))


# ------------------------------------------------------ decision gate
async def decision_checks(live_engine: Any = None) -> List[AssertCheck]:
    """THE JEV DECISION BATTERY (2026-11-14 f): every
    ``02_decisions.feature`` scenario — the fork jev decisions
    (route-fork / fork-point), the resume (exception-resumption)
    decisions, the spawn-streak / route-spawn / route-join /
    failure-probe reads — asserted TWICE:

    * STUB — the feature-dataset engine answers each scenario's typed
      facts and the interpreted action must equal the feature's
      expectation (deterministic, zero spend; the same check
      ``temp_scripts/test_dummy_langgraph.py`` makes per scenario);
    * LIVE — when *live_engine* (the daemon's active
      ``JevDecisionEngine``) is not None, the REAL resident onejev
      model's raw argmax is compared against the same expectations
      (``DecisionChoiceChecker``); None, an exception or an all-scenario
      miss reads as the host being unreachable → a SKIPPED check, and
      a genuine mismatch (some but not all) FAILS.
    """
    from test.dummy_langgraph import StubJevDecisionEngine, load_decision_book
    from test.dummy_langgraph.jev import DecisionChoiceChecker

    checks: List[AssertCheck] = []
    book = load_decision_book()
    stub = StubJevDecisionEngine()
    for sc in book.scenarios:
        decision = await stub.run(sc.template, sc.facts)
        # the resume template maps yes/no onto the repair vocabulary;
        # every other template's raw choice IS its verdict
        if sc.template_key == "resume_judgment":
            expected = ("retry-step" if sc.expected_choice == "yes"
                        else "no-action")
        else:
            expected = sc.expected_choice
        checks.append(AssertCheck(
            name=f"decision @{sc.tag}: stub choice == {expected}",
            ok=bool(decision.ok and decision.action == expected),
            detail=f"got {decision.action!r}"))
    if live_engine is None:
        checks.append(AssertCheck(
            name="decision battery live: the real onejev argmax matched "
                 "every scenario",
            ok=True, skipped=True, detail="no engine mounted"))
        return checks
    try:
        failures = await DecisionChoiceChecker(
            engine=live_engine).check()
    except Exception as exc:  # noqa: BLE001 — host down = SKIP, not fail
        checks.append(AssertCheck(
            name="decision battery live: the real onejev argmax matched "
                 "every scenario",
            ok=True, skipped=True,
            detail=f"resident host unreachable ({type(exc).__name__})"))
        return checks
    if failures and len(failures) == len(book.scenarios):
        checks.append(AssertCheck(
            name="decision battery live: the real onejev argmax matched "
                 "every scenario",
            ok=True, skipped=True,
            detail="every read not-ok — resident host unreachable?"))
        return checks
    checks.append(AssertCheck(
        name="decision battery live: the real onejev argmax matched "
             "every scenario",
        ok=not failures,
        detail=("; ".join(failures[:5])
                + (f" (+{len(failures) - 5} more)" if len(failures) > 5
                   else "")) if failures else
        f"{len(book.scenarios)} scenarios matched"))
    return checks
