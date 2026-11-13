"""test.dummy_llm.provider — the SIMPLE in/out double: one
``chat_complete`` that takes the harness's OpenAI-style request and
returns the reply its GHERKIN FEATURE dataset names — on matched what
request, return what response.

The feature files under ``features/`` are the whole dataset — the
scenarios declare WHAT each turn of the real ai-summary langgraph chain
looks like; this provider only matches a request against them:

* the Given/When steps are the PREDICATES (all must hold — Given = the
  structural input state, When = the discriminating trigger; the
  atom vocabulary is ``system contains "..."`` / ``conversation
  contains "..."`` and their ``does not contain`` negatives /
  ``tools are declared`` / ``no tools are declared`` /
  ``the flake is armed once``, case-folded substring matches; other
  step prose is descriptive only);
* the Then step is the reply: ``the reply is`` + DocString (the plan
  scenario's is the plan JSON the REAL ``parse_plan_reply`` accepts;
  step/review/output turns get plain findings) or ``the reply calls
  "<tool>" with`` + DocString (a JSON object → an OpenAI-style
  ``tool_calls`` reply, so steps genuinely walk kg/db tool turns).

Scenarios match in FILE ORDER, first match wins — put the more specific
(scenario-marker-carrying) ones first. No match raises ``ReplyNotFound``
(a silent generic reply would hide a dataset drift). Zero network, zero
spend, deterministic.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from test.dummy_llm._gherkin import (
    FeatureSpec, ScenarioSpec, StepSpec, load_features,
)

# This package's features directory (the request→response dataset).
FEATURES_DIR = Path(__file__).resolve().parent / "features"

# The audit endpoint the in-process lane's ``llm_requests`` rows carry
# (the transport seam's provider name is always ``dummy``).
AUDIT_URL = "dummy://in-process/chat/completions"

# The FIXED token numbers every usage block carries on top of the
# script-aware estimate (2 CJK chars / token, 2 tokens / English word)
# — deterministic, non-zero floors so a dummy run's consumption
# display never reads as an empty/zero-count face.
FIXED_PROMPT_TOKENS = 128
FIXED_COMPLETION_TOKENS = 64

# The predicate-atom vocabulary (compiled once; all case-folded).
_SYSTEM_RE = re.compile(r'system contains "(.+?)"', re.IGNORECASE)
_SYSTEM_NOT_RE = re.compile(r'system does not contain "(.+?)"',
                            re.IGNORECASE)
_CONV_RE = re.compile(r'conversation contains "(.+?)"', re.IGNORECASE)
_CONV_NOT_RE = re.compile(r'conversation does not contain "(.+?)"',
                          re.IGNORECASE)
_TOOLS_RE = re.compile(r"^tools are declared$", re.IGNORECASE)
_NO_TOOLS_RE = re.compile(r"^no tools are declared$", re.IGNORECASE)
_FLAKE_RE = re.compile(r"^the flake is armed once$", re.IGNORECASE)
# The tool-call Then forms. The CUT form (2026-11-13, the
# output-ceiling scenario): the DocString is the JSON the model
# INTENDED; the provider emits its arguments chopped in half (invalid
# JSON — the parse failure the real ceiling produces) and stamps
# ``finish_reason: "length"`` on the message.
_CALL_CUT_RE = re.compile(
    r'the reply calls "(.+?)" with its arguments cut', re.IGNORECASE)
_CALL_RE = re.compile(r'the reply calls "(.+?)" with', re.IGNORECASE)


class ReplyNotFound(LookupError):
    """No feature scenario matched the request — the dataset is missing
    a turn the chain actually walks."""


class _Predicate:
    """One compiled match atom (all must hold for the scenario)."""

    def __init__(self, scenario: ScenarioSpec, step: StepSpec) -> None:
        self.scenario = scenario
        self.describe = f"{step.keyword} {step.text}"
        text = step.text
        system = _SYSTEM_RE.search(text)
        system_not = _SYSTEM_NOT_RE.search(text)
        conv = _CONV_RE.search(text)
        conv_not = _CONV_NOT_RE.search(text)
        if system_not:
            self.kind, self.needle = "system_not", system_not.group(1).lower()
        elif system:
            self.kind, self.needle = "system", system.group(1).lower()
        elif conv_not:
            self.kind, self.needle = "conv_not", conv_not.group(1).lower()
        elif conv:
            self.kind, self.needle = "conv", conv.group(1).lower()
        elif _TOOLS_RE.match(text.strip()):
            self.kind, self.needle = "tools", ""
        elif _NO_TOOLS_RE.match(text.strip()):
            self.kind, self.needle = "no_tools", ""
        elif _FLAKE_RE.match(text.strip()):
            self.kind, self.needle = "flake", ""
        else:
            # Descriptive prose — never binds.
            self.kind, self.needle = "prose", ""

    def matches(self, system_text: str, conversation: str,
                tools: bool) -> bool:
        if self.kind == "system":
            return self.needle in system_text.lower()
        if self.kind == "system_not":
            return self.needle not in system_text.lower()
        if self.kind == "conv":
            return self.needle in conversation.lower()
        if self.kind == "conv_not":
            return self.needle not in conversation.lower()
        if self.kind == "tools":
            return tools
        if self.kind == "no_tools":
            return not tools
        return True  # prose / flake marker


class _ScenarioRule:
    """One scenario compiled into a predicate list + its reply."""

    def __init__(self, scenario: ScenarioSpec) -> None:
        self.scenario = scenario
        self.predicates = [
            _Predicate(scenario, step)
            for step in scenario.steps_of("Given", "When")
        ]
        self.flake = any(p.kind == "flake" for p in self.predicates)
        then = scenario.steps_of("Then")[0]
        self.reply = (then.docstring or "").strip()
        if not self.reply:
            raise ValueError(
                f"scenario @{scenario.tag}: its Then carries no "
                f"DocString reply")
        cut = _CALL_CUT_RE.search(then.text)
        call = _CALL_RE.search(then.text)
        self.tool_call: Optional[str] = (
            (cut or call).group(1) if (cut or call) else None)
        self.arguments_cut = bool(cut)
        self.arguments: Dict[str, Any] = {}
        if self.tool_call:
            try:
                self.arguments = json.loads(self.reply)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"scenario @{scenario.tag}: its tool-call Then "
                    f"DocString is not JSON: {exc}")

    def matches(self, system_text: str, conversation: str,
                tools: bool) -> bool:
        return all(p.matches(system_text, conversation, tools)
                   for p in self.predicates)


class DummyLlmProvider:
    """The offline chat double over the feature dataset (see module
    docstring). Satisfies the harness's ONE provider contract —
    ``await chat_complete(messages, *, model=, tools=, temperature=,
    max_tokens=)`` → ``{"role": "assistant", "content": str}`` — so the
    REAL ``run_thread`` chain (plan → custom → result) walks its whole
    three-graph path against the canned replies with zero spend.

    ``features=None`` loads the package's whole dataset (minus the jev
    decision features, which are the decision dataset, not chat turns);
    a list of tags mounts ONLY those scenarios (a slice of the dataset,
    in file order)."""

    def __init__(self, features: Optional[Sequence[str]] = None,
                 *, scenarios: Optional[Sequence[str]] = None,
                 features_dir: Optional[Path] = None) -> None:
        self._rules: List[_ScenarioRule] = []
        for feature in load_features(features_dir or FEATURES_DIR):
            self._mount(feature, features, scenarios)
        if not self._rules:
            raise ReplyNotFound(
                "the mounted feature set is empty — nothing to answer")
        self.n_calls = 0
        self.n_flakes = 0
        # The cumulative token consumption (the shared estimate formula)
        # — the in-process lane's consumption API (see :meth:`usage`).
        self.total_usage: Dict[str, int] = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._scenarios_used: List[str] = []
        # The last request's face (system head + conversation head) —
        # the debugging aid when a walk matches unexpectedly.
        self.last_request: Dict[str, str] = {}

    # ------------------------------------------------------------- mounts
    def _mount(self, feature: FeatureSpec,
               features: Optional[Sequence[str]],
               scenarios: Optional[Sequence[str]]) -> None:
        if feature.name.startswith("decisions"):
            return  # the jev dataset — served by the decision stub, not chat
        if features is not None and feature.path not in features:
            return
        for sc in feature.scenarios:
            if scenarios is not None and sc.tag not in scenarios:
                continue
            self._rules.append(_ScenarioRule(sc))

    # ---------------------------------------------------------- in / out
    async def chat_complete(
        self, messages: Sequence[Dict[str, Any]], *,
        model: Optional[str] = None,
        tools: Optional[Sequence[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """One chat completion: match the request → the scenario's
        reply. Raises ``ReplyNotFound`` when nothing matches."""
        self.n_calls += 1
        system_text = str((messages[0] or {}).get("content") or "") \
            if messages else ""
        conversation = "\n".join(
            str(m.get("content") or "") for m in messages)
        self.last_request = {
            "n_messages": str(len(messages)),
            "system_head": system_text[:200],
            "user_head": next(
                (str(m.get("content") or "")[:4000] for m in messages
                 if m.get("role") == "user"), "")}
        payload = {
            "model": model, "messages": list(messages),
            "tools": list(tools) if tools else None,
            "temperature": temperature, "max_tokens": max_tokens}
        for rule in list(self._rules):
            if rule.matches(system_text, conversation, bool(tools)):
                if rule.flake:
                    # The deliberate failure: raise ONCE with the Then
                    # DocString as the error text, then disarm (the
                    # resumed re-run falls through to the scenario that
                    # answers). The failed call still audits its row.
                    self._rules.remove(rule)
                    self.n_flakes += 1
                    self._audit(payload, None, rule.reply)
                    raise RuntimeError(rule.reply)
                self._scenarios_used.append(rule.scenario.tag)
                if rule.tool_call:
                    args_json = json.dumps(rule.arguments,
                                           ensure_ascii=False)
                    if rule.arguments_cut:
                        # The ceiling cut: half the intended JSON —
                        # an Unterminated string the loop's tool seam
                        # must classify + teach the resend remedy for.
                        args_json = args_json[:max(1, len(args_json) // 2)]
                    message = {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": f"call_{self.n_calls}",
                            "type": "function",
                            "function": {
                                "name": rule.tool_call,
                                "arguments": args_json,
                            },
                        }],
                    }
                    if rule.arguments_cut:
                        message["finish_reason"] = "length"
                else:
                    message = {"role": "assistant", "content": rule.reply}
                self._audit(payload, message, None)
                return message
        raise ReplyNotFound(
            f"no feature scenario matched call #{self.n_calls} "
            f"(system head: {system_text[:120]!r})")

    def _audit(self, payload: Dict[str, Any],
               message: Optional[Dict[str, Any]],
               failure: Optional[str]) -> None:
        """Record ONE ``llm_requests`` row through the transport's own
        seam — provider ``dummy``, the ``dummy://in-process`` endpoint,
        the envelope-shaped response (so the UI request rows and the
        cache-aware ``llm_usage`` rollup render the same face as the
        HTTP-sim lane; the ambient chain binding applies, chained runs
        land with real chain ids). Fail-soft: a test lane can never
        break because its audit did."""
        if message is None:
            try:
                from llm_agents._core.request_log import record_request

                record_request(
                    "dummy", AUDIT_URL, payload,
                    model=payload.get("model"), status_code=None,
                    is_failed=True, failure_reason=failure)
            except Exception:  # noqa: BLE001
                pass
            return
        usage = estimate_usage(payload.get("messages") or [], message)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.total_usage[key] += usage[key]
        try:
            from llm_agents._core.request_log import record_request

            record_request(
                "dummy", AUDIT_URL, payload,
                model=payload.get("model"),
                data={"choices": [{"index": 0, "message": message,
                                   "finish_reason": (
                                       "tool_calls"
                                       if message.get("tool_calls")
                                       else "stop")}],
                      "usage": usage},
                status_code=200)
        except Exception:  # noqa: BLE001 — the audit is fire-and-forget
            pass

    # -------------------------------------------------- consumption API
    def usage(self) -> Dict[str, int]:
        """The cumulative token consumption served so far (a fresh dict)
        — the in-process token-consumption API (the host lane answers
        the same totals through its /control + /stats surface)."""
        return dict(self.total_usage)

    # ------------------------------------------------------- observability
    def reply_texts(self) -> List[str]:
        """Every reply served so far (the probe's distinctness check)."""
        texts: List[str] = []
        rules = {r.scenario.tag: r for r in self._rules}
        for tag in self._scenarios_used:
            texts.append(rules[tag].reply)
        return texts

    def scenarios_used(self) -> List[str]:
        """The fixture tags served, in serve order (the walk's trace)."""
        return list(self._scenarios_used)


# The historical name — the engine lived in test.dummy_langgraph until
# the 2026-11-12 split (dummy_langgraph keeps only the kg/jev cucumber
# assertion datasets now); every old import keeps resolving.
DummyLanggraphProvider = DummyLlmProvider


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _count_tokens(text: str) -> int:
    """The ROUGH deterministic token count for one text — Chinese: ONE
    token per 2 CJK chars; English (the space-split words): TWO tokens
    per word; a mixed word counts both faces."""
    tokens = 0
    for word in text.split():
        cjk = len(_CJK_RE.findall(word))
        other = len(word) - cjk
        tokens += (cjk + 1) // 2  # 2 CJK chars = 1 token
        if other:
            tokens += 2            # 1 non-CJK word = 2 tokens
    return tokens


def estimate_usage(messages: Sequence[Dict[str, Any]],
                   message: Dict[str, Any]) -> Dict[str, int]:
    """The ROUGH deterministic usage estimate the HTTP envelope reports
    (the 2-CJK-chars-per-token / 2-tokens-per-English-word count + the
    fixed token floors — test payload, never an accounting source; the
    shared formula so the HTTP-sim and the in-process audit rows report
    identical numbers)."""
    def _tokens(msgs: Sequence[Dict[str, Any]]) -> int:
        total = 0
        for m in msgs:
            total += _count_tokens(str(m.get("content") or ""))
            for call in (m.get("tool_calls") or []):
                total += _count_tokens(str(
                    (call.get("function") or {}).get("arguments") or ""))
        return total

    prompt = max(1, _tokens(messages)) + FIXED_PROMPT_TOKENS
    completion = max(1, _tokens([message])) + FIXED_COMPLETION_TOKENS
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }
