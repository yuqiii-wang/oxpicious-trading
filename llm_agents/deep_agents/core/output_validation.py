"""llm_agents.deep_agents.core.output_validation — structural validation
of the agent's free-text replies (findings, final answers).

The planner's plan-JSON contract owns its own parse + validate + repair
in ``planning.plan_json`` (structure AND business-naming rules); this
module owns EVERYTHING ELSE the model says as a final answer. The checks
are STRUCTURAL and language-neutral — no judgment of content, only of
shape:

  * empty / stub reply;
  * TRUNCATION smells: unbalanced code fences, the house truncation
    marker (``…[截断]`` — sandbox output and text-caps both end with it),
    a reply that ends right after a heading (nothing under it);
  * degenerate repetition (one long line repeated many times);
  * a reply that PRESENTS itself as JSON (```json fence or a leading
    { / [ ) but does not parse.

The repair contract: :func:`validate_output` returns specific,
model-actionable complaints; ``core.loop`` feeds them back as ONE
bounded correction round (the same propose → validate → correct shape
the planner session uses — the answer-gate nudge's sibling). When the
round runs out the answer ships anyway: format never fails a run (the
always-answer contract).

Pure module: no DB, no provider import — unit-tests against plain
strings, same as plan_json.
"""
from __future__ import annotations

import json
import re
from typing import List

# The house truncation marker (text_caps.TRUNCATION_SUFFIX and the
# sandbox's head+tail output cut both end with it) — a final answer
# carrying it at the very end was built from truncated upstream text.
_TRUNCATION_MARKERS = ("…[截断]", "...[截断]", "[截断]", " …[truncated]",
                       "[truncated]")

# A heading as the LAST non-empty line — a reply cut right after
# announcing a section wrote none of the section.
_HEADING_LINE_RE = re.compile(r"^#{1,6}\s+\S")

# Degenerate repetition: one sufficiently long line pasted many times.
_REPEAT_LINE_MIN_CHARS = 40
_REPEAT_LINE_MIN_COUNT = 6

_FENCE_LINE_RE = re.compile(r"^\s*```")
_JSON_FENCE_RE = re.compile(r"^\s*```json\b", re.IGNORECASE)


def _empty_issues(text: str) -> List[str]:
    if not text.strip():
        return ["the reply is empty — restate the answer with the key "
                "numbers and the verdict"]
    return []


def _fence_issues(text: str) -> List[str]:
    """Unbalanced code fences — an opened ``` never closed (or one
    closed that was never opened): the reply was cut mid-block or the
    fence markup broke."""
    fences = [line for line in text.splitlines() if _FENCE_LINE_RE.match(line)]
    if len(fences) % 2 != 0:
        return ["the reply has an UNBALANCED code fence (``` opened but "
                "never closed) — resend the complete reply with every "
                "fence closed"]
    return []


def _truncation_issues(text: str) -> List[str]:
    stripped = text.rstrip()
    for marker in _TRUNCATION_MARKERS:
        if stripped.endswith(marker):
            return ["the reply ends with a truncation marker — it was cut "
                    "short of the complete finding; resend the full reply"]
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and _HEADING_LINE_RE.match(lines[-1]):
        return ["the reply ends right after a heading with no content "
                "under it — resend the complete reply"]
    return []


def _repetition_issues(text: str) -> List[str]:
    counts: dict = {}
    for line in text.splitlines():
        key = line.strip()
        if len(key) >= _REPEAT_LINE_MIN_CHARS:
            counts[key] = counts.get(key, 0) + 1
    repeated = [line for line, n in counts.items() if n >= _REPEAT_LINE_MIN_COUNT]
    if repeated:
        sample = repeated[0][:60]
        return ["the reply repeats the same line many times "
                f"(starting {sample!r}) — resend a clean reply"]
    return []


def presents_as_json(text: str) -> bool:
    """Whether the reply claims to be JSON: a ```json fence, or a first
    non-space character of { or [."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _JSON_FENCE_RE.match(stripped):
        return True
    return stripped[0] in "{["


def _json_issues(text: str) -> List[str]:
    if not presents_as_json(text):
        return []
    stripped = (text or "").strip()
    # Tolerate fences and prose the way plan_json does: take the outermost
    # brace/bracket span and parse that.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = stripped.find(opener), stripped.rfind(closer)
        if start >= 0 and end > start:
            try:
                json.loads(stripped[start:end + 1])
                return []
            except json.JSONDecodeError as exc:
                return [f"the reply presents as JSON but does not parse "
                        f"({exc.msg} at line {exc.lineno} column "
                        f"{exc.colno}) — resend the corrected JSON"]
    return ["the reply presents as JSON but holds no JSON object/array "
            "— resend valid JSON"]


def validate_output(text: str) -> List[str]:
    """Validate one free-text reply; [] when clean, else specific
    model-actionable complaints (fed back as the repair round). JSON is
    checked only when the reply presents itself as JSON — a markdown
    answer quoting a broken JSON snippet verbatim is the evidence, not
    the bug."""
    issues: List[str] = []
    for check in (_empty_issues, _fence_issues, _truncation_issues,
                  _repetition_issues, _json_issues):
        issues.extend(check(text or ""))
    return issues


def repair_user_message(issues: List[str]) -> str:
    """The correction-round user message — the same shape as
    plan_json.repair_user_message: the specific complaints, then the ask."""
    bullets = "\n".join(f"- {issue}" for issue in issues)
    return (
        "Your previous reply has format problems and cannot be used as "
        f"the answer:\n{bullets}\n"
        "Reply again with the COMPLETE corrected answer (same content "
        "and language, well-formed markdown; close every code fence; "
        "finish every section you start)."
    )
