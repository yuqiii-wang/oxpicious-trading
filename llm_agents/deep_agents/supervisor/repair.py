"""llm_agents.deep_agents.supervisor.repair — the REPAIR LANE's
coordination + policy core (2026-11-06): the event-time counterpart of
the post-run observe sweep.

WHY A CHANNEL: a failed step settles its row, skips the remaining
pending tail and routes the lane to finalize ALL WITHIN ONE SUPERSTEP
of the looping step node — an external poller flipping rows back can
never land between those writes. So the step lane itself opens a
BOUNDED PAUSE WINDOW: on a root-path failure (before the doomed-tail
settle) it signals this channel and waits up to the run's repair
grace for the supervisor poller's verdicts. ``retry-step`` verdicts
re-open the failed rows (``planning.store.reopen_state``) and the
lane's normal routing re-picks them next hop — the run resumes. Any
other outcome (no verdict, ``no-action``, grace expiry, repair
disabled) falls through to the existing failure semantics untouched.

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
from typing import Dict, List, Sequence, Set, Tuple

from pydantic import BaseModel, ConfigDict

from llm_agents.deep_agents.limits import run_limits

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
    class transient, the node's retry budget and the run's repair
    budget unspent. Bounded, code-enforced (never prompt-enforced)."""
    if action != ACTION_RETRY or not is_transient(error_text):
        return False
    limits = run_limits()
    return (channel.attempts.get(state_id, 0)
            < limits.supervisor_max_node_retries
            and channel.repairs_used
            < limits.supervisor_max_repairs_per_run)


class RepairChannel:
    """The lane ↔ poller handshake for ONE run. Single lane consumer
    (the looping step node) and single poller consumer (the
    supervisor session) by construction — plain Events, no queues.

    Lane side: ``signal_failures`` then ``await_verdicts``; poller
    side: ``wait_failures`` (wakes on signal OR after the poll
    interval), then ``set_verdicts`` once. Bookkeeping (attempts,
    handled ids, repairs used) lives here so the final sweep can
    dedupe mid-run-handled failures."""

    def __init__(self) -> None:
        self._lane_wake = asyncio.Event()    # lane -> poller: look now
        self._poll_wake = asyncio.Event()    # interval/poll -> check DB
        self._verdict_ready = asyncio.Event()  # poller -> lane
        self._pending: List[Tuple[int, str]] = []   # (state_id, error)
        self._verdicts: Dict[int, Verdict] = {}
        self._closed = False
        # -- bookkeeping (poller writes; lane reads via await_verdicts) --
        self.attempts: Dict[int, int] = {}   # retries consumed per state
        self.handled: Set[int] = set()       # failures already reviewed
        self.attempted: Set[int] = set()     # one REVIEW attempt per failure
        self.asked: Set[int] = set()  # sids the agent was ASKED about
        # (or the model-unreachable code fallback decided for — a
        # second failure fast-paths no-action through the askable
        # filter once the retry budget is spent either way)
        self.repairs_used: int = 0           # re-opens applied this run

    # ---- lane side -------------------------------------------------
    async def signal_failures(
            self, failures: Sequence[Tuple[int, str]]) -> None:
        """The lane's PAUSE WINDOW opens: hand the failed (state_id,
        error) pairs to the poller. Fires the wake BEFORE the lane
        starts waiting, so a fast poller never misses the window."""
        if self._closed or not failures:
            return
        self._pending = list(failures)
        self._verdicts = {}
        self._verdict_ready.clear()
        self._lane_wake.set()

    async def await_verdicts(self, *, grace_sec: float) -> Dict[int, Verdict]:
        """Wait out the pause window for the poller's verdicts.
        Returns {} when repair is disabled (grace 0), the window
        times out, or the channel closed — the lane then falls
        through to its normal failure semantics."""
        if self._closed or grace_sec <= 0 or not self._pending:
            return {}
        try:
            await asyncio.wait_for(self._verdict_ready.wait(),
                                   timeout=grace_sec)
        except asyncio.TimeoutError:
            return {}
        return dict(self._verdicts)

    # ---- poller side -----------------------------------------------
    async def wait_failures(self, *, interval_sec: float) -> bool:
        """Block until the lane signals OR the poll interval lapses
        (False = interval lapse — poll the DB as the independent
        belt; True = lane signalled — take _pending now)."""
        self._lane_wake.clear()
        self._poll_wake.clear()
        waiters = [asyncio.create_task(self._lane_wake.wait()),
                   asyncio.create_task(self._poll_wake.wait())]
        try:
            done, _ = await asyncio.wait(
                waiters, timeout=interval_sec,
                return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()
        return bool(done) and waiters[0] in done

    def take_pending(self) -> List[Tuple[int, str]]:
        """Consume the lane's signalled failures (empty when the wake
        was the interval — the poller then reads the DB itself)."""
        pending, self._pending = self._pending, []
        return pending

    @property
    def pending_ids(self) -> Set[int]:
        """The state ids currently signalled and unanswered — the belt
        poll excludes them (their verdict belongs to the lane path)."""
        return {sid for sid, _ in self._pending}

    def set_verdicts(self, verdicts: Dict[int, Verdict]) -> None:
        """The poller's answer — wakes the waiting lane."""
        self._verdicts = dict(verdicts)
        self._verdict_ready.set()

    def poke_poll(self) -> None:
        """Ask the poller to look at the DB on its next loop tick
        (used by the session's exit path)."""
        self._poll_wake.set()

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """The run is over — release the lane (its wait returns {})
        and let the session's final sweep proceed."""
        self._closed = True
        self._verdicts = {}
        self._verdict_ready.set()
        self._lane_wake.set()
        self._poll_wake.set()
