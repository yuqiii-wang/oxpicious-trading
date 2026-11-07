"""llm_agents.test.dummy_llm.replies — the dummy LLM's canned reply book.

The DummyLlmProvider is only useful when DIFFERENT query texts get
DIFFERENT replies — a langgraph replay asserts that the trend step
produced the trend finding, the result wave produced four distinct
review texts, the output node produced the synthesis. This module owns
that mapping: a pydantic rule table (``ReplyRule``) matched against the
conversation's LAST USER message, plus shape-based fallbacks for
everything the rules do not name.

Three dispatch phases, in order (``DummyReplyBook.match``):

1. PLANNING SHAPE — the system prompt is one of the planner family
   (``plan_system_prompt`` / ``subplan_system_prompt`` both announce
   "planning node" / "sub-planning"): reply with a plan JSON that
   ``planning.plan_json.parse_plan_reply`` accepts. Domain rules are
   NOT consulted here — a plan proposal's user message QUOTES the run
   goal, so volume/risk keywords inside it would otherwise hijack the
   reply and planning would never parse.
2. RULE TABLE — the first rule whose pattern finds in the query text
   wins (rules may additionally require a declared tool or a system
   prompt fragment, e.g. the supervisor's verdict ask).
3. SHAPE FALLBACK — no rule matched: a declared-tools conversation is
   a step/fork hop (reply a finding); anything else is an answer.

Every reply is deterministic and self-labelling (``[dummy <kind> ·
rule=<name>]``) so a test can tell WHICH rule produced WHICH text —
and every rendered plan reply passes the REAL ``parse_plan_reply``
(asserted live by ``temp_scripts/test_dummy_llm.py``).

stdlib + pydantic only — no llm_agents imports (the provider module
wires it into the harness).
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field


# The planner-family fingerprint in the system prompt (plan_system_prompt
# says "You are the planning node ...", subplan_system_prompt says "You
# are the sub-planning step ..."). Matched case-insensitively.
_PLANNING_SYSTEM_RE = re.compile(r"planning node|sub-planning", re.I)

# The TOOL directive — a test escape hatch inside the query text:
#   "TOOL:db_run_query {\"sql\": \"SELECT 1\"}"  → one tool_call for
# db_run_query with those arguments (arguments JSON optional → {}).
# Evaluated BEFORE the rule table; lets a test drive ANY declared tool
# without writing a rule for it.
_TOOL_DIRECTIVE_RE = re.compile(r"TOOL:(\w+)\s*(\{.*\})?\s*$", re.S)

# Subject extraction: the query's subject line, trimmed to what a
# title can carry (the plan contract caps titles at 160 chars).
_SUBJECT_MAX = 100


def _subject_of(query: str) -> str:
    """The query's subject line: the first non-empty line that is not a
    bare section marker (``[Run goal]`` and friends — every harness
    message opens with one, so the RAW first line would give every
    conversation the same subject and the DIFF-reply promise would
    collapse). Falls back to a query digest so even marker-only texts
    stay distinguishable."""
    lines = [(line or "").strip()
             for line in (query or "").splitlines()]
    # A step conversation's DIFFERENTIATING line is its directive —
    # ``1. [node_tag] the step's own title`` — the run goal and the
    # findings sections repeat across every hop. Prefer it so sibling
    # steps/reviewers get DIFF replies, not one shared text.
    for line in lines:
        if re.match(r"\d+\. \[[^\]]+\] ", line):
            return line[:_SUBJECT_MAX]
    for line in lines:
        if not line or re.fullmatch(r"\[[^\]]*\]", line):
            continue
        return line[:_SUBJECT_MAX]
    digest = hashlib.md5((query or "").encode("utf-8")).hexdigest()[:8]
    return f"(no subject line · {digest})"


class PlanStepSpec(BaseModel):
    """One planned step inside a ``kind="plan"`` reply — the plan-JSON
    contract's fields (``planning.plan_json.parse_plan_json``), with the
    fork marker flattened to ``delegate_goal`` for authoring comfort."""

    node: str
    title: str
    notes: Optional[str] = None
    delegate_goal: Optional[str] = None
    parallel: bool = False

    def render(self) -> Dict[str, Any]:
        step: Dict[str, Any] = {"node": self.node, "title": self.title}
        if self.notes is not None:
            step["notes"] = self.notes
        if self.delegate_goal is not None:
            step["delegate"] = {"goal": self.delegate_goal}
        if self.parallel:
            step["parallel"] = True
        return step


class ToolCallSpec(BaseModel):
    """One canned tool call — rendered OpenAI-style (``id``/``type``/
    ``function``) by the book; ``arguments`` serializes to the JSON
    string the loop's executor expects."""

    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class ReplySpec(BaseModel):
    """One reply in whatever kind the conversation needs.

    kind: ``content`` (plain assistant text — findings, reviews,
    answers, supervisor verdicts), ``plan`` (a plan-JSON reply rendered
    from ``steps`` + ``more`` + ``load_prompts``), ``tool_calls`` (one
    assistant message carrying the calls) or ``error`` (RAISE
    ``RuntimeError(error)`` — an injected provider failure for the
    repair-lane probes). ``{subject}`` in any string field is replaced
    with the query's first line at match time."""

    kind: Literal["content", "plan", "tool_calls", "error"] = "content"
    content: Optional[str] = None
    steps: Optional[List[PlanStepSpec]] = None
    more: bool = False
    load_prompts: Optional[List[str]] = None
    tool_calls: Optional[List[ToolCallSpec]] = None
    error: Optional[str] = None

    def with_subject(self, subject: str) -> "ReplySpec":
        """The {subject} substitution (recursive over steps/calls)."""
        text = (lambda s: s.replace("{subject}", subject)
                if isinstance(s, str) else s)
        data = self.model_dump()
        data["content"] = text(data.get("content"))
        data["error"] = text(data.get("error"))
        for step in data.get("steps") or []:
            step["node"] = text(step.get("node"))
            step["title"] = text(step.get("title"))
            step["notes"] = text(step.get("notes"))
            step["delegate_goal"] = text(step.get("delegate_goal"))
        return ReplySpec.model_validate(data)


class ReplyRule(BaseModel):
    """One query-text → reply mapping of the catalog.

    ``pattern`` finds (``re.search``, IGNORECASE) in the conversation's
    LAST USER message; ``system_pattern`` (when set) must ALSO find in
    the system prompt (the supervisor's verdict ask is distinguished by
    its system text, not the goal); ``requires_tool`` (when set) must be
    among the declared tool names (a db/sandbox demo rule must not fire
    on a tool-less conversation). First matching rule wins — order the
    catalog specific → general."""

    name: str
    pattern: str
    reply: ReplySpec
    system_pattern: Optional[str] = None
    requires_tool: Optional[str] = None
    note: Optional[str] = None


def _finding(label: str, subject: str) -> str:
    return (
        f"DUMMY FINDING (rule={label}) — subject={subject} — the offline "
        "dummy reply for this query family: deterministic, provider-free "
        "text so the harness's plumbing (findings bridge, review wave, "
        "output synthesis) can be exercised without LLM spend. Nothing "
        "here is investment advice; it is test payload."
    )


def _answer(label: str, subject: str) -> str:
    return (
        f"DUMMY ANSWER (rule={label}) — subject={subject} — the offline "
        "dummy synthesis: the run completed end-to-end on canned replies "
        "(plan → steps → review wave → output), zero provider calls. "
        "Test payload, not advice."
    )


def _verdict(subject: str) -> str:
    # The supervisor repair lane parses the verdict from the RAW reply
    # (repair.parse_action): "Overall:" + "Action:" lines. no-action is
    # the safe canned verdict — the lane stays idle unless a probe
    # scripts a retry-step deliberately.
    return (
        "Error: dummy replay — no real diagnosis available\n"
        "Overall: no-action\nAction: no-action\n"
        f"(dummy supervisor verdict for: {subject})"
    )


def _generic_plan(subject: str) -> ReplySpec:
    """The shape fallback plan — three inline business steps whose tags
    pass ``parse_plan_json``'s no-operation-verb complaint. Titles carry
    the subject so the persisted plan reads like the ask."""
    return ReplySpec(
        kind="plan",
        more=False,
        steps=[
            PlanStepSpec(
                node="scope_anchor",
                title=f"Anchor the subject and data scope: {subject}",
                notes="The dummy plan's scope entry — settles what the "
                      "study covers before any aspect work.",
            ),
            PlanStepSpec(
                node="core_evidence",
                title=f"Settle the core question with evidence: {subject}",
                notes="The dummy plan's main study step — reads the "
                      "relevant state and reports the finding.",
            ),
            PlanStepSpec(
                node="view_synthesis",
                title=f"Weigh the findings into one view: {subject}",
                notes="The dummy plan's closing inline step — folds the "
                      "aspect findings into the run's view.",
            ),
        ],
    )


def _generic_finding(subject: str) -> str:
    return (
        f"DUMMY FINDING (fallback) — subject={subject} — no catalog rule "
        "matched this query text; the shape fallback (a tools-carrying "
        "conversation) returns this finding so the hop settles normally. "
        "Test payload, not advice."
    )


def _generic_answer(subject: str) -> str:
    return (
        f"DUMMY ANSWER (fallback) — subject={subject} — no catalog rule "
        "matched this query text; the shape fallback (no tools declared) "
        "returns this final answer. Test payload, not advice."
    )


class DummyReplyBook:
    """The dummy LLM's reply strategy: the rule catalog + the shape
    fallbacks, rendered to assistant-message dicts.

    Rules are pydantic (``ReplyRule``) so a test can pass its own
    catalog (``DummyReplyBook(rules=[...])``) or extend the defaults
    (``DummyReplyBook(extra_rules=[...])``) — ``include_defaults=False``
    replaces the catalog outright. The planning phase and the TOOL
    directive are structural, not rules: every book has them.
    """

    def __init__(
        self,
        rules: Optional[Sequence[ReplyRule]] = None,
        extra_rules: Optional[Sequence[ReplyRule]] = None,
        *,
        include_defaults: bool = True,
    ) -> None:
        self.rules: List[ReplyRule] = []
        if include_defaults:
            self.rules.extend(DEFAULT_RULES)
        if rules:
            self.rules.extend(rules)
        self.rules.extend(extra_rules or [])

    # -- matching --------------------------------------------------------

    def match(self, messages: Sequence[Dict[str, Any]],
              tool_names: Sequence[str]) -> Optional[ReplySpec]:
        """One reply decision for one chat_complete call (or None — the
        caller decides; the provider's shape fallbacks cover it)."""
        system = _message_text(messages, "system")
        query = _last_user_text(messages)

        # 1. Planning shape — before the rule table (a plan proposal's
        #    user message quotes the goal with domain keywords in it).
        if _PLANNING_SYSTEM_RE.search(system):
            return self.plan_reply(query)

        # 1b. The TOOL directive escape hatch: TOOL:name [json-args].
        directive = _TOOL_DIRECTIVE_RE.search(query)
        if directive:
            arguments: Dict[str, Any] = {}
            if directive.group(2):
                try:
                    parsed = json.loads(directive.group(2))
                    if isinstance(parsed, dict):
                        arguments = parsed
                except json.JSONDecodeError:
                    arguments = {"_unparsed": directive.group(2)}
            return ReplySpec(
                kind="tool_calls",
                tool_calls=[ToolCallSpec(
                    name=directive.group(1), arguments=arguments)],
            )

        # 2. The rule table — first match wins.
        subject = _subject_of(query)
        for rule in self.rules:
            if not re.search(rule.pattern, query, re.IGNORECASE):
                continue
            if (rule.system_pattern is not None
                    and not re.search(rule.system_pattern, system,
                                      re.IGNORECASE)):
                continue
            if (rule.requires_tool is not None
                    and rule.requires_tool not in tool_names):
                continue
            return rule.reply.with_subject(subject)

        # 3. Nothing matched — the caller's shape fallback decides.
        return None

    def plan_reply(self, query: str) -> ReplySpec:
        """The planning-phase reply — the generic three-step plan. A
        book subclass overrides this for scenario-specific plans."""
        return _generic_plan(_subject_of(query))

    def fallback(self, messages: Sequence[Dict[str, Any]],
                 tool_names: Sequence[str]) -> ReplySpec:
        """The shape fallback: a tools-carrying conversation is a
        step/fork hop (a finding settles it), anything else is a final
        answer. The supervisor verdict rule never reached this — its
        system pattern matched in the rule table."""
        query = _last_user_text(messages)
        subject = _subject_of(query)
        if tool_names:
            return ReplySpec(kind="content", content=_generic_finding(subject))
        return ReplySpec(kind="content", content=_generic_answer(subject))

    # -- rendering ---------------------------------------------------------

    def render(self, spec: ReplySpec, *,
               call_seq: int = 0) -> Dict[str, Any]:
        """Render a ReplySpec to the assistant message dict the loop
        contract expects (``content`` and/or ``tool_calls``). The
        ``error`` kind raises — the provider calls this only after
        deciding it wants the exception."""
        if spec.kind == "plan":
            payload: Dict[str, Any] = {
                "steps": [step.render() for step in (spec.steps or [])]}
            if spec.more:
                payload["more"] = True
            if spec.load_prompts:
                payload["load_prompts"] = list(spec.load_prompts)
            return {"role": "assistant",
                    "content": json.dumps(payload, ensure_ascii=False)}
        if spec.kind == "tool_calls":
            calls = []
            for offset, call in enumerate(spec.tool_calls or []):
                calls.append({
                    "id": f"dummy_call_{call_seq}_{offset}",
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments,
                                                ensure_ascii=False),
                    },
                })
            return {"role": "assistant", "content": None,
                    "tool_calls": calls}
        return {"role": "assistant", "content": spec.content or ""}


def _message_text(messages: Sequence[Dict[str, Any]], role: str) -> str:
    for message in messages:
        if message.get("role") == role:
            return str(message.get("content") or "")
    return ""


def _last_user_text(messages: Sequence[Dict[str, Any]]) -> str:
    for message in reversed(list(messages)):
        if message.get("role") == "user":
            return str(message.get("content") or "")
    return ""


# ----------------------------------------------------------------------
# The default catalog — quite a few dummy responses by DIFF query text.
# Domain families mirror the repo's study vocabulary (bilingual — the
# goals are written in trader Chinese); reply texts stay DISTINCT per
# rule so a test can assert WHICH family a node's finding came from.
# Order: specific → general (market_overview last among the findings).
# ----------------------------------------------------------------------
DEFAULT_RULES: List[ReplyRule] = [
    ReplyRule(
        name="supervisor_verdict",
        pattern=r".",
        system_pattern=r"supervisor",
        reply=ReplySpec(kind="content", content=_verdict("{subject}")),
        note="The repair lane's verdict ask — its system prompt is the "
             "deep-agent-supervisor-system row; the canned verdict is "
             "no-action (the lane stays idle).",
    ),
    ReplyRule(
        name="tool_demo_update_plan",
        pattern=r"update_plan|调整计划|add.*step",
        requires_tool="update_plan",
        reply=ReplySpec(
            kind="tool_calls",
            tool_calls=[ToolCallSpec(
                name="update_plan",
                arguments={"add_steps": [{
                    "node": "dummy_added_aspect",
                    "title": "Dummy added aspect: {subject}",
                    "notes": "Inserted by the dummy LLM's tool demo rule "
                             "to exercise the plan-mutation path.",
                }]},
            )],
        ),
        note="One well-formed update_plan call — exercises the plan "
             "authority's add path offline.",
    ),
    ReplyRule(
        name="tool_demo_db_query",
        pattern=r"db_run_query|查库|执行查询",
        requires_tool="db_run_query",
        reply=ReplySpec(
            kind="tool_calls",
            tool_calls=[ToolCallSpec(
                name="db_run_query",
                arguments={"sql": "SELECT 1 AS dummy_probe"},
            )],
        ),
        note="One read-only MCP query — exercises the tool seam and the "
             "tool-result guard offline.",
    ),
    ReplyRule(
        name="tool_demo_sandbox",
        pattern=r"sandbox|沙盒",
        requires_tool="sandbox_write_file",
        reply=ReplySpec(
            kind="tool_calls",
            tool_calls=[
                ToolCallSpec(
                    name="sandbox_write_file",
                    arguments={"path": "dummy_probe.py",
                               "content": "print('dummy sandbox probe')"},
                ),
                ToolCallSpec(
                    name="sandbox_run",
                    arguments={"path": "dummy_probe.py"},
                ),
            ],
        ),
        note="Write-then-run over the docker sandbox — sibling calls in "
             "one message exercise the loop's concurrent execution.",
    ),
    ReplyRule(
        name="thread_search_demo",
        pattern=r"search_thread|检索历史|thread.?search",
        requires_tool="search_thread_context",
        reply=ReplySpec(
            kind="tool_calls",
            tool_calls=[ToolCallSpec(
                name="search_thread_context",
                arguments={"query": "{subject}"},
            )],
        ),
    ),
    ReplyRule(
        name="trend_ma",
        pattern=r"趋势|均线|moving.?ave|ma_spread|\bma\b",
        reply=ReplySpec(kind="content", content=_finding("trend_ma", "{subject}")),
    ),
    ReplyRule(
        name="volume_price",
        pattern=r"放量|缩量|量价|成交量|volume",
        reply=ReplySpec(kind="content", content=_finding("volume_price", "{subject}")),
    ),
    ReplyRule(
        name="momentum_rsi",
        pattern=r"rsi|macd|momentum|动量|强弱|背离",
        reply=ReplySpec(kind="content", content=_finding("momentum_rsi", "{subject}")),
    ),
    ReplyRule(
        name="bollinger",
        pattern=r"boll|布林",
        reply=ReplySpec(kind="content", content=_finding("bollinger", "{subject}")),
    ),
    ReplyRule(
        name="options_positioning",
        pattern=r"option|期权|vix|wall",
        reply=ReplySpec(kind="content", content=_finding("options_positioning", "{subject}")),
    ),
    ReplyRule(
        name="margin_flow",
        pattern=r"margin|融资|两融|担保",
        reply=ReplySpec(kind="content", content=_finding("margin_flow", "{subject}")),
    ),
    ReplyRule(
        name="market_crowdedness",
        pattern=r"crowd|拥挤|热门|hot",
        reply=ReplySpec(kind="content", content=_finding("market_crowdedness", "{subject}")),
    ),
    ReplyRule(
        name="signal_consensus",
        pattern=r"signal|信号",
        reply=ReplySpec(kind="content", content=_finding("signal_consensus", "{subject}")),
    ),
    ReplyRule(
        name="return_estimate",
        pattern=r"return|收益|预期回报|estimat",
        reply=ReplySpec(kind="content", content=_finding("return_estimate", "{subject}")),
    ),
    ReplyRule(
        name="risk_analysis",
        pattern=r"risk|风险|回撤|drawdown",
        reply=ReplySpec(kind="content", content=_finding("risk_analysis", "{subject}")),
    ),
    ReplyRule(
        name="bull_case",
        pattern=r"bull|乐观|看多|optimistic|upside",
        reply=ReplySpec(kind="content", content=_finding("bull_case", "{subject}")),
    ),
    ReplyRule(
        name="bear_case",
        pattern=r"bear|悲观|看空|pessimistic|downside",
        reply=ReplySpec(kind="content", content=_finding("bear_case", "{subject}")),
    ),
    ReplyRule(
        name="synthesis",
        pattern=r"总结|综合|synthe|conclu|结论|final|最终|answer",
        reply=ReplySpec(kind="content", content=_answer("synthesis", "{subject}")),
    ),
    ReplyRule(
        name="market_overview",
        pattern=r"市场|大盘|指数|index|overview",
        reply=ReplySpec(kind="content", content=_finding("market_overview", "{subject}")),
    ),
]
