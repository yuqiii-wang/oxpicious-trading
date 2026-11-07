"""llm_agents.deep_agents.langgraph.prompts — the thread graph's per-step
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
    ``compaction``);
  * the two OUTPUT-NODE DIRECTIVES (2026-11-06 three-graph refactor;
    2026-11-07 DB-editable) — the charters of a graph's FINAL OUTPUT
    NODE's synthesis conversation, each opening with the output node's
    place in the run's three-graph chain: ``CUSTOM_OUTPUT_DIRECTIVE``
    (the custom graph's findings synthesis — the graph-output handoff
    document the result graph reads as its input) and
    ``RESULT_OUTPUT_DIRECTIVE`` (the result graph's final answer
    weighing the findings AND the four reviews — the run's
    final-result document). Seeded as the type-3 internal rows
    ``deep-agent-custom-output-directive`` / ``deep-agent-result-output-
    directive``; the constants are the fail-soft fallbacks.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from llm_agents.deep_agents.langgraph.compaction import digest_block
from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.payload_guard import (
    guard_notes_block, guard_payload,
)
from llm_agents.deep_agents.planning.store import (AGENT_PROMPT_STANDING_SYSTEM, StateRow)
from llm_agents.deep_agents.skills.base import SkillBook


def finding_line(ordinal: int, title: str, finding: str) -> str:
    """One step's finding line — per-conversation evidence capped per
    line (the configured finding_max_chars) so the whole block stays
    bounded."""
    text = " ".join(str(finding).split())
    finding_max = run_limits().finding_max_chars
    if len(text) > finding_max:
        text = text[:finding_max - 1] + "…"
    return f"{ordinal}. {title} — {text}"


def findings_block(findings: List[str]) -> str:
    """Render the accumulated findings for a step's user message; when
    the block would overflow the configured findings_block_max_chars,
    earlier findings age to title-only lines (``N. <title>`` — the
    part before the em dash)."""
    if not findings:
        return "(no findings yet — you are the first research step)"
    lines = list(findings)
    while sum(len(line) for line in lines) > \
            run_limits().findings_block_max_chars and len(lines) > 1:
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
# rework and forking its own sub-work; the chain paragraph teaches the
# cross-graph context reads (2026-11-06 three-graph refactor); the
# CRITICAL paragraph (2026-11-06 b) teaches the must-check both ways —
# flag your own discovery, read the run's flagged ones.
STEP_SYSTEM_ADDENDUM = (
    "You are ONE research step of a planned multi-step run; other steps "
    "run as their own conversations before and after you — within your "
    "graph their work reaches you through the FINDINGS block in the "
    "user message, so make YOUR answer equally self-contained (the "
    "numbers, the verdict) for the steps after you.\n"
    "THE RUN IS A CHAIN OF GRAPHS: one run = three chained LangGraphs, "
    "one thread each — plan (the planner's proposal), custom (the "
    "business steps), result (the four reviews + the final answer). "
    "Every EARLIER graph's nodes' recorded contexts are readable from "
    "where you sit, and reading them is cheap: call "
    "search_thread_context with keywords for what you need — it spans "
    "the WHOLE chain over the recorded requests AND the recorded "
    "payloads (the planner's proposal, any earlier step's "
    "asks/answers/fetches and its full result notes, fork answers, the "
    "custom graph's final synthesis; hits are labeled [plan|node] / "
    "[custom|node] / [result|node] and the header lists each graph's "
    "thread id). For an EXACT full payload (the plan JSON, a step's "
    "full result note, the synthesis document), take the thread id "
    "from the search header and read the row directly with "
    "db_run_query: SELECT c.node, l.label AS kind, c.payload FROM "
    "llm.deep_agent_contexts c JOIN static.label_dim l ON l.domain = "
    "'agent_context_kind' AND l.code = c.kind WHERE c.thread_id = "
    "'<thread id>' (kind plan = the plan JSON, step-input/step-result "
    "= a step's directive/result note, graph-output = the custom "
    "graph's synthesis, final-result = the run's answer). Search "
    "first, query second — never redo work an earlier graph already "
    "finished.\n"
    "CRITICAL DISCOVERIES — a MUST CHECK, both ways. (1) CHECK BEFORE "
    "YOU CONCLUDE: run search_thread_context with critical_only=true "
    "and read every flagged discovery of the run — a finding an "
    "earlier agent judged to CHANGE THE RUN'S CONCLUSION (a material "
    "break in the evidence, a risk/trigger the plan must react to, a "
    "number that overturns the working view); your reply must weigh "
    "them, not repeat what they overturned. (2) FLAG YOUR OWN: when "
    "YOUR reply settles such a discovery, END the reply with one "
    "final line `[CRITICAL] <the discovery in one line>` — the line is "
    "stripped and recorded as your step's critical flag for every "
    "later step, the reviews and the final answer; no such finding, no "
    "marker line.\n"
    "Work YOUR step only. Your step serves ONE goal — the business "
    "question its title states — and the runner marks that goal "
    "FINISHED (the step completed) when you reply, so do not reply "
    "until the goal is settled: the reply is the settlement (concise; "
    "include the key numbers and the pass/fail verdict; when the goal "
    "proves unanswerable, the finding says why — that settles it).\n"
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
    "write the goal as THIS fork's business question (a SMALLER goal "
    "cut from your step's goal: the subject + what to settle, plus "
    "the tables/columns it needs), not a repeat of the run."
)

# THE OUTPUT-NODE DIRECTIVES (2026-11-06 three-graph refactor; 2026-11-07
# DB-editable) — the charters of the two execution graphs' FINAL OUTPUT
# NODES, each opening with the OUTPUT NODE's place in the run's
# three-graph langgraph chain. Seeded as the type-3 internal rows
# ``deep-agent-custom-output-directive`` / ``deep-agent-result-output-
# directive``; these constants are the fail-soft fallback the runner
# resolves against (agent_prompts.get(KEY) or CONSTANT — the seed and the
# constants must stay in sync). Each output seat runs ONE fresh
# step-flavored conversation over the findings bridge with its directive
# as the charter; the synthesis it returns is the graph's output document
# (the custom graph's feeds the result graph; the result graph's is the
# run's answer).
CUSTOM_OUTPUT_DIRECTIVE = (
    "You are the OUTPUT node of this run's CUSTOM graph — the middle "
    "graph of the run's THREE-GRAPH CHAIN. The PLAN graph proposed the "
    "business steps; THIS custom graph executed them node by node "
    "(entry → step* with compact detours → finalize → output — you are "
    "its last node); the RESULT graph runs after you. Your input is the "
    "accumulated findings bridge: every business step's one-line "
    "verdict and numbers; the whole chain's recorded traffic sits "
    "behind search_thread_context. Your document persists as the kind "
    "graph-output context row — THE HANDOFF: the result graph seeds it "
    "as line 0 of its own findings bridge, and its four independent "
    "review seats (return_estimate / risk_analysis / "
    "optimistic_review / pessimistic_review) and its output node read "
    "THIS document beside the raw findings. Weigh the findings into "
    "this custom graph's concluding synthesis: the balanced view of "
    "the subject NOW — where trend, valuation, positioning and flows "
    "stand; which findings agree, which conflict and what the conflict "
    "means; what the evidence supports and with what confidence, and "
    "what it cannot settle yet. CRITICAL DISCOVERIES are a MUST CHECK: "
    "run search_thread_context with critical_only=true first and let "
    "every flagged finding (a material break in the evidence, a "
    "risk/trigger, a number that overturns the working view) weigh "
    "openly on the synthesis — name it, its source step, and how it "
    "bounds the view; when the synthesis itself concludes such a "
    "discovery, end the document with one final line `[CRITICAL] <the "
    "discovery in one line>`. Make it the definitive findings digest "
    "for the run's RESULT stage — self-contained, citing the steps' "
    "numbers, the view's conditions stated plainly."
)
RESULT_OUTPUT_DIRECTIVE = (
    "You are the OUTPUT node of this run's RESULT graph — the LAST "
    "graph of the run's three-graph chain (plan → custom → result). "
    "The custom graph's synthesis rides your findings bridge as line 0 "
    "(\"Custom graph output\"), beside the raw findings and the four "
    "review seats' findings (return_estimate / risk_analysis / "
    "optimistic_review / pessimistic_review — each steelmanned through "
    "its own lens). Your document persists as the kind final-result "
    "context row — the run's answer document the user reads. Weigh the "
    "business findings, the custom graph's synthesis and the four "
    "reviews into the run's FINAL ANSWER: the concluding view of the "
    "subject — expected direction and indicative range with confidence "
    "over the next month-quarter; the material risks with their "
    "triggers; the strongest bull case and the strongest bear case "
    "with their conditions; and explicitly where the reviews "
    "OVERturned, confirmed or bounded the custom graph's synthesis. "
    "CRITICAL DISCOVERIES are a MUST CHECK: run search_thread_context "
    "with critical_only=true and address every flagged finding of the "
    "run in the answer — a discovery left unweighed is a hole in the "
    "conclusion; when the answer itself concludes a new critical "
    "discovery, end the document with one final line `[CRITICAL] <the "
    "discovery in one line>`. This is the run's result document — "
    "complete, cited from the findings and reviews, no new research."
)

# The STANDING opening prompt — composed FIRST into EVERY agent
# conversation's system text (planner, main steps both lanes, the
# degraded execute lane, and every spawned child through the input
# hook), so the whole run shares one identity and one behavior floor:
# what the goal is, that findings feed the run's single answer, biz
# skills over generic priors, and proactive delegation. The skills
# sentence rides the GRADUAL LOADING LADDER (planning.prompt_loading):
# loaded skills' texts ride the message itself, the rest sit in the
# prompt catalog as one preview line each — a step fetches a cataloged
# skill's text BY KEY only when its work touches that skill's domain
# (never wholesale). The spawn sentence is TOOL-AWARE ("when your
# toolkit carries spawn_subagent") because the prompt also rides
# depth-cap children that carry NO spawn tool — naming the tool to them
# would only invite hallucinated calls (the core.delegation
# floor-prompt lesson, verbatim).
# The standing opening prompt's constant + resolver live in
# planning.session (relocated 2026-11-06 — the plan agent session is
# planning-pure; langgraph only composes). Re-imported here so the
# step-lane/hook consumers keep their import paths.
from llm_agents.deep_agents.planning.session import (  # noqa: E402
    AGENT_STANDING_SYSTEM, standing_system,
)


class StepPromptContext(BaseModel):
    """One step hop's message inputs (2026-11-06 — the former ten-param
    signature of ``step_user_message``): the run goal + structured
    context, the step's row, the findings bridge and the loading
    ladder's three surfaces (loaded texts, step-matched prompts, the
    catalog menu) plus the compaction digest."""

    goal: str
    data: Optional[Dict[str, Any]] = None
    state: StateRow
    delegate_goal: Optional[str] = None
    findings: List[str] = Field(default_factory=list)
    prompt_texts: Dict[str, str] = Field(default_factory=dict)
    prompt_catalog: Dict[str, str] = Field(default_factory=dict)
    node_prompts: Dict[str, str] = Field(default_factory=dict)
    digest: Optional[str] = None
    n_compacted: int = 0


def step_user_message(ctx: StepPromptContext) -> str:
    """One step hop's user message: the run goal, the step's directive
    (title / notes / fork goal), and the accumulated findings of the
    earlier steps — the only bridge between the per-step
    conversations. ``prompt_texts`` (the rendered texts of the prompts
    the loading ladder LOADED for this run) rides between the context
    and the directive, so every step honors the same study
    instructions the planner planned under; ``node_prompts`` (the
    DYNAMIC rung's keyword matches on THIS step's own directive —
    label hits and keys the directive names) rides right after, this
    node's addition; ``prompt_catalog`` (the still-unloaded rows,
    prompt_key -> one preview line — see ``planning.prompt_loading``)
    rides as the per-step fetch menu behind the ``load_prompt`` tool.
    ``digest`` (the compact node's merged evidence, with
    ``n_compacted`` the raw lines it covers) rides BESIDE the raw
    findings tail: the first steps' substance arrives as the digest,
    the recent ones as full lines."""
    parts = [f"[Run goal]\n{ctx.goal}"]
    if ctx.data:
        # The large-payload pre-check rides the hop too: oversized
        # context entries ship as metadata + preview on EVERY step
        # message (the block re-sent per hop is where context rot
        # compounds), with the notes prompting the model which data
        # needs re-fetching.
        guarded, notes = guard_payload(ctx.data)
        parts.append("[Structured context — JSON]\n```json\n"
                     f"{json.dumps(guarded, ensure_ascii=False, indent=2)}"
                     "\n```")
        block = guard_notes_block(notes)
        if block:
            parts.append(block)
    if ctx.prompt_texts:
        parts.append(SkillBook.items_block(
            ctx.prompt_texts.items(),
            "[Loaded prompts — the study instructions this run works "
            "under; honor them]"))
    if ctx.node_prompts:
        parts.append(SkillBook.items_block(
            ctx.node_prompts.items(),
            "[Step-matched prompts — keyword-matched to YOUR step's "
            "directive; honor them for this step]"))
    if ctx.prompt_catalog:
        parts.append(prompt_catalog_block(ctx.prompt_catalog))
    directive = [f"[Your step — work THIS step only]\n"
                 f"{ctx.state.ordinal}. [{ctx.state.node}] {ctx.state.title}"]
    if ctx.delegate_goal:
        directive.append(f"Fork goal: {ctx.delegate_goal}")
    parts.append("\n".join(directive))
    if ctx.digest:
        parts.append(digest_block(ctx.digest, ctx.n_compacted))
    if ctx.findings:
        parts.append("[Findings so far — from the earlier steps"
                     + ("; the tail since the last compaction"
                        if ctx.digest else "") + "]\n"
                     + findings_block(ctx.findings))
    elif not ctx.digest:
        parts.append(findings_block(ctx.findings))
    return "\n\n".join(parts)


def prompt_catalog_block(catalog: Dict[str, str]) -> str:
    """The still-unloaded prompts' render in a step hop's user message —
    the per-step fetch menu of the gradual loading ladder behind the
    ``load_prompt`` tool (same ``- key: text`` item shape as the loaded
    block above it, so the two read as one surface)."""
    quoted = "\n".join(f"- {key}: {text}" for key, text in catalog.items())
    return ("[Prompt catalog — NOT loaded; when YOUR step works one of "
            "these domains, call load_prompt with that ONE prompt_key "
            "and apply the returned instruction — never load them all]"
            f"\n{quoted}")


