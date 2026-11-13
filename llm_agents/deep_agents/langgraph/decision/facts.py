"""llm_agents.deep_agents.langgraph.decision.facts — the typed inputs
and outputs of the System One decision templates.

The FACTS are deliberately narrow: the code-filtered decision context
only (error class, capped text heads, streaks, budgets) — never a
transcript or a findings bridge (context hygiene as hard policy; a jev
read is ~0.1s, the facts block must stay small enough to build in
microseconds). The OUTCOME is the calibrated read plus its provenance:
``ok`` is False for every fail-soft exit (host unreachable, contract
breach, below the confidence floor) and the CALLER then runs its own
existing fallback — the engine never picks a fallback action itself.
``action`` still carries the RAW argmax on a below-floor read (and
``probabilities`` the raw read) for the log/telemetry; callers gate on
``ok``, never on ``action``.

The two fact shapes ride the two standing templates
(``templates.ResumeJudgmentDecision`` / ``templates.NodeSpawnDecision``);
the harness call sites OUTSIDE langgraph (``core.loop``,
``supervisor.workers``) never import them — they call the engine's
primitive-kwargs methods and the engine builds the facts here.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ResumeFacts(BaseModel):
    """One failed node, post-code-filter: the transient-class facts the
    supervisor's resumption judgment needs."""

    error_text: str = Field(
        description="The node's recorded error (capped head — the "
        "classifying prefix is what matters).")
    node_title: str = Field(
        default="", description="The failed node's directive title "
        "(capped; '' when unknown).")
    rate_limited: bool = Field(
        description="The code throttle-classifier's answer — a retry "
        "on this class is throttled.")
    attempts: int = Field(
        description="Retries this node already consumed.")
    max_node_retries: int = Field(
        description="The node's retry budget.")
    repairs_used: int = Field(
        description="Re-opens applied this run so far.")
    max_repairs_per_run: int = Field(
        description="The run's repair budget.")


class SpawnFacts(BaseModel):
    """The spawn breaker at its threshold: the script-debugging loop's
    fix history — the fresh-context spawn's would-be handoff, condensed."""

    syntax_streak: int = Field(
        description="Consecutive traceback-class script failures.")
    threshold: int = Field(
        description="The streak the breaker fires at.")
    spawns_used: int = Field(
        description="Repair spawns this conversation already spent.")
    max_spawns: int = Field(
        description="The conversation's spawn budget.")
    script_path: str = Field(
        default="", description="The failing script's path ('' when "
        "unknown).")
    fail_lines: str = Field(
        default="", description="The failed runs' last exception lines "
        "(already capped by the recorder; '' when none).")


class KgCandidate(BaseModel):
    """One KG result entity offered to the relevance read (capped head
    — name + kind + a short summary; never the rendered dossier)."""

    name: str = Field(
        description="The entity's schema-qualified name (capped).")
    kind: str = Field(
        default="", description="The entity_type ('table', 'view', …).")
    summary: str = Field(
        default="", description="The description's capped head.")


class KgRelevanceFacts(BaseModel):
    """One kg_search / kg_expand result, post-traversal: the ranked
    candidates the relevance read judges (the query is the state, the
    candidates are the options)."""

    query: str = Field(
        description="The kg_search / kg_expand query text (capped).")
    scope_label: str = Field(
        default="", description="Which namespace/corpus was searched.")
    candidates: List[KgCandidate] = Field(
        default_factory=list,
        description="The ranked result entities (≤ 16 — the read's "
                    "choice vocabulary adds 'reject' on top).")
    accept_floor: float = Field(
        default=0.15,
        description="Per-candidate probability floor for a multi-answer "
                    "accept; the template's interpret uses it.")
    min_confidence: float = Field(
        default=0.35,
        description="The read's own ok-floor — a multi-answer dossier "
                    "read spreads mass across related entities, so the "
                    "single-pick floor does not apply; measured "
                    "2026-11-12 (real dossiers top out ~0.4 with the "
                    "right ranking, noise rejects read 0.9+).")


class RouteFacts(BaseModel):
    """One LLM response mid-conversation, post-code-filter: the ROUTER's
    facts (2026-11-12). The loop's mechanical pre-filters already ran —
    these facts only reach a template when the response PROPOSES sandbox
    script work (the spawn read) or the node is a scripting node showing
    messiness (the join read); the read judges the judgment call, not
    the detection."""

    node_role: str = Field(
        default="",
        description="This node's role: '' a biz/inference conversation, "
                    "'scripting' a node spawned to write/run scripts, "
                    "'investigator' a failure probe.")
    depth: int = Field(
        default=0,
        description="The node's spawn depth (0 = a root step "
                    "conversation).")
    steps_used: int = Field(
        description="Conversation steps already spent (incl. this one).")
    max_steps: int = Field(
        description="The conversation's step budget.")
    response_head: str = Field(
        default="",
        description="The response's text head (capped — its plan/next-"
                    "step statement is the signal).")
    pending_tools: str = Field(
        default="",
        description="Comma-joined names of the response's proposed tool "
                    "calls (capped).")
    sandbox_calls: int = Field(
        default=0,
        description="How many proposed calls target sandbox script "
                    "write/run/edit tools.")
    fail_streak: int = Field(
        default=0,
        description="Consecutive failed sandbox-tool outcomes.")
    syntax_streak: int = Field(
        default=0,
        description="Consecutive traceback-class script failures.")
    spawns_used: int = Field(
        default=0,
        description="Router spawns this conversation already spent.")
    max_spawns: int = Field(
        default=0,
        description="The conversation's router-spawn budget.")
    goal_head: str = Field(
        default="",
        description="The NODE'S PURPOSE (the run/step goal's head, "
                    "capped) — the DRIFT FORK read (2026-11-13) judges "
                    "the latest context against it.")


class ForkPointCandidate(BaseModel):
    """One candidate PRE-DRIFT checkpoint the fork-point read picks
    among: a completed assistant turn of the drifting conversation
    (label turn_<msg_index>) and its response head."""

    label: str = Field(
        description="The candidate's answer token (turn_<msg_index>) — "
                    "the loop maps it back to a transcript cut index.")
    head: str = Field(
        description="The turn's response head (capped) — the context "
                    "the read conditions on.")


class ForkPointFacts(BaseModel):
    """The ROUTER's FORK-POINT read facts (2026-11-13): the drift fork
    was judged worthwhile — which PREVIOUS LLM context should the fork
    continue from? The candidates are the conversation's most recent
    completed turns (assistant response + its tool results) PLUS an
    explicit 'now' option the caller adds; the raw argmax IS the
    verdict (a below-floor or unknown label lands fork-from-now, the
    safe default)."""

    goal_head: str = Field(
        default="",
        description="The node's purpose the fork re-anchors on (capped "
                    "head).")
    candidates: List[ForkPointCandidate] = Field(
        default_factory=list,
        description="The ≤ 16 most recent checkpoint candidates (the "
                    "jev per-request option ceiling reserves room for "
                    "the caller's 'now' option).")
    steps_used: int = Field(
        description="Conversation steps already spent.")
    max_steps: int = Field(
        description="The conversation's step budget.")


class ProbeFacts(BaseModel):
    """One FAILED spawned child the parent just collected, post-filter:
    the FAILURE-PROBE read's facts (2026-11-12). The probe question is
    STRONGLY PROMPTED toward spawning an investigator — the caller's
    policy spawns on every read except a confident NO."""

    failed_role: str = Field(
        default="scripting",
        description="The failed child's role ('scripting' is the probed "
                    "class).")
    failed_goal: str = Field(
        default="",
        description="The failed child's goal (capped head).")
    error_text: str = Field(
        default="",
        description="The failure's recorded error (capped head).")
    probes_used: int = Field(
        default=0,
        description="Investigator spawns this conversation already "
                    "spent.")
    max_probes: int = Field(
        default=1,
        description="The conversation's investigator budget.")


class ContentFacts(BaseModel):
    """One large TEXT result at a tool seam, post-size-filter: the
    capped head the classification reads condition on (never the whole
    text — a jev read is ~0.1s, the facts stay microseconds-small)."""

    text_head: str = Field(
        description="The text's head (already char-capped by the "
                    "caller — the classifying prefix is what matters).")
    total_chars: int = Field(
        description="The FULL text's char count (context, not read "
                    "input).")
    source: str = Field(
        default="tool",
        description="Where the text came from: 'db' | 'sandbox' | "
                    "'tool'.")
    industry_candidates: List[str] = Field(
        default_factory=list,
        description="The keyword-prefiltered industry names (≤ 26 — "
                    "the jev per-request option ceiling) the industry "
                    "attempt picks among; empty = the question is "
                    "omitted.")
    accept_floor: float = Field(
        default=0.5,
        description="Per-label floor for riding an industry label on "
                    "the envelope; the multi-way industry read is SOFT "
                    "EVIDENCE (position-biased by measurement), so the "
                    "floor is deliberately high.")


class JevDecision(BaseModel):
    """One System One read, mapped onto the caller's vocabulary by the
    template that produced it."""

    action: str = ""
    confidence: float = 0.0
    probabilities: Dict[str, float] = Field(default_factory=dict)
    accept_floor: float = Field(
        default=0.15,
        description="The per-candidate accept floor the template's "
                    "interpret applies to a multi-answer read (the "
                    "facts' knob, carried onto the decision).")
    option_names: List[str] = Field(
        default_factory=list,
        description="The read's candidate option labels (the facts' "
                    "candidates, carried onto the decision so a "
                    "multi-answer template's interpret can expand an "
                    "'all' verdict).")
    ok: bool = False
    note: str = ""
    model: Optional[str] = None
    template: str = ""
