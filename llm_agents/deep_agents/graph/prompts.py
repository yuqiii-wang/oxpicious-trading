"""llm_agents.deep_agents.graph.prompts — the thread graph's per-step
prompt surfaces.

The PLANNER-side input machinery (the dynamic templates
``template_args`` / ``render_template``, the run's BUSINESS framing
``business_framing``, and the plan node's user message
``plan_user_content``) lives with the proposal session in
``planning.planner`` — planning owns what the planner reads, this
module owns what a step hop reads:

  * the FINDINGS bridge (``findings_block``) — the bounded rendering of
    the earlier steps' findings that each step conversation carries
    (the per-step conversations meet only through this accumulated
    evidence; older findings age to title lines when the block would
    overflow);
  * ``STEP_SYSTEM_ADDENDUM`` + ``step_user_message`` — one step hop's
    conversation: the step contract (work YOUR step, self-contained
    finding, replan powers, fork powers) and the user message (run
    goal + structured context + the selected prompts' texts + the
    step's directive + findings so far — the compacted digest block
    riding BESIDE the raw tail when a compaction has run, see
    ``compaction``).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from llm_agents.deep_agents.graph.compaction import digest_block
from llm_agents.deep_agents.planning.store import StateRow


# One step's finding, and the findings block the next step sees: per-
# conversation evidence capped per line, the whole block capped by
# aging OLDER findings down to their title line (the recent evidence —
# what the next step mostly needs — stays full).
FINDING_MAX = 1200
FINDINGS_BLOCK_MAX = 12000


def finding_line(ordinal: int, title: str, finding: str) -> str:
    text = " ".join(str(finding).split())
    if len(text) > FINDING_MAX:
        text = text[:FINDING_MAX - 1] + "…"
    return f"{ordinal}. {title} — {text}"


def findings_block(findings: List[str]) -> str:
    """Render the accumulated findings for a step's user message; when
    the block would overflow, earlier findings age to title-only lines
    (``N. <title>`` — the part before the em dash)."""
    if not findings:
        return "(no findings yet — you are the first research step)"
    lines = list(findings)
    while sum(len(line) for line in lines) > FINDINGS_BLOCK_MAX and \
            len(lines) > 1:
        for i in range(len(lines)):
            head = lines[i].split(" — ", 1)[0]
            if len(head) < len(lines[i]):
                lines[i] = head + " — (aged out; see the thread graph)"
                break
        else:  # everything already aged — drop from the front
            lines.pop(0)
    return "\n".join(lines)


# The per-step system addendum: the step's own lifecycle is runner-side
# (no ticking tool); the model's plan powers are the living-graph
# rework and forking its own sub-work.
STEP_SYSTEM_ADDENDUM = (
    "You are ONE research step of a planned multi-step run; other steps "
    "run as their own conversations before and after you — your only "
    "channel to their work is the FINDINGS block in the user message, "
    "so make YOUR answer equally self-contained (the numbers, the "
    "verdict) for the steps after you.\n"
    "Work YOUR step only, then reply with the step's finding as your "
    "final answer (concise; include the key numbers and the "
    "pass/fail verdict).\n"
    "REPLAN: when what you find changes the remaining plan, call "
    "update_plan — add steps (delegate_goal marks a fork a subagent "
    "will run), revise or remove steps that have NOT run, terminate "
    "running forks you spawned when they stopped being relevant. Steps "
    "already running or settled never move.\n"
    "FORKS: for self-contained sub-work inside YOUR step, "
    "spawn_subagent(background=true) lets it run while you continue; "
    "when the step splits into several NON-OVERLAPPING studies "
    "(different subjects or different indicators), spawn them ALL in "
    "ONE step as sibling calls — sibling spawns run in parallel, "
    "one-at-a-time spawns serialize. Gather every fork with "
    "collect_subagents before answering. Every forked subagent "
    "automatically receives the run's business context "
    "(subject, focus question, selected prompts) beside its goal — "
    "write the goal as THIS fork's business question (the subject + "
    "what to settle, plus the tables/columns it needs), not a repeat "
    "of the run."
)

def step_user_message(
    goal: str,
    data: Optional[Dict[str, Any]],
    state: StateRow,
    delegate_goal: Optional[str],
    findings: List[str],
    prompt_texts: Optional[Dict[str, str]] = None,
    *,
    digest: Optional[str] = None,
    n_compacted: int = 0,
) -> str:
    """One step hop's user message: the run goal, the step's directive
    (title / notes / fork goal), and the accumulated findings of the
    earlier steps — the only bridge between the per-step
    conversations. ``prompt_texts`` (the rendered texts of the plan
    prompts the user selected for the run) rides between the context
    and the directive, so every step honors the same study
    instructions the planner planned under. ``digest`` (the compact
    node's merged evidence, with ``n_compacted`` the raw lines it
    covers) rides BESIDE the raw findings tail: the first steps'
    substance arrives as the digest, the recent ones as full lines."""
    parts = [f"[Run goal]\n{goal}"]
    if data:
        parts.append("[Structured context — JSON]\n```json\n"
                     f"{json.dumps(data, ensure_ascii=False, indent=2)}"
                     "\n```")
    if prompt_texts:
        quoted = "\n\n".join(
            f"- {key}: {text}" for key, text in prompt_texts.items())
        parts.append("[Selected prompts — the study instructions the "
                     f"user picked for this run; honor them]\n{quoted}")
    directive = [f"[Your step — work THIS step only]\n"
                 f"{state.ordinal}. [{state.node}] {state.title}"]
    if delegate_goal:
        directive.append(f"Fork goal: {delegate_goal}")
    parts.append("\n".join(directive))
    if digest:
        parts.append(digest_block(digest, n_compacted))
    if findings:
        parts.append("[Findings so far — from the earlier steps"
                     + ("; the tail since the last compaction"
                        if digest else "") + "]\n"
                     + findings_block(findings))
    elif not digest:
        parts.append(findings_block(findings))
    return "\n\n".join(parts)


