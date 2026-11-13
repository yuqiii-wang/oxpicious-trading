"""llm_agents.deep_agents.langgraph.decision — the SYSTEM ONE decision
module of the langgraph layer (2026-11-12, migrated from the package
root ``decision/``): typed harness decisions answered by the resident
local-model host's jev models (``POST /v1/systemone``, calibrated
probabilities) instead of — or before — a generative System Two ask.

WHY: the harness's judgment points split by the DeepSeek Harness rule
"code filters first, the model judges only what passes". The code
filters already exist (the repair lane's transient classifier, the
spawn breaker's hard thresholds); what passes them is a small
CATEGORICAL judgment. That is exactly the jev contract's shape — one
forward, calibrated probabilities, ~0.1s on the local GPU, zero
provider spend — the cheap mechanical judge the premium model stops
paying for (per-model routing). The System Two ask stays as the
fallback: jev unavailable or unconfident → the caller's existing lane
runs untouched.

STRUCTURE (the template pattern — a NEW harness decision is one
subclass, not new engine code):

* :mod:`.templates` — the :class:`~.templates.DecisionTemplate` ABC +
  the standing registry: ``resume_judgment`` (should the supervisor
  resume a failed node? → the repair lane's verdict), ``node_spawn``
  (is a fresh-context sub node worthwhile now? → the spawn breaker's
  gate) and the ROUTER TRIO (2026-11-12): ``route_spawn`` /
  ``route_join`` / ``failure_probe`` — after every LLM response the
  injected engine routes the next work (inference / spawn the coding to
  a scripting sub node / join a messy scripting node back to its
  parent; a failed scripting child gets an AUTHORIZED investigator);
* :mod:`.engine` — :class:`JevDecisionEngine`, the one read path every
  template shares, plus the primitive-kwargs conveniences
  (:meth:`~.engine.JevDecisionEngine.decide_resume`,
  :meth:`~.engine.JevDecisionEngine.decide_spawn`,
  :meth:`~.engine.JevDecisionEngine.decide_route_spawn`,
  :meth:`~.engine.JevDecisionEngine.decide_route_join`,
  :meth:`~.engine.JevDecisionEngine.decide_failure_probe`);
* :mod:`.facts` — the narrow typed inputs and the
  :class:`~.facts.JevDecision` outcome.

LAYERING: this module lives in the COMPOSING layer, and the live call
sites sit outside it by design — ``core.loop`` (the spawn gate) and
``supervisor.workers`` (the verdict ask) hold a duck-typed INJECTED
engine and call the primitive-kwargs methods, importing nothing from
langgraph at runtime ("core knows nothing of langgraph"; the runner
and ``super_run.chain`` wire
:meth:`JevDecisionEngine.active`). ``None`` keeps the legacy lanes,
which keeps every existing stub test and dummy-harness probe
deterministic; the ``jev_decisions`` knob (``deep_agent_config.json``)
and the ``JEV_DECISIONS=0`` env kill-switch turn the lane off.
"""
from llm_agents.deep_agents.langgraph.decision.engine import (
    JevDecisionEngine,
)
from llm_agents.deep_agents.langgraph.decision.facts import (
    ContentFacts, SpawnFacts, JevDecision, KgCandidate, KgRelevanceFacts,
    ProbeFacts, ResumeFacts, RouteFacts,
)
from llm_agents.deep_agents.langgraph.decision.templates import (
    ContentDomainDecision, ContentIndustryDecision,
    ContentSentimentDecision, DecisionTemplate, FailureProbeDecision,
    KgRelevanceDecision, NodeSpawnDecision, ResumeJudgmentDecision,
    StepRouteSpawnDecision, StepRouteJoinDecision, TEMPLATES,
)

__all__ = [
    "ContentDomainDecision", "ContentFacts", "ContentIndustryDecision",
    "ContentSentimentDecision", "DecisionTemplate",
    "FailureProbeDecision", "SpawnFacts", "JevDecision",
    "JevDecisionEngine", "KgCandidate", "KgRelevanceDecision",
    "KgRelevanceFacts", "NodeSpawnDecision", "ProbeFacts", "ResumeFacts",
    "ResumeJudgmentDecision", "RouteFacts", "StepRouteSpawnDecision",
    "StepRouteJoinDecision", "TEMPLATES",
]
