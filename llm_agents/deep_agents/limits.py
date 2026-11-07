"""The deep agent's RUN LIMITS — one static JSON config file, read
fail-soft with code defaults.

The limits that bound how many (and how large) LLM requests one
deep-agent run may make — conversation step budgets, the LangGraph
recursion bound, the plan-tree caps, the subagent fan-out, the
per-request output ceiling, the findings-bridge and eager-prompt
budgets, and the payload-guard caps (oversized data ships as metadata
+ preview, see ``payload_guard``) — used to live as hardcoded module
constants scattered across the package. They now live in ONE editable
file of record:

    database/sql/monitoring/static/deep_agent_config.json

(the same static-config discipline as service_config/ and
external_request_config/: the file is in git, the Config bridge edits
it, ``updated_at`` is stamped on every save). The daemon reads it
through :func:`run_limits` — an mtime-cached, fail-soft load — so a
config edit takes effect on the NEXT run without a daemon restart,
and any problem reading the file (missing, malformed, out-of-range)
falls back to the built-in defaults: a config problem never blocks a
run.

Layering: this module imports nothing from the package (stdlib +
pydantic only) — every layer (core / planning / graph) may read it. The
model field defaults ARE the migrated constants (the numbers the code
ran with before the migration); the JSON file seeds the same values.
Field docs state each limit's enforcement site.
"""
import json
from pathlib import Path
from typing import Any, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = (
    _REPO_ROOT / "database" / "sql" / "monitoring" / "static"
    / "deep_agent_config.json")


class RunLimits(BaseModel):
    """One resolved limits snapshot — plain ints the call sites use
    directly. ``subagent_timeout_sec`` is None (unbounded, the
    historical behavior) or a positive wall-clock seconds per spawned
    child."""

    model_config = ConfigDict(frozen=True)

    # Per-conversation hop budget: the bridge's default max_steps for
    # a run (server RunRequest default + the state fallback in the
    # execute-node clamp) — how many LLM turns one conversation may
    # take before it is stopped.
    max_conversation_steps: int = 40
    # The execute-node clamp: a conversation keeps at least this many
    # hops and at most this many, whatever the run asked for (the
    # delegated-fork headroom cap reuses the ceiling).
    min_conversation_steps: int = 4
    max_steps_cap: int = 32
    # The graph invocation's LangGraph superstep bound (plan + hops +
    # replans + finalize + result) — past it the run raises.
    recursion_limit: int = 160
    # The run's TOTAL wall-clock budget (seconds): past it the step
    # lane routes to finalize (the run settles cleanly — every pending
    # row not-run, the answer the last completed finding), instead of
    # grinding forever. The bridge/client deadlines sit AT this value.
    run_timeout_sec: float = 18_000.0
    # The per-LLM-request WALL-CLOCK budget (seconds) — the transport's
    # retry ladder terminates at this budget (each attempt's read
    # timeout shrinks to the remainder; exhaustion raises the
    # budget-exceeded error). The policy: one logical request must
    # finish well under this; a request that overran means the input is
    # too heavy — the caller refactors it into smaller tasks (the
    # planner's slimmed init message; the fork pre-planner's small
    # call). Scope: ONE individual LLM request (one chat completion
    # call, its retries included) — NOT a graph node. A node (one step
    # conversation hop) makes MANY such requests across its tool
    # turns, so it legitimately outlasts this budget many times over;
    # the node itself is bounded by max_conversation_steps turns and
    # the run by run_timeout_sec, never by this value.
    llm_request_timeout_sec: float = 300.0

    # The plan tree: how many steps the PLANNED plan may hold, how
    # many of them may carry a fork marker, how many propose→correct
    # rounds the planning session gets, and the replan guards
    # (update_plan total-node cap, forks-per-node cap).
    max_plan_nodes: int = 12
    max_delegated_steps: int = 6
    plan_repair_rounds: int = 2
    # The batched proposal protocol: how many proposal ROUNDS (one LLM
    # call each, a few steps per reply) the planning session may spend
    # before the plan closes — the init call proposes the first batch,
    # every later round proposes the next batch over the nodes-so-far
    # (planner.propose_batches; runner._propose_and_persist_plan).
    plan_proposal_batches: int = 6
    max_total_plan_steps: int = 24
    max_fork_children: int = 8

    # The subagent fan-out: fork-wave width per step hop, the
    # per-agent spawn semaphore, the spawn-chain depth cap (an agent
    # AT the depth gets no spawn tool), and the optional wall clock
    # per spawned child.
    max_parallel_delegated_steps: int = 4
    max_concurrent_subagents: int = 4
    max_spawn_depth: int = 2
    subagent_timeout_sec: Optional[float] = None

    # The per-LLM-request output ceiling (the runaway guard) and the
    # output-validation repair rounds per conversation.
    max_output_tokens: int = 8192
    max_output_repairs: int = 1

    # The findings bridge: one finding's char cap and the whole
    # block's cap before older findings age to title lines.
    finding_max_chars: int = 1200
    findings_block_max_chars: int = 12000

    # The LARGE-PAYLOAD PRE-CHECK (payload_guard.py, applied at every
    # message builder + the loop's tool-result seam): the structured
    # context's total serialized size above which its largest entries
    # are guarded down to the gate; the per-node serialized size above
    # which one subtree ships as metadata + preview instead of its full
    # content; and the preview budget (chars per elided value, elements
    # per elided container). tool_result_max_chars caps ONE tool
    # result's text (head+tail) before it enters the transcript.
    payload_guard_chars: int = 16000
    payload_entry_max_chars: int = 4000
    payload_preview_chars: int = 800
    payload_preview_items: int = 3
    tool_result_max_chars: int = 20000

    # The eager prompt-loading budget: a skills selection at or under
    # this many rows — or whose full texts fit this char budget —
    # loads eagerly without the planner's verdict.
    prompt_eager_rows: int = 4
    prompt_eager_chars: int = 4000
    # The KG-HOTPATH corroboration lane (planning/kg_prompts.py,
    # 2026-11-07): optional-labeled skills the promoted llm.kg_hotpaths
    # chains implicate auto-load at plan time — at most this many rows
    # per run (0 disables the lane), probing at most this many
    # top-hit_count chains.
    prompt_hotpath_rows: int = 4
    hotpath_probe_chains: int = 5

    # The SUPERVISOR REPAIR LANE (supervisor/repair.py + poller.py):
    # the per-run poll cadence watching node states; the pause window
    # the step lane holds for a repair verdict on a root-path failure
    # (0 disables repair — the lane never waits); the per-node and
    # per-run retry budgets (a retry re-opens the failed row only
    # while both remain); the cooldown a RATE-LIMIT-class retry waits
    # inside the window before the hop re-runs (the account is hot —
    # an immediate re-run re-hits the same limit; the grace must cover
    # the intervention + this throttle).
    supervisor_poll_interval_sec: float = 10.0
    supervisor_repair_grace_sec: float = 120.0
    supervisor_max_node_retries: int = 1
    supervisor_max_repairs_per_run: int = 2
    supervisor_retry_throttle_sec: float = 45.0

    @classmethod
    def from_doc(cls, doc: Any) -> "RunLimits":
        """JSON doc → validated limits: unknown keys are ignored,
        malformed/out-of-range values fall back to that field's
        default (per-field — one bad key never discards the rest),
        and cross-field fixups hold (the cap never sits under the
        floor). Non-dict input yields the defaults."""
        if not isinstance(doc, dict):
            return cls()

        def num(key: str, default: int, low: int,
                high: Optional[int] = None) -> Union[int, float]:
            raw = doc.get(key, default)
            try:
                value = int(raw) if float(raw) == int(raw) else float(raw)
            except (TypeError, ValueError):
                return default
            if value < low or (high is not None and value > high):
                return default
            return value

        base = cls(
            max_conversation_steps=int(
                num("max_conversation_steps", 40, 1, 1000)),
            min_conversation_steps=int(
                num("min_conversation_steps", 4, 1, 64)),
            max_steps_cap=int(num("max_steps_cap", 32, 1, 1000)),
            recursion_limit=int(num("recursion_limit", 160, 25, 100_000)),
            run_timeout_sec=float(num("run_timeout_sec", 18_000.0, 60.0)),
            llm_request_timeout_sec=float(
                num("llm_request_timeout_sec", 300.0, 5.0)),
            max_plan_nodes=int(num("max_plan_nodes", 12, 1, 200)),
            max_delegated_steps=int(num("max_delegated_steps", 6, 0, 200)),
            plan_repair_rounds=int(num("plan_repair_rounds", 2, 0, 10)),
            plan_proposal_batches=int(
                num("plan_proposal_batches", 6, 1, 24)),
            max_total_plan_steps=int(
                num("max_total_plan_steps", 24, 1, 1000)),
            max_fork_children=int(num("max_fork_children", 8, 0, 200)),
            max_parallel_delegated_steps=int(
                num("max_parallel_delegated_steps", 4, 1, 64)),
            max_concurrent_subagents=int(
                num("max_concurrent_subagents", 4, 1, 64)),
            max_spawn_depth=int(num("max_spawn_depth", 2, 0, 8)),
            subagent_timeout_sec=(
                None if doc.get("subagent_timeout_sec") is None
                else float(num("subagent_timeout_sec", 0.0, 0)
                           ) or None),
            max_output_tokens=int(num("max_output_tokens", 8192, 256)),
            max_output_repairs=int(num("max_output_repairs", 1, 0, 5)),
            finding_max_chars=int(num("finding_max_chars", 1200, 50)),
            findings_block_max_chars=int(
                num("findings_block_max_chars", 12000, 100)),
            payload_guard_chars=int(num("payload_guard_chars", 16000, 0)),
            payload_entry_max_chars=int(
                num("payload_entry_max_chars", 4000, 200)),
            payload_preview_chars=int(num("payload_preview_chars", 800, 100)),
            payload_preview_items=int(
                num("payload_preview_items", 3, 1, 50)),
            tool_result_max_chars=int(num("tool_result_max_chars", 20000, 500)),
            prompt_eager_rows=int(num("prompt_eager_rows", 4, 0, 100)),
            prompt_eager_chars=int(num("prompt_eager_chars", 4000, 0)),
            prompt_hotpath_rows=int(num("prompt_hotpath_rows", 4, 0, 50)),
            hotpath_probe_chains=int(num("hotpath_probe_chains", 5, 1, 20)),
            supervisor_poll_interval_sec=num(
                "supervisor_poll_interval_sec", 10.0, 2.0, 60.0),
            supervisor_repair_grace_sec=num(
                "supervisor_repair_grace_sec", 120.0, 0.0, 600.0),
            supervisor_max_node_retries=int(
                num("supervisor_max_node_retries", 1, 0, 3)),
            supervisor_max_repairs_per_run=int(
                num("supervisor_max_repairs_per_run", 2, 0, 6)),
            supervisor_retry_throttle_sec=num(
                "supervisor_retry_throttle_sec", 45.0, 0.0, 300.0),
        )
        # Cross-field fixups: the clamp ceiling never sits under the
        # floor; the findings block never under one finding.
        if base.max_steps_cap < base.min_conversation_steps:
            base = base.model_copy(
                update={"max_steps_cap": base.min_conversation_steps})
        if base.findings_block_max_chars < base.finding_max_chars:
            base = base.model_copy(
                update={"findings_block_max_chars": base.finding_max_chars})
        # The payload guard's three thresholds stay ordered: the
        # preview sits under the per-entry threshold, the entry
        # threshold under the whole-payload gate.
        if base.payload_entry_max_chars < base.payload_preview_chars:
            base = base.model_copy(
                update={"payload_entry_max_chars": base.payload_preview_chars})
        if base.payload_guard_chars < base.payload_entry_max_chars:
            base = base.model_copy(
                update={"payload_guard_chars": base.payload_entry_max_chars})
        return base


# mtime-cached single doc read — (stat key, limits); None key = the
# file is unreadable (defaults, retried on every call until it
# reappears).
_CACHE: Optional[Tuple[Optional[Tuple[int, int]], RunLimits]] = None


def run_limits() -> RunLimits:
    """The current limits snapshot — re-read when the config file's
    mtime/size changes, defaults when it cannot be read or parsed.
    Cheap enough to call per hop; never raises."""
    global _CACHE
    try:
        st = CONFIG_FILE.stat()
        key: Optional[Tuple[int, int]] = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    if _CACHE is not None and _CACHE[0] == key:
        return _CACHE[1]
    limits = RunLimits()
    if key is not None:
        try:
            limits = RunLimits.from_doc(
                json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            limits = RunLimits()
    _CACHE = (key, limits)
    return limits
