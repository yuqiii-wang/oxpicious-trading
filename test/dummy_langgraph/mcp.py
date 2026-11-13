"""test.dummy_langgraph.mcp — the FEATURE-DRIVEN tool seam: the same
gherkin dataset serves the harness's TOOL calls, not just the chat
turns. ``FeatureToolSeam`` is the duck-typed MCP client
(``tool_schemas`` + ``call_tool``) the probes inject into
``run_thread(..., mcp=...)``; each tool call is matched against the
tool scenarios in ``features/03_tools.feature``:

    @kg-ma
    Scenario: kg_search over the moving-average vocabulary
      Given the tool is "kg_search"
      And the arguments contain "均线"
      When the knowledge graph is searched
      Then the result is
      ```
      kg_entities: ...
      ```

* Given/When = the match conditions — ``the tool is "<name>"`` and
  ``the arguments contain "<text>"`` (case-folded substring over the
  JSON-serialized arguments — a query word, a table name, anything the
  caller passed);
* Then = the result text (the DocString).

First match in file order wins; the ``kg_search`` scenarios cover the
varied trader inputs (均线/排列, 放量/缩量, macd/金叉, bollinger, rsi,
momentum) and a final no-match render teaching the synonym loop; a
miss raises ``ToolResultNotFound`` — the dataset must cover the calls
the walk actually makes.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from test.dummy_llm._gherkin import ScenarioSpec, StepSpec, load_features
from test.dummy_langgraph.provider import FEATURES_DIR, ReplyNotFound

_TOOL_RE = re.compile(r'the tool is "(.+?)"', re.IGNORECASE)
_ARGS_RE = re.compile(r'the arguments contain "(.+?)"', re.IGNORECASE)


class ToolResultNotFound(ReplyNotFound):
    """No tool scenario matched the call."""


class _ToolRule:
    """One compiled tool scenario: predicates over (tool, arguments)
    + the result DocString."""

    def __init__(self, scenario: ScenarioSpec) -> None:
        self.scenario = scenario
        self.tool: Optional[str] = None
        self.needles: List[str] = []
        for step in scenario.steps_of("Given", "When"):
            m = _TOOL_RE.search(step.text)
            if m:
                self.tool = m.group(1).lower()
                continue
            m = _ARGS_RE.search(step.text)
            if m:
                self.needles.append(m.group(1).lower())
        then = scenario.steps_of("Then")[0]
        self.result = (then.docstring or "").strip()
        if not self.tool or not self.result:
            raise ValueError(
                f"tool scenario @{scenario.tag}: needs "
                "'the tool is \"...\"' and a Then DocString result")

    def matches(self, name: str, arguments: Dict[str, Any]) -> bool:
        if self.tool and self.tool != (name or "").lower():
            return False
        blob = json.dumps(arguments, ensure_ascii=False).lower()
        return all(n in blob for n in self.needles)


class FeatureToolSeam:
    """The MCP double over the tool scenarios (the dataset's third
    feature file). Declares ``db_run_query`` + ``kg_search``; answers
    every ``call_tool`` from the matched scenario's Then DocString."""

    def __init__(self, scenarios: Optional[Sequence[str]] = None,
                 features_dir: Optional[Path] = None) -> None:
        from pathlib import Path as _Path
        self._rules: List[_ToolRule] = []
        for feature in load_features(features_dir or FEATURES_DIR):
            if not feature.path.endswith("03_tools.feature"):
                continue
            for sc in feature.scenarios:
                if scenarios is not None and sc.tag not in scenarios:
                    continue
                self._rules.append(_ToolRule(sc))
        if not self._rules:
            raise ToolResultNotFound("no tool scenarios mounted")
        self.n_calls = 0
        self.calls: List[Dict[str, Any]] = []

    async def tool_schemas(self) -> List[Dict[str, Any]]:
        return [
            {"type": "function", "function": {
                "name": "db_run_query",
                "description": "read-only SQL over the replica (feature "
                               "seam)",
                "parameters": {"type": "object", "properties": {
                    "sql": {"type": "string"}}}}},
            {"type": "function", "function": {
                "name": "kg_search",
                "description": "semantic search over the DB-semantics "
                               "knowledge graph (feature seam)",
                "parameters": {"type": "object", "properties": {
                    "query": {"type": "string"}}}}},
        ]

    async def call_tool(self, name: str,
                        arguments: Dict[str, Any]) -> str:
        self.n_calls += 1
        self.calls.append({"name": name, "arguments": arguments})
        for rule in self._rules:
            if rule.matches(name, arguments or {}):
                return rule.result
        raise ToolResultNotFound(
            f"no tool scenario matched {name} "
            f"({json.dumps(arguments, ensure_ascii=False)[:120]})")
