"""test.dummy_llm._gherkin — the strict MINIMAL Gherkin parser behind
the dummy llm double (the request→response + kg/jev feature datasets).

The feature files under ``features/`` ARE the scenario dataset; this
module only turns them into typed specs. Supported grammar (canonical
cucumber.io split — Given = the INPUT the model is handed, When = the
discriminating trigger, Then = the EXPECTED output; ``And``/``But``/``*``
continue the preceding clause; one ``Scenario`` = one turn; the ``@tag``
above it is the turn's stable fixture name; file order is the chain
order; ```-fenced DocStrings carry the payloads):

    @plan-proposal
    Scenario: the planner proposes the plan
      Given the system contains "planning node"
      When the planner is asked for the plan proposal
      Then the reply is
        ```
        {"steps": [...]}
        ```

Validation happens AT LOAD: a scenario needs its @tag, at least one
Given/When step and exactly one Then; the parser raises
``GherkinParseError`` naming the file and line — a broken dataset fails
fast, never silently matches nothing.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

# The step keywords this grammar knows (``*`` is Gherkin's neutral
# continuation keyword).
_STEP_KEYWORDS = ("Given", "When", "Then", "And", "But", "*")

_SCENARIO_RE = re.compile(r"^Scenario:(\s+.*)$")
_TAG_RE = re.compile(r"^(@\S+(?:\s+@\S+)*)\s*$")
_FEATURE_RE = re.compile(r"^Feature:(\s+.*)$")


class GherkinParseError(ValueError):
    """One broken feature file — message carries file path + line no."""


class StepSpec(BaseModel):
    """One Gherkin step: its keyword (Given/When/Then/...) and the text
    after it; ``docstring`` carries the ```-fenced body when the step
    has one (Then steps' replies / facts payloads)."""

    keyword: str
    text: str
    docstring: Optional[str] = None
    line: int = 0


class ScenarioSpec(BaseModel):
    """One turn: the ``@tag`` fixture name, the prose name, its steps in
    file order."""

    tag: str
    name: str
    steps: List[StepSpec] = Field(default_factory=list)
    line: int = 0

    def steps_of(self, *keywords: str) -> List[StepSpec]:
        """The steps whose keyword (normalized: And/But/* inherit the
        preceding clause) is one of ``keywords``."""
        return [s for s in self.steps if s.keyword in keywords]


class FeatureSpec(BaseModel):
    """One .feature file: its title and the scenarios in file order."""

    name: str
    path: str
    scenarios: List[ScenarioSpec] = Field(default_factory=list)


class _GherkinParser:
    """The line-oriented parser (one file in, one FeatureSpec out)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lines = path.read_text(encoding="utf-8").splitlines()

    def parse(self) -> FeatureSpec:
        feature = FeatureSpec(name="", path=str(self._path))
        scenario: Optional[ScenarioSpec] = None
        pending_tag: Optional[str] = None
        step: Optional[StepSpec] = None
        fence: Optional[str] = None  # the OPEN delimiter (``` or """)
        doc_lines: List[str] = []
        for idx, raw in enumerate(self._lines, start=1):
            if fence is not None:
                if raw.strip() == fence:
                    assert step is not None
                    step.docstring = "\n".join(doc_lines)
                    fence = None
                    doc_lines = []
                else:
                    doc_lines.append(raw)
                continue
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line in ('```', '"""') and step is not None:
                fence = line
                continue
            tag = _TAG_RE.match(line)
            if tag:
                # A tag line belongs to the NEXT scenario (the previous
                # one may still be `current` until its Scenario: line
                # arrives) — hold it as pending.
                pending_tag = tag.group(1).split()[0].lstrip("@")
                continue
            feature_m = _FEATURE_RE.match(line)
            if feature_m:
                feature.name = feature_m.group(1).strip()
                continue
            scenario_m = _SCENARIO_RE.match(line)
            if scenario_m:
                scenario = ScenarioSpec(
                    tag=pending_tag or "",
                    name=scenario_m.group(1).strip(), line=idx)
                pending_tag = None
                feature.scenarios.append(scenario)
                step = None
                continue
            # Prose BEFORE the first Scenario is the Feature
            # description (descriptive only — never a predicate); step
            # keywords only bind inside a Scenario.
            if scenario is None:
                feature.name += " " + line
                continue
            keyword = self._step_keyword(line)
            if keyword is not None:
                text = line[len(keyword):].strip()
                if keyword in ("And", "But", "*"):
                    keyword = self._last_keyword(scenario)
                    if keyword is None:
                        raise GherkinParseError(
                            f"{self._path}:{idx}: {line!r} continues "
                            f"nothing")
                step = StepSpec(keyword=keyword, text=text, line=idx)
                scenario.steps.append(step)
                continue
            # Scenario prose before its first step extends the name.
            if not scenario.steps:
                scenario.name += " " + line
            else:
                raise GherkinParseError(
                    f"{self._path}:{idx}: unrecognized line {line!r}")
        if fence is not None:
            raise GherkinParseError(
                f"{self._path}: unterminated DocString")
        self._validate(feature)
        return feature

    # ----------------------------------------------------------- plumbing
    @staticmethod
    def _step_keyword(line: str) -> Optional[str]:
        for kw in _STEP_KEYWORDS:
            if line.startswith(kw + " ") or line == kw:
                return kw
        return None

    @staticmethod
    def _last_keyword(scenario: ScenarioSpec) -> Optional[str]:
        return scenario.steps[-1].keyword if scenario.steps else None

    def _validate(self, feature: FeatureSpec) -> None:
        if not feature.name:
            raise GherkinParseError(f"{self._path}: no Feature: line")
        if not feature.scenarios:
            raise GherkinParseError(f"{self._path}: no scenarios")
        for sc in feature.scenarios:
            where = f"{self._path}:{sc.line} (@{sc.tag})"
            if not sc.tag:
                raise GherkinParseError(f"{where}: scenario has no @tag")
            if not sc.steps_of("Given", "When"):
                raise GherkinParseError(
                    f"{where}: no Given/When steps")
            thens = sc.steps_of("Then")
            if len(thens) != 1:
                raise GherkinParseError(
                    f"{where}: exactly one Then required, "
                    f"found {len(thens)}")


def parse_feature(path: str | Path) -> FeatureSpec:
    """Parse one .feature file into its typed spec (raises at load)."""
    return _GherkinParser(Path(path)).parse()


def load_features(directory: str | Path) -> List[FeatureSpec]:
    """Every .feature file in ``directory``, sorted by filename (file
    order is the chain order; names are numbered for that)."""
    root = Path(directory)
    if not root.is_dir():
        raise GherkinParseError(f"{root}: no features directory")
    return [parse_feature(p)
            for p in sorted(root.glob("*.feature"))]
