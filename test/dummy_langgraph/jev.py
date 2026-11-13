"""test.dummy_langgraph.jev — the DECISION half of the dataset: the
``decisions.feature`` scenarios turn ONE System One input (the typed
decision facts) into the EXPECTED choice output, and this module serves
them both ways:

* :class:`StubJevDecisionEngine` — the offline engine answering from the
  feature expectations (deterministic; injected into ``run_thread(...,
  decision_engine=...)`` so the REAL langgraph decision lanes walk
  offline, and every read is recorded);
* :class:`DecisionChoiceChecker` — the same scenarios as LIVE
  assertions: each scenario's facts go through the REAL
  ``JevDecisionEngine`` (the resident local-model host, ~0.1s, zero
  spend, deterministic) and the actual argmax choice must equal the
  feature's ``Then the choice is "..."`` at its confidence floor.

One scenario = one decision input:

    @resume-transient-timeout
    Scenario: a transient read timeout is resumed
      Given the resume facts
        ```
        {"error_text": "...", "rate_limited": false, ...}
        ```
      When the resume_judgment template asks whether to resume
      Then the choice is "yes" with confidence at least 0.6
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from llm_agents.deep_agents.langgraph.decision.engine import JevDecisionEngine
from llm_agents.deep_agents.langgraph.decision.facts import (
    ForkPointFacts, SpawnFacts, JevDecision, ProbeFacts, ResumeFacts,
    RouteFacts,
)
from llm_agents.deep_agents.langgraph.decision.templates import (
    TEMPLATES, FailureProbeDecision, NodeSpawnDecision,
    ResumeJudgmentDecision, StepRouteForkDecision,
    StepRouteForkPointDecision, StepRouteSpawnDecision,
    StepRouteJoinDecision,
)
from test.dummy_llm._gherkin import (
    FeatureSpec, ScenarioSpec, load_features,
)
from test.dummy_langgraph.provider import FEATURES_DIR

DECISIONS_FEATURE = "decisions.feature"

_CHOICE_RE = re.compile(
    r'choice is "(.+?)"(?:\s+with confidence at least (\d+(?:\.\d+)?))?')
_FACTS_RE = re.compile(r"the (\w+) facts", re.IGNORECASE)
_TEMPLATE_RE = re.compile(r"the (\w+) template", re.IGNORECASE)

# facts name → the typed facts class + the template that reads it.
_FACTS_TYPES: Dict[str, Tuple[type, type]] = {
    "resume": (ResumeFacts, ResumeJudgmentDecision),
    "spawn": (SpawnFacts, NodeSpawnDecision),
    "route_spawn": (RouteFacts, StepRouteSpawnDecision),
    "route_fork": (RouteFacts, StepRouteForkDecision),
    "route_fork_point": (ForkPointFacts, StepRouteForkPointDecision),
    "route_join": (RouteFacts, StepRouteJoinDecision),
    "probe": (ProbeFacts, FailureProbeDecision),
}


class DecisionScenario(BaseModel):
    """One compiled decision scenario: the typed facts IN, the expected
    choice OUT."""

    tag: str
    facts: Any
    template_key: str
    expected_choice: str
    min_confidence: float = 0.0

    @property
    def template(self):  # noqa: ANN201 — the registry object
        return TEMPLATES[self.template_key]


class _DecisionBook:
    """The compiled decisions.feature dataset (raises at load)."""

    def __init__(self, features: Optional[List[FeatureSpec]] = None,
                 ) -> None:
        sources = features if features is not None else load_features(
            FEATURES_DIR)
        self.scenarios: List[DecisionScenario] = []
        for feature in sources:
            if not feature.path.endswith(DECISIONS_FEATURE):
                continue
            for sc in feature.scenarios:
                self.scenarios.append(self._compile(sc))
        if not self.scenarios:
            raise ValueError(
                f"no decision scenarios found in {DECISIONS_FEATURE}")

    @staticmethod
    def _compile(sc: ScenarioSpec) -> DecisionScenario:
        facts_m = _FACTS_RE.search(
            " ".join(s.text for s in sc.steps_of("Given")))
        tpl_m = _TEMPLATE_RE.search(
            " ".join(s.text for s in sc.steps_of("When")))
        if facts_m is None or tpl_m is None:
            raise ValueError(
                f"@{sc.tag}: a decision scenario needs "
                f'\'Given the <resume|spawn> facts\' and '
                f'\'When the <template_key> template ...\'')
        facts_cls, tpl_cls = _FACTS_TYPES[facts_m.group(1).lower()]
        doc = sc.steps_of("Given")[0].docstring or ""
        try:
            payload = json.loads(doc)
            facts = facts_cls.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 — fail fast, named
            raise ValueError(f"@{sc.tag}: bad facts DocString: {exc}")
        template = TEMPLATES[tpl_cls.key]
        if tpl_m.group(1).lower() != template.key.lower():
            raise ValueError(
                f"@{sc.tag}: When names {tpl_m.group(1)!r} but the "
                f"facts read through template {template.key!r}")
        then = sc.steps_of("Then")[0]
        choice_m = _CHOICE_RE.search(then.text)
        if choice_m is None:
            raise ValueError(
                f'@{sc.tag}: Then must be "the choice is '
                f"'<label>' [with confidence at least X]'")
        return DecisionScenario(
            tag=sc.tag, facts=facts, template_key=template.key,
            expected_choice=choice_m.group(1),
            min_confidence=float(choice_m.group(2) or 0.0))

    def for_template(self, template_key: str) -> List[DecisionScenario]:
        return [s for s in self.scenarios
                if s.template_key == template_key]


def load_decision_book() -> _DecisionBook:
    """The compiled decision dataset (module-level convenience)."""
    return _DecisionBook()


class StubJevDecisionEngine:
    """The duck-typed decision engine answering FROM THE FEATURE
    EXPECTATIONS (the same contract as ``JevDecisionEngine``:
    ``decide_resume`` / ``decide_spawn`` primitive kwargs + the typed
    ``run``; NEVER raises; every read recorded). The chosen scenario is
    the first dataset entry for that template whose recorded facts
    equal the call's facts (exact model dump match) — else the
    template's first scenario. Confidence is 1.0 (the dataset's
    expectation IS the answer offline)."""

    def __init__(self) -> None:
        self._book = load_decision_book()
        self.reads: List[Dict[str, Any]] = []

    async def run(self, template, facts: Any) -> JevDecision:
        key = getattr(template, "key", "")
        scenario = self._pick(key, facts)
        decision = JevDecision(
            action=scenario.expected_choice, confidence=1.0,
            probabilities={}, ok=True, template=key,
            model="stub:feature-dataset")
        decision = template.interpret(decision)
        self.reads.append({
            "template": key, "tag": scenario.tag,
            "action": decision.action,
            "facts": facts.model_dump() if hasattr(facts, "model_dump")
            else facts})
        return decision

    async def decide_resume(self, **kwargs: Any) -> JevDecision:
        return await self.run(
            self._book.for_template(ResumeJudgmentDecision.key)[0].template,
            ResumeFacts(**kwargs))

    async def decide_spawn(self, **kwargs: Any) -> JevDecision:
        return await self.run(
            self._book.for_template(NodeSpawnDecision.key)[0].template,
            SpawnFacts(**kwargs))

    async def decide_route_spawn(self, **kwargs: Any) -> JevDecision:
        return await self.run(
            self._book.for_template(StepRouteSpawnDecision.key)[0].template,
            RouteFacts(**kwargs))

    async def decide_route_fork(self, **kwargs: Any) -> JevDecision:
        return await self.run(
            self._book.for_template(StepRouteForkDecision.key)[0].template,
            RouteFacts(**kwargs))

    async def decide_route_fork_point(self, **kwargs: Any) -> JevDecision:
        return await self.run(
            self._book.for_template(
                StepRouteForkPointDecision.key)[0].template,
            ForkPointFacts(**kwargs))

    async def decide_route_join(self, **kwargs: Any) -> JevDecision:
        return await self.run(
            self._book.for_template(StepRouteJoinDecision.key)[0].template,
            RouteFacts(**kwargs))

    async def decide_failure_probe(self, **kwargs: Any) -> JevDecision:
        return await self.run(
            self._book.for_template(FailureProbeDecision.key)[0].template,
            ProbeFacts(**kwargs))

    def _pick(self, template_key: str,
              facts: Any) -> DecisionScenario:
        wanted = (facts.model_dump()
                  if hasattr(facts, "model_dump") else facts)
        for scenario in self._book.for_template(template_key):
            if scenario.facts.model_dump() == wanted:
                return scenario
        candidates = self._book.for_template(template_key)
        return candidates[0]


class DecisionChoiceChecker:
    """The live half: every decision scenario's facts through the REAL
    ``JevDecisionEngine`` → assert the argmax choice equals the
    feature's expectation. Deterministic (no sampling) and free; a
    report of failures comes back — empty means the dataset holds."""

    def __init__(self, engine: Optional[JevDecisionEngine] = None) -> None:
        self._engine = engine or JevDecisionEngine()
        self._book = load_decision_book()

    async def check(self) -> List[str]:
        """Run every scenario; return the failure lines (empty = all
        choices asserted). Each read may retry ONCE (the host's first
        read after a cold rehydrate can time out — the engine's
        fail-soft lands it as ok=False; a confident-but-below-floor
        note is a verdict, never retried). The judged choice is the RAW
        argmax — the feature's ``Then`` names the model's choice word
        (yes/no), not the template's post-interpret action vocabulary
        (retry-step/no-action) the stub serves."""
        failures: List[str] = []
        for scenario in self._book.scenarios:
            decision = await self._engine.run(scenario.template,
                                              scenario.facts)
            if not decision.ok and not decision.note.startswith(
                    "below confidence floor"):
                decision = await self._engine.run(scenario.template,
                                                  scenario.facts)
            probabilities = decision.probabilities or {}
            raw = (max(probabilities, key=probabilities.get)
                   if probabilities else decision.action)
            if raw != scenario.expected_choice:
                failures.append(
                    f"@{scenario.tag}: choice {raw!r} "
                    f"(p={decision.confidence:.2f}, "
                    f"{decision.note or 'ok'}) != expected "
                    f"{scenario.expected_choice!r}")
            elif decision.confidence < scenario.min_confidence:
                failures.append(
                    f"@{scenario.tag}: confidence "
                    f"{decision.confidence:.2f} < floor "
                    f"{scenario.min_confidence:.2f}")
        return failures
