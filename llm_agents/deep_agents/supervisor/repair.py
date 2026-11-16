"""llm_agents.deep_agents.supervisor.repair — the REPAIR LANE's
coordination + policy core (2026-11-06; 2026-11-11 refactored onto the
EVENT QUEUE): the event-time counterpart of the post-run observe sweep.

WHY A WINDOW: a failed step settles its row, skips the remaining
pending tail and routes the lane to finalize ALL WITHIN ONE SUPERSTEP
of the looping step node — an external poller flipping rows back can
never land between those writes. So the step lane itself opens a
BOUNDED PAUSE WINDOW: on a root-path failure (before the doomed-tail
settle) it signals this channel and waits up to the run's repair
grace for the supervisor's verdicts. ``retry-step`` verdicts re-open
the failed rows (``planning.store.reopen_state``) and the lane's
normal routing re-picks them next hop — the run resumes. Any other
outcome (no verdict, ``no-action``, grace expiry, repair disabled)
falls through to the existing failure semantics untouched.

THE QUEUE REFACTOR (2026-11-11): this module keeps the POLICY
(classifiers, the Action parser, the structural gate) and the LANE
FACADE (``RepairChannel`` — the lane's two calls are unchanged), while
the event mechanics moved to ``event_queue.py`` (the coalescing queue
+ the per-window future) and the DECISION moved to ``workers.py`` (the
RepairWorker the picker launches per classified event). The channel is
now two things only:

* the WINDOW REGISTRY — at most one open pause window at a time; each
  carries a future the RepairWorker resolves with the verdict dict
  (``resolve_window``), and a superseding signal resolves the stale
  window ``{}`` (its lane wait falls through — nothing waits twice);
* the LEDGER — the per-run repair bookkeeping (attempts per state,
  handled/attempted/asked ids, repairs used) that ``should_retry``
  reads and the workers update, shared by every worker so the final
  sweep dedupes against mid-run reviews.

CODE FILTERS FIRST, THE MODEL JUDGES AFTER (the Super-agent property
the watchdog already follows): :func:`is_transient` classifies the
error text in pure code — a NON-transient class (balance exhaustion,
step-cap, user stop) returns an immediate ``no-action`` verdict with
ZERO LLM cost; only a transient, budgeted candidate wakes the
supervisor agent, and its ``Action:`` line is advisory until
:func:`should_retry` validates it against the classifier AND the
retry budgets.

Pure module, stub-testable (asyncio primitives + the limits reader).
"""
from __future__ import annotations

import asyncio
import re
from typing import Dict, List, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, ConfigDict

from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.supervisor.event_queue import (
    EventKind, FailureRef, RepairWindow, SupervisorEvent,
    SupervisorEventQueue,
)

# The Action line's accepted values (the verdict contract).
ACTION_RETRY = "retry-step"
ACTION_IGNORE = "ignore-step"
ACTION_NONE = "no-action"


class Verdict(BaseModel):
    """One failed node's verdict — the ACTION plus, on a retry, the
    supervisor's INSTRUCTION: the bounded repair-protocol reply text
    the resumed node agent receives as its resume context (what went
    wrong, what to do differently). An empty instruction on
    no-action/ignore verdicts."""

    model_config = ConfigDict(frozen=True)

    action: str
    instruction: str = ""

# TRANSIENT classes — an operational hiccup a re-run can clear. The
# strings match what the hops record (``STEP FAILED —
# <Type>: zhipu: …`` / the transport's message formats).
_TRANSIENT_PATTERNS = (
    re.compile(r"HTTP 429\b"),                 # rate limited (persistent)
    re.compile(r"HTTP 5(?:00|02|03|04)\b"),    # upstream 5xx
    # zhipu provider codes: 1302 = account rate limit (速率限制),
    # 1701 = concurrency limit, 1234 = upstream network retry.
    re.compile(r"API error (?:1302|1701|1234)"),
    re.compile(r"速率限制"),                    # the 1302 message itself
    re.compile(r"transport error"),            # Timeout/ConnectionError wrap
    re.compile(r"(?:TimeoutError|timed out|timed_out)"),
    re.compile(r"ConnectionError"),
    re.compile(r"LlmRequestBudgetExceeded"),
    re.compile(r"request budget of .+ exhausted"),
)
# NOT transient — retrying cannot clear these (balance/quota, the
# step cap, a user stop). Checked FIRST so a 429 mention inside e.g. a
# step-limit message cannot mis-classify.
_NON_TRANSIENT_PATTERNS = (
    re.compile(r"API error 1113"),             # zhipu balance exhaustion
    re.compile(r"step limit|step-limit"),      # conversation cap
    re.compile(r"stopped by user"),
)


def is_transient(error_text: str) -> bool:
    """Code classifier: can a re-run plausibly clear this error?"""
    text = error_text or ""
    if any(p.search(text) for p in _NON_TRANSIENT_PATTERNS):
        return False
    return any(p.search(text) for p in _TRANSIENT_PATTERNS)


# The RATE-LIMIT subclass — a retry on this class is THROTTLED (the
# account/cohort is hot; an immediate re-run re-hits the same limit,
# observed live 2026-11-06). A strict subset of the transient classes.
_RATE_LIMITED_PATTERNS = (
    re.compile(r"HTTP 429\b"),
    re.compile(r"API error (?:1302|1701)"),
    re.compile(r"速率限制"),
)


def is_rate_limited(error_text: str) -> bool:
    """The throttle classifier: does this failure say the PROVIDER is
    rate limiting us right now?"""
    text = error_text or ""
    return any(p.search(text) for p in _RATE_LIMITED_PATTERNS)


# THE CAP-CLASS LANE (2026-11-15, run d7b24a36): a step-cap death is
# still never a PLAIN retry (the same conversation re-runs under the
# same budget and hits the same wall — the reason `step limit` sits in
# _NON_TRANSIENT_PATTERNS), but a cap death whose recorded usage shows
# REAL WORK can be retried ONCE with a RAISED step budget: the lane
# re-opens the node and its fresh conversation (and its delegated
# spawn children) run under base + supervisor_cap_retry_step_bump
# steps. The evidence channel is the error TEXT itself — the loop's
# cap raise embeds `(spent N step(s), M tool call(s))` and a failed
# child's result format carries the same counters — so this gate stays
# pure (no store reads), the same "code filters first" discipline.
_STEP_CAP_PATTERN = re.compile(r"hit the \d+-step limit|step limit|step-limit")
# The other never-retryable classes outrank the cap lane: a balance
# death or a user stop that happens to mention a step limit is not a
# budget problem.
_CAP_EXCLUDE_PATTERN = re.compile(r"API error 1113|stopped by user")
# The usage counters, as embedded by both carriers (the failed-child
# result format and the cap RuntimeError's `(spent N ...)` clause).
_CAP_USAGE_PATTERN = re.compile(r"(\d+) step\(s\), (\d+) tool call\(s\)")
# A cap death with fewer executed tool calls than this is a degenerate
# loop (repeated empty replies), not a budget-starved analysis — it
# stays no-action.
CAP_RETRY_MIN_TOOL_CALLS = 5


def is_step_cap(error_text: str) -> bool:
    """Code classifier: is this failure a conversation step-cap death
    (the retryable-with-raised-budget class)?"""
    text = error_text or ""
    if _CAP_EXCLUDE_PATTERN.search(text):
        return False
    return bool(_STEP_CAP_PATTERN.search(text))


def cap_usage(error_text: str) -> Optional[Tuple[int, int]]:
    """The (steps, tool_calls) the failed conversation actually spent,
    parsed from the error text's embedded counters — None when the
    text carries none (a legacy/counter-less failure is never eligible)."""
    match = _CAP_USAGE_PATTERN.search(error_text or "")
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


_ACTION_LINE = re.compile(
    r"(?:^|\n|\s)Action:\s*(retry-step|ignore-step|no-action)\b",
    re.IGNORECASE)


def parse_action(reply_text: str) -> str:
    """Fail-soft parse of the intervention's ``Action:`` line — a
    missing or unrecognized line reads as ``no-action`` (a repair is
    never applied on ambiguity)."""
    match = _ACTION_LINE.search(reply_text or "")
    return match.group(1).lower() if match else ACTION_NONE


def should_retry(channel: "RepairChannel", state_id: int,
                 error_text: str, action: str) -> bool:
    """The STRUCTURAL policy gate — the agent's verdict is advisory
    until this passes: the action must be ``retry-step``, the error
    class transient (the plain lane) or a step-cap death whose recorded
    usage shows real work (the raised-budget lane, run d7b24a36), and
    the node's retry budget, the run's repair budget and the cap lane's
    own per-run budget unspent. Bounded, code-enforced (never
    prompt-enforced)."""
    if action != ACTION_RETRY:
        return False
    limits = run_limits()
    if not is_transient(error_text):
        if not is_step_cap(error_text):
            return False
        usage = cap_usage(error_text)
        if usage is None or usage[1] < CAP_RETRY_MIN_TOOL_CALLS:
            return False  # a counter-less / degenerate cap death stands
        if (channel.cap_retries_used
                >= limits.supervisor_cap_retries_per_run):
            return False
    return (channel.attempts.get(state_id, 0)
            < limits.supervisor_max_node_retries
            and channel.repairs_used
            < limits.supervisor_max_repairs_per_run)


def cap_retry_step_budget(error_text: str,
                          base_max_steps: int) -> Optional[int]:
    """The RAISED conversation budget an eligible cap-class retry
    re-runs under — the hop's normal clamp +
    ``supervisor_cap_retry_step_bump`` (deliberately ABOVE
    ``max_steps_cap``: that excess is the lane's whole point). None
    when the failure is not an eligible cap death — the same
    predicate ``should_retry`` gates on, so the lane's budget and the
    gate's verdict can never disagree. The lanes call this when
    writing a retry verdict's resume entry (the raised budget rides
    the entry's ``step_budget`` value, str-encoded, checkpoint-safe)."""
    if not is_step_cap(error_text):
        return None
    usage = cap_usage(error_text)
    if usage is None or usage[1] < CAP_RETRY_MIN_TOOL_CALLS:
        return None
    bump = run_limits().supervisor_cap_retry_step_bump
    if bump <= 0:
        return None
    return int(base_max_steps) + int(bump)


class RepairChannel:
    """The lane ↔ supervisor handshake for ONE run — now the window
    registry + supervision ledger OVER the event queue (the mechanics
    live in ``event_queue.SupervisorEventQueue``, the decisions in the
    workers the picker launches). The LANE SIDE is unchanged:
    ``signal_failures`` then ``await_verdicts``, {} on grace 0 /
    timeout / close. Single lane consumer (the looping step node, and
    the review join — the graphs never overlap) by construction."""

    def __init__(self) -> None:
        self.queue = SupervisorEventQueue()
        self._window: Optional[RepairWindow] = None
        # -- the ledger (workers write; should_retry and the belt
        #    filter read) --
        self.attempts: Dict[int, int] = {}   # retries consumed per state
        self.handled: Set[int] = set()       # failures already reviewed
        self.attempted: Set[int] = set()     # one REVIEW attempt per failure
        self.asked: Set[int] = set()  # sids the agent was ASKED about
        # (or the model-unreachable code fallback decided for — a
        # second failure fast-paths no-action through the askable
        # filter once the retry budget is spent either way)
        self.repairs_used: int = 0           # re-opens applied this run
        # The CAP-CLASS lane's own per-run budget (2026-11-15, run
        # d7b24a36): re-opens of step-cap deaths applied with a RAISED
        # step budget — bounded separately from the transient lane so a
        # run of budget-starved spawns cannot retry-storm.
        self.cap_retries_used: int = 0

    # ---- lane side (the contract the lanes call — unchanged) --------
    async def signal_failures(
            self, failures: Sequence[Tuple[int, str]]) -> None:
        """The lane's PAUSE WINDOW opens: register a fresh window for
        the failed (state_id, error) pairs and offer the NODE_FAILED
        event the picker dispatches the RepairWorker for. A stale open
        window (the same lane signalling twice without an answer) is
        resolved ``{}`` first — at most one window waits at a time.
        The offer fires before the lane starts waiting, so a fast
        picker never misses the window."""
        if self.queue.closed or not failures:
            return
        pairs = tuple((int(sid), str(err or "")) for sid, err in failures)
        self._close_window()
        window = RepairWindow(pairs)
        self._window = window
        self.queue.offer(SupervisorEvent(
            kind=EventKind.NODE_FAILED,
            failures=[FailureRef(state_id=sid, error=err)
                      for sid, err in pairs],
            source="lane", window_id=window.id))

    async def await_verdicts(self, *, grace_sec: float) -> Dict[int, Verdict]:
        """Wait out the pause window for the picker's verdicts.
        Returns {} when repair is disabled (grace 0), the window times
        out, or the channel closed — the lane then falls through to
        its normal failure semantics. The window's future is shielded:
        a lane-side timeout never cancels the resolution the worker
        may still land (the DB writes stand either way, as before).
        THE TIMEOUT ABANDON (2026-11-13): on a timeout the window is
        ABANDONED (resolved {} AND cleared from the registry) — the
        timed-out window used to stay registered, so a verdict landing
        after the fall-through still passed every gate and its
        ``reopen_state`` could strand a pending row past the chain's
        finalize (a zombie nothing re-runs, live until the boot sweep).
        A late worker now lands its review row but ``is_open`` refuses
        its row-mutating writes; grace 0 keeps the old semantics (the
        window stays open and a landed verdict still applies — repair
        "disabled" there means the lane never waits, not that a decided
        verdict is vetoed)."""
        window = self._window
        if window is None or grace_sec <= 0 or self.queue.closed:
            return {}
        try:
            return dict(await asyncio.wait_for(
                asyncio.shield(window.future), timeout=grace_sec) or {})
        except asyncio.TimeoutError:
            self.abandon(window.id)
            return {}
        except asyncio.CancelledError:
            # The LANE task itself is being cancelled (a user stop, a
            # runner unwind) — abandon the window exactly like a
            # timeout so the registry does not keep a stale entry whose
            # pending_ids would exclude its sids from the belt scan;
            # the workers' late-verdict guard then refuses a late
            # worker's row writes the same way.
            self.abandon(window.id)
            raise

    # ---- worker side -------------------------------------------------
    def resolve_window(self, window_id: str,
                       verdicts: Dict[int, Verdict]) -> bool:
        """The RepairWorker's answer for ONE window — wakes the waiting
        lane and closes the window. False when the id does not match
        the open window (superseded/closed — the lane is already
        released; the worker's DB writes stand)."""
        window = self._window
        if window is None or window.id != window_id:
            return False
        self._window = None
        window.resolve(verdicts)
        return True

    def abandon(self, window_id: str) -> bool:
        """Release ONE window as TIMED OUT — resolve {} AND clear the
        registry. The mirror of ``resolve_window`` for the lane-side
        grace expiry: the lane wait has already fallen through, and the
        clear is what lets ``is_open`` (the workers' late-verdict guard)
        distinguish a live window's decision from one that must not
        touch the rows anymore. False when the id does not match (a
        concurrent worker resolution may have closed it first — its
        writes stand, they were decided while live)."""
        window = self._window
        if window is None or window.id != window_id:
            return False
        self._window = None
        window.resolve({})
        return True

    def is_open(self, window_id: Optional[str]) -> bool:
        """Is *window_id* still the open window? (The picker drops
        NODE_FAILED events whose window was superseded/closed — their
        lane wait already fell through.)"""
        return (window_id is not None and self._window is not None
                and self._window.id == window_id)

    @property
    def open_window_id(self) -> Optional[str]:
        window = self._window
        return window.id if window is not None else None

    # ---- shared reads --------------------------------------------------
    @property
    def pending_ids(self) -> Set[int]:
        """The state ids currently inside the OPEN window — the belt
        scan excludes them (their verdict belongs to the lane path)."""
        window = self._window
        return {sid for sid, _ in window.failures} if window else set()

    @property
    def closed(self) -> bool:
        return self.queue.closed

    # ---- lifecycle -------------------------------------------------------
    def _close_window(self) -> None:
        """Release the open window with {} (supersede or shutdown)."""
        window, self._window = self._window, None
        if window is not None:
            window.resolve({})

    def close(self) -> None:
        """The run is over — release the lane (its wait returns {}) and
        offer the terminal RUN_SETTLED event (the picker's sweep
        dispatch). Idempotent."""
        self._close_window()
        self.queue.close()
