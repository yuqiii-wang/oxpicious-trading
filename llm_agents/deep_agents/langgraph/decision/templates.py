"""llm_agents.deep_agents.langgraph.decision.templates — the decision
TEMPLATES: each typical harness judgment as ONE reusable object that
owns its System One question vocabulary and the raw-read → verdict
mapping, so a new decision capability is one subclass (the same
extension shape as the supervisor's workers: a template + a registry
entry + one call site; the engine's read path never changes).

The two standing templates cover the harness's two named judgment
shapes:

* :class:`ResumeJudgmentDecision` — "should the supervisor resume a
  failed node?" (the repair lane's verdict);
* :class:`NodeSpawnDecision` — "is spawning a fresh-context sub node
  worthwhile now?" (the spawn breaker's gate).

Both are NOUL questions on purpose: the 2026-11-11/12 serving probes
showed this model family's calibrated reads are clean on the yes/no
vocabulary (anchors 0.80/0.12) and position-biased on lettered
multi-way choices — decompose a k-way pick into noul templates.

The question TEXTS are part of the serving contract (the probability
read conditions on them) — keep them stable and re-probe when you must
edit. Pure module: facts in, plain request dicts out — stub-testable
without a host.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, Tuple

from llm_agents.deep_agents.langgraph.decision.facts import (
    ContentFacts, SpawnFacts, JevDecision, KgRelevanceFacts, ProbeFacts,
    ResumeFacts, RouteFacts,
)

# The repair-lane Action vocabulary (supervisor.repair.ACTION_*), so a
# resume verdict rides the existing should_retry gate unchanged.
ACTION_RETRY = "retry-step"
ACTION_NO = "no-action"

_HEAD = 400


class DecisionTemplate(ABC):
    """ONE harness judgment as a reusable System One read (see module
    docstring). Subclasses own THREE things: the typed facts they read,
    the question payload (state + questions), and the raw-read →
    verdict mapping (:meth:`interpret`)."""

    key: ClassVar[str] = ""
    action_key: ClassVar[str] = ""

    @abstractmethod
    def build_request(self, facts: Any) -> Tuple[Dict[str, Any],
                                                 Dict[str, Any]]:
        """The facts → (state, questions) — the ``/v1/systemone``
        payload. ``questions`` carries exactly one entry keyed
        ``action_key``."""

    def interpret(self, decision: JevDecision) -> JevDecision:
        """The raw argmax → the caller's action vocabulary. Default:
        identity (the raw yes/no / option label IS the verdict)."""
        return decision


class ResumeJudgmentDecision(DecisionTemplate):
    """The supervisor's NODE RESUMPTION judgment — one noul
    ("resume?"). A confident read maps onto the repair lane's actions:
    YES → ``retry-step``, NO → ``no-action`` (never an auto-ignore —
    dispensability is the generative fallback's judgment). The worker
    uses ONLY the affirming side as its fast path; the no side is the
    template's honest mapping, not a lane veto."""

    key: ClassVar[str] = "resume_judgment"
    action_key: ClassVar[str] = "resume_worthwhile"

    def build_request(self, facts: ResumeFacts) -> Tuple[Dict[str, Any],
                                                         Dict[str, Any]]:
        state: Dict[str, Any] = {
            "decision": "supervisor repair verdict",
            "failed_node": (facts.node_title or "").strip()[:200],
            "error": (facts.error_text or "").strip()[:_HEAD],
            "rate_limited": facts.rate_limited,
            "retries_used": facts.attempts,
            "retries_allowed": facts.max_node_retries,
            "run_repairs_used": facts.repairs_used,
            "run_repairs_allowed": facts.max_repairs_per_run,
            "retry_policy": (
                "a resumed node re-runs from its own context "
                "(rate-limited errors wait out a cooldown first — a "
                "retry does not hammer the provider)"),
        }
        questions = {
            self.action_key: {
                "type": "noul",
                "instructions": (
                    "A pipeline step failed with the recorded error. "
                    "The error class is transient and the node's retry "
                    "budget is available. Is resuming this node (a "
                    "fresh re-run of its own conversation, keeping the "
                    "rest of the run intact) likely to clear the "
                    "failure and worth the node budget?"),
            },
        }
        return state, questions

    def interpret(self, decision: JevDecision) -> JevDecision:
        if not decision.ok:
            return decision
        return decision.model_copy(update={
            "action": (ACTION_RETRY if decision.action == "yes"
                       else ACTION_NO)})


class NodeSpawnDecision(DecisionTemplate):
    """The NODE SPAWN gate — one noul: is a fresh-context repair child
    worth spawning NOW (the streak reached its threshold; the budgets
    allow it — those are code gates, the judgment is whether the
    failure pattern is one a clean-context child fixes). The verdict
    is the raw yes/no."""

    key: ClassVar[str] = "node_spawn"
    action_key: ClassVar[str] = "spawn_worthwhile"

    def build_request(self, facts: SpawnFacts) -> Tuple[Dict[str, Any],
                                                       Dict[str, Any]]:
        state: Dict[str, Any] = {
            "decision": "fresh-context repair spawn gate",
            "syntax_streak": facts.syntax_streak,
            "threshold": facts.threshold,
            "spawns_used": facts.spawns_used,
            "max_spawns": facts.max_spawns,
            "script_path": (facts.script_path or "").strip()[:200],
            "recent_failures": (facts.fail_lines or "").strip()[:800],
        }
        questions = {
            self.action_key: {
                "type": "noul",
                "instructions": (
                    "The script-debugging loop just recorded its "
                    f"{facts.syntax_streak}-th consecutive "
                    "traceback-class failure. The failure lines show a "
                    "mechanical, repeatedly-patching debug session "
                    "whose context is burning. Is handing the script "
                    "to a fresh-context repair child (clean slate, "
                    "shared sandbox) likely to fix what the burning "
                    "parent cannot?"),
            },
        }
        return state, questions


class KgRelevanceDecision(DecisionTemplate):
    """The KG RELEVANCE gate (2026-11-12) — one ``choice`` read over a
    kg_search / kg_expand result's ranked entities PLUS explicit
    ``all`` and ``reject`` options: which of the found entities
    actually answer the query — every one of them, some of them, or
    none. The verdict is a MULTI-ANSWER: every candidate
    whose probability clears the accept floor ships to the conversation
    (``action`` = comma-joined accepted names), ``reject`` when the
    reject option wins the argmax or nothing clears the floor (the tool
    then sends only the synonym-loop nudge). POSITION-BIAS CAVEAT: the
    2026-11-11/12 probes showed lettered multi-way reads read biased
    for SINGLE picks — this template treats the distribution as soft
    evidence (floored per-label accepts, a hard argmax only for
    reject) and MUST be validated against obvious-anchor probes
    (``temp_scripts/probe_kg_gate_live.py``) before the gate is
    trusted in a lane; a below-floor read keeps today's full render.
    MEASURED (2026-11-12, ``temp_scripts/probe_kg_gate_live.py``): the
    anchor reads are decisive (a clearly-relevant dossier 0.80, an
    obviously-noise one reject 0.91); REAL dossiers spread mass across
    related entities (top ~0.2-0.4 with the RIGHT ranking — the two
    RSI tables led the RSI query) — so the read carries its OWN ok
    floor (``jev_kg_confidence``, default 0.35) instead of the
    single-pick ``jev_min_confidence``, and per-candidate accepts are
    floored separately (``jev_kg_accept_floor``)."""

    key: ClassVar[str] = "kg_relevance"
    action_key: ClassVar[str] = "relevant_entities"
    REJECT = "reject"
    ALL = "all"

    def build_request(
            self, facts: KgRelevanceFacts) -> Tuple[Dict[str, Any],
                                                    Dict[str, Any]]:
        criteria: Dict[str, str] = {
            c.name: f"{c.kind}: {c.summary}".strip(": ") or c.name
            for c in facts.candidates
        }
        criteria[self.ALL] = (
            "EVERY candidate answers the query — accept all of them")
        criteria[self.REJECT] = (
            "NONE of the entities answer the query — the result is noise "
            "for it")
        state: Dict[str, Any] = {
            "decision": "knowledge-graph result relevance",
            "query": (facts.query or "").strip()[:400],
            "corpus": (facts.scope_label or "").strip()[:200],
        }
        questions = {
            self.action_key: {
                "type": "choice",
                "instructions": (
                    "A semantic search over a trading data knowledge "
                    "graph answered with the candidate entities below. "
                    "Which candidates genuinely match the query's "
                    "business meaning? Several may match; choose every "
                    "clearly relevant one, or reject when none do. Do "
                    "not choose an entity merely because its name "
                    "shares a keyword with the query — its description "
                    "must carry the queried concept."),
                "criteria": criteria,
            },
        }
        return state, questions

    def interpret(self, decision: JevDecision) -> JevDecision:
        if not decision.ok:
            return decision
        probabilities = decision.probabilities
        floor = decision.accept_floor
        # the explicit "all" option: accept EVERY candidate (the facts'
        # option names, minus the meta options themselves)
        if decision.action == self.ALL:
            names = [name for name in decision.option_names
                     if name not in (self.ALL, self.REJECT)]
            if names:
                return decision.model_copy(update={"action": ",".join(names)})
            return decision.model_copy(update={"action": self.REJECT})
        if probabilities.get(self.REJECT, 0.0) >= decision.confidence \
                and decision.action == self.REJECT:
            return decision.model_copy(update={"action": self.REJECT})
        accepted = sorted(
            (name for name, p in probabilities.items()
             if name not in (self.REJECT, self.ALL) and p >= floor),
            key=probabilities.get, reverse=True)
        if not accepted:
            return decision.model_copy(update={"action": self.REJECT})
        return decision.model_copy(
            update={"action": ",".join(accepted)})


class StepRouteSpawnDecision(DecisionTemplate):
    """The ROUTER's SPAWN read (2026-11-12) — one noul: is the coding
    work this response just proposed a self-contained SUB-ASSISTANT
    task a fresh scripting sub node should take over, keeping this
    conversation on its own biz/inference chain? Fires only after the
    loop's mechanical pre-filter (the response proposes sandbox
    write/run/edit calls) and never in a node that is itself scripting
    (the no-re-spawn rule — a scripting node's coding is ITS work, not
    another spawn's). Affirm-only at the call site: only a confident YES
    preempts the calls; a below-floor or unavailable read continues
    (the model executes its own plan)."""

    key: ClassVar[str] = "route_spawn"
    action_key: ClassVar[str] = "spawn_the_coding"

    def build_request(self, facts: RouteFacts) -> Tuple[Dict[str, Any],
                                                        Dict[str, Any]]:
        state: Dict[str, Any] = {
            "decision": "router: spawn coding to a sub node",
            "node_role": facts.node_role or "biz-inference",
            "depth": facts.depth,
            "steps_used": facts.steps_used,
            "max_steps": facts.max_steps,
            "response_head": (facts.response_head or "").strip()[:_HEAD],
            "pending_tools": (facts.pending_tools or "").strip()[:200],
            "sandbox_calls": facts.sandbox_calls,
            "router_spawns_used": facts.spawns_used,
            "router_spawns_max": facts.max_spawns,
        }
        questions = {
            self.action_key: {
                "type": "noul",
                "instructions": (
                    "A trading-research agent's latest reply proposes "
                    f"{facts.sandbox_calls} sandbox script tool call(s) — "
                    "writing or running Python. Its text says what it "
                    "plans next. Is that coding work a SELF-CONTAINED "
                    "sub-assistant task (script writing and execution "
                    "with a clear deliverable) that a fresh scripting "
                    "sub node should take over — keeping this "
                    "conversation focused on its business reasoning — "
                    "rather than a continuity of this conversation's "
                    "own inference chain?"),
            },
        }
        return state, questions


class StepRouteForkDecision(DecisionTemplate):
    """The ROUTER's DRIFT-FORK read (2026-11-13, reframed 2026-11-13
    from the continuation-cost framing to the DRIFT framing): one noul
    asked after EVERY tool-calling response in a non-scripting node
    with fork budget — has the latest context GRADUALLY DEVIATED from
    this node's purpose, such that a fork sub node should take over,
    continuing from an EARLIER (pre-drift) point of this conversation
    while the parent awaits? The fork re-anchors the node's purpose on
    the pre-drift context instead of letting the drifted tail spend
    the node's remaining budget. Affirm-only at the call site: a no /
    below-floor / unavailable read continues the node's own plan; a
    confident yes is followed by the ``route_fork_point`` read picking
    how far back to fork."""

    key: ClassVar[str] = "route_fork"
    action_key: ClassVar[str] = "fork_the_work"

    def build_request(self, facts: RouteFacts) -> Tuple[Dict[str, Any],
                                                        Dict[str, Any]]:
        state: Dict[str, Any] = {
            "decision": "router: the context drifts from the node's "
                        "purpose — fork from a pre-drift point",
            "node_purpose": (facts.goal_head or "").strip()[:_HEAD],
            "node_role": facts.node_role or "biz-inference",
            "depth": facts.depth,
            "steps_used": facts.steps_used,
            "max_steps": facts.max_steps,
            "response_head": (facts.response_head or "").strip()[:_HEAD],
            "pending_tools": (facts.pending_tools or "").strip()[:200],
            "sandbox_calls": facts.sandbox_calls,
            "fail_streak": facts.fail_streak,
            "syntax_streak": facts.syntax_streak,
            "router_forks_used": facts.spawns_used,
            "router_forks_max": facts.max_spawns,
        }
        questions = {
            self.action_key: {
                "type": "noul",
                "instructions": (
                    "A trading-research agent node works under a fixed "
                    "purpose. Its latest replies have GRADUALLY drifted "
                    "from that purpose — chasing side details, re-doing "
                    "settled work, or wandering off the goal — while "
                    "burning its step budget (it has spent "
                    f"{facts.steps_used} of {facts.max_steps}). Should "
                    "the node FORK now: a sub node inherits the "
                    "conversation from an EARLIER, pre-drift point and "
                    "completes the node's purpose from there, rather "
                    "than this conversation continuing its drifted "
                    "line? Judge drift, not difficulty: a hard but "
                    "on-purpose line of work is NOT drift."),
            },
        }
        return state, questions


class StepRouteForkPointDecision(DecisionTemplate):
    """The ROUTER's FORK-POINT read (2026-11-13) — one ``choice``,
    asked only after the drift fork read affirmed: from WHICH previous
    LLM context should the fork continue? The criteria are the
    conversation's most recent completed turns (assistant response +
    its tool results — each a self-consistent checkpoint) plus an
    explicit ``now`` option. The raw argmax label IS the verdict: the
    caller maps ``turn_<msg_index>`` to a transcript cut; ``now``, an
    unknown label or a below-floor read lands fork-from-now (inherit
    everything — the safe default). POSITION-BIAS CAVEAT: lettered
    multi-way picks are soft evidence by measurement — the candidates
    carry DISTINCT response heads (the semantics, not the order, must
    drive the pick) and this template MUST be validated against
    obvious-anchor probes before the lane is trusted (the decisions
    dataset's live checker measures it per release)."""

    key: ClassVar[str] = "route_fork_point"
    action_key: ClassVar[str] = "fork_from"

    NOW = "now"

    def build_request(self, facts: ForkPointFacts) -> Tuple[Dict[str, Any],
                                                            Dict[str, Any]]:
        criteria: Dict[str, str] = {
            c.label: c.head for c in facts.candidates}
        criteria[self.NOW] = (
            "fork from the CURRENT state — inherit the whole "
            "conversation including the drifted tail")
        state: Dict[str, Any] = {
            "decision": "router: pick the fork point (a previous LLM "
                        "context) for the drift fork",
            "node_purpose": (facts.goal_head or "").strip()[:_HEAD],
            "steps_used": facts.steps_used,
            "max_steps": facts.max_steps,
        }
        questions = {
            self.action_key: {
                "type": "choice",
                "instructions": (
                    "A research node's conversation drifted from its "
                    "purpose; a fork will continue from ONE of the "
                    "checkpoint turns below (each states where that "
                    "turn's reply stood). Pick the EARLIEST checkpoint "
                    "that is (a) still ON PURPOSE — before the drift "
                    "began — and (b) rich enough to continue from "
                    "(its findings carry the node's established "
                    "evidence). Do not pick a checkpoint whose own "
                    "reply already shows the drift; do not pick 'now' "
                    "unless every checkpoint is already drifted."),
                "criteria": criteria,
            },
        }
        return state, questions


class StepRouteJoinDecision(DecisionTemplate):
    """The ROUTER's JOIN read (2026-11-12) — one noul: is this SCRIPTING
    node's debug loop messy enough that the node should STOP now and
    hand its current state back to the parent chain? Fires only in a
    scripting-role node showing messiness (failed sandbox outcomes or a
    mostly-spent step budget — code filters, not judgment). Affirm-only
    at the call site: only a confident YES stops the node; the nudge /
    breaker lanes judge everything else."""

    key: ClassVar[str] = "route_join"
    action_key: ClassVar[str] = "stop_and_join"

    def build_request(self, facts: RouteFacts) -> Tuple[Dict[str, Any],
                                                        Dict[str, Any]]:
        state: Dict[str, Any] = {
            "decision": "router: stop a messy scripting node",
            "node_role": facts.node_role or "scripting",
            "steps_used": facts.steps_used,
            "max_steps": facts.max_steps,
            "response_head": (facts.response_head or "").strip()[:_HEAD],
            "pending_tools": (facts.pending_tools or "").strip()[:200],
            "sandbox_fail_streak": facts.fail_streak,
            "syntax_streak": facts.syntax_streak,
        }
        questions = {
            self.action_key: {
                "type": "noul",
                "instructions": (
                    "A scripting sub node is debugging its script. Its "
                    f"recent record shows {facts.fail_streak} failed "
                    f"sandbox outcome(s) and {facts.syntax_streak} "
                    "traceback-class failure(s), and it has spent "
                    f"{facts.steps_used} of {facts.max_steps} steps. Is "
                    "the scripting getting MESSY — repeated failures, "
                    "context burning — such that this node should STOP "
                    "now and join its parent chain with what it has "
                    "(the parent decides what happens next), rather "
                    "than continuing to patch?"),
            },
        }
        return state, questions


class FailureProbeDecision(DecisionTemplate):
    """The FAILURE-PROBE read (2026-11-12) — one noul, STRONGLY PROMPTED
    toward the spawn: a scripting sub node just failed and was joined
    back; should the parent spawn an INVESTIGATOR sub node (authorized
    to inspect the failed node's recorded traffic — its node id, llm
    request chain, context payloads) to find out why? The caller's
    policy is the inverse of the affirm-only lanes: every read spawns
    (bounded by the probe budget) EXCEPT a confident NO — the question
    text itself leans yes on purpose."""

    key: ClassVar[str] = "failure_probe"
    action_key: ClassVar[str] = "spawn_investigator"

    def build_request(self, facts: ProbeFacts) -> Tuple[Dict[str, Any],
                                                        Dict[str, Any]]:
        state: Dict[str, Any] = {
            "decision": "failure probe: spawn an investigator",
            "failed_role": facts.failed_role,
            "failed_goal": (facts.failed_goal or "").strip()[:_HEAD],
            "error": (facts.error_text or "").strip()[:_HEAD],
            "probes_used": facts.probes_used,
            "probes_max": facts.max_probes,
            "authorization": (
                "the investigator receives the failed node's state id, "
                "llm request chain id and context references, and may "
                "search them (search_thread_context) and read the "
                "shared sandbox session"),
        }
        questions = {
            self.action_key: {
                "type": "noul",
                "instructions": (
                    "A scripting sub node of a trading-research run "
                    "just FAILED and its failure was joined back to the "
                    "parent, which still needs the work done. The "
                    "failure's cause is unknown to the parent. A fresh "
                    "investigator sub node — authorized to inspect the "
                    "failed node's recorded traffic (its node id, its "
                    "llm request chain, its context payloads) and the "
                    "shared sandbox — is the fastest way to learn WHY "
                    "it failed before deciding the next attempt. "
                    "Should the parent spawn that investigator now?"),
            },
        }
        return state, questions


class ContentSentimentDecision(DecisionTemplate):
    """The TEXT SENTIMENT read (2026-11-12) — one noul: is this tool
    result's text positive in tone? Off-context since 2026-11-13 (the
    read records a decision step in ``llm.deep_agent_aux_steps``; its
    result never rides the envelope into the conversation), never a
    gate. NOUL on purpose (the calibrated strength)."""

    key: ClassVar[str] = "content_sentiment"
    action_key: ClassVar[str] = "positive_tone"

    def build_request(self, facts: ContentFacts) -> Tuple[Dict[str, Any],
                                                          Dict[str, Any]]:
        state: Dict[str, Any] = {
            "decision": "tool-result text sentiment",
            "source": facts.source,
            "total_chars": facts.total_chars,
            "text_head": (facts.text_head or "").strip()[:_HEAD],
        }
        questions = {
            self.action_key: {
                "type": "noul",
                "instructions": (
                    "The text head below is the start of a large tool "
                    "result that entered a trading research agent's "
                    "conversation. Is its tone POSITIVE (good news, "
                    "gains, success, optimism) rather than negative or "
                    "neutral?"),
            },
        }
        return state, questions


class ContentDomainDecision(DecisionTemplate):
    """The TEXT DOMAIN read (2026-11-12) — one small ``choice`` over
    the COARSE content families (8, semantically far apart — the
    measured position-bias caveat applies to long near-synonym lists,
    not this one). Off-context since 2026-11-13 (records a decision
    step; the result never rides the envelope)."""

    key: ClassVar[str] = "content_domain"
    action_key: ClassVar[str] = "domain"
    DOMAINS: ClassVar[Dict[str, str]] = {
        "market_data": "numeric market data — prices, bars, indicator "
                       "tables",
        "news": "news article or headline prose",
        "research": "research note / analysis prose with reasoning",
        "table_dump": "a generic tabular dump (no market semantics)",
        "error_log": "an error, traceback or log output",
        "code": "source code or a script",
        "macro": "macro / policy / economic commentary",
        "other": "none of the above fit",
    }

    def build_request(self, facts: ContentFacts) -> Tuple[Dict[str, Any],
                                                          Dict[str, Any]]:
        state: Dict[str, Any] = {
            "decision": "tool-result text domain",
            "source": facts.source,
            "text_head": (facts.text_head or "").strip()[:_HEAD],
        }
        questions = {
            self.action_key: {
                "type": "choice",
                "instructions": (
                    "Which coarse family does this tool result's text "
                    "belong to? Pick the ONE best fit from the "
                    "candidates."),
                "criteria": dict(self.DOMAINS),
            },
        }
        return state, questions


class ContentIndustryDecision(DecisionTemplate):
    """The INDUSTRY ATTEMPT (2026-11-12, the user's industry-level
    directive) — one ``choice`` over ≤26 keyword-prefiltered industry
    names from the repo's ``stats.industry_identity`` vocabulary. SOFT
    EVIDENCE ONLY: the probes showed lettered multi-way reads are
    position-biased, so the caller applies a HIGH per-label floor
    (``facts.accept_floor``) and treats the labels as hints, never
    verdicts; the read is off-context since 2026-11-13 (records a
    decision step, never rides the envelope). Empty candidates → the
    question is omitted (the engine skips the read)."""

    key: ClassVar[str] = "content_industry"
    action_key: ClassVar[str] = "industry"
    OTHER = "none"

    def build_request(self, facts: ContentFacts) -> Tuple[Dict[str, Any],
                                                          Dict[str, Any]]:
        criteria: Dict[str, str] = {name: f"the {name} industry"
                                    for name in facts.industry_candidates}
        criteria[self.OTHER] = "none of the candidate industries fit"
        state: Dict[str, Any] = {
            "decision": "tool-result text industry classification",
            "source": facts.source,
            "text_head": (facts.text_head or "").strip()[:_HEAD],
        }
        questions = {
            self.action_key: {
                "type": "choice",
                "instructions": (
                    "Which industry does this tool result's text most "
                    "clearly discuss? The candidates were prefiltered "
                    "from the full industry vocabulary by keyword "
                    "match — pick the ONE best supported by the text, "
                    "or 'none' when the text mentions none of them "
                    "substantively."),
                "criteria": criteria,
            },
        }
        return state, questions


# The standing registry — a NEW harness decision = one subclass above +
# one entry here + one call site; the engine's read path is shared.
TEMPLATES: Dict[str, DecisionTemplate] = {
    t.key: t() for t in (ResumeJudgmentDecision, NodeSpawnDecision,
                         KgRelevanceDecision, StepRouteSpawnDecision,
                         StepRouteForkDecision, StepRouteForkPointDecision,
                         StepRouteJoinDecision, FailureProbeDecision,
                         ContentSentimentDecision,
                         ContentDomainDecision, ContentIndustryDecision)
}
