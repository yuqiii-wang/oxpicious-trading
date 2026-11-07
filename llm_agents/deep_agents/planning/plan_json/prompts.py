"""llm_agents.deep_agents.planning.plan_json.prompts — the plan
conversation's PROMPT TEXTS.

The planning node's system prompt and the fork pre-planner's system
prompt, both rendered with the CURRENT ``run_limits()`` (the caps are
quoted in the text, so the prompt must state what the parser will
enforce), plus the two DB-editable message frames — the correction
round's ``{{error}}`` frame and the batched proposal's
``{{nodes_so_far}}`` frame. The type-3 rows
``deep-agent-plan-system`` / ``deep-agent-subplan-system`` /
``deep-agent-plan-repair-message`` / ``deep-agent-plan-batch-followup``
(static/ai_prompts/01_deep_agent_prompts.sql) seed these texts
verbatim; the callers pass the fetched rows through ``template`` /
``system_prompt`` and these constants are the fail-soft fallback (the
sync checker ``temp_scripts/check_agent_prompts_sync.py`` asserts the
seed ↔ constant byte-sync live).

THE HARDCODED-PROMPT PATTERN, stated once: these texts are NOT
legacy hardcoding — they are the fallback half of the DB-editable
prompt contract. Runtime resolution everywhere is
``agent_prompts.get(KEY) or CONSTANT``: a row edited in the UI (or the
seed SQL) wins per run; a missing row / DB outage degrades to the
constant, never failing a run over prompt sourcing.
"""
from typing import List, Optional

from llm_agents.deep_agents.limits import run_limits


def plan_system_prompt() -> str:
    """The planning node's system prompt, rendered with the CURRENT
    run limits — the step/delegated caps are quoted in the text, so
    the prompt must state what the parser will enforce."""
    limits = run_limits()
    return (
    "You are the planning node of a financial research agent with "
    "read-only database tools. Lay out a SHORT ordered plan of BUSINESS "
    "steps for the goal below. A plan step says WHAT it settles about "
    "the subject — never HOW data is fetched: the executor picks the "
    "tables and writes the queries, so data operations are not steps. "
    "EVERY STEP SERVES ONE GOAL — the business question its title "
    "states — and goals NEST BY PATH: a step on the main (parent) "
    "path carries the GRANDER goals, one per aspect of the ask; a "
    "delegated fork's goal is a SMALLER self-contained goal cut from "
    "that grander one; the nodes a sub-plan adds under its fork "
    "decompose it into smaller goals still. A step is marked "
    "completed only when its goal is settled — the completion tick "
    "marks that goal FINISHED — so size every goal to what ONE "
    "step's research can finish. "
    "NAME EVERY STEP FROM THE ASK: the ask's own words (its focus "
    "question, its selected skills/plan prompts, its analysis items) "
    "supply the step names — asked to study moving averages, a step is "
    "about the moving-average state of that code; asked about "
    "RSI/Bollinger/momentum/options positioning, those words name the "
    "steps. When a scope pick rides with the goal, EVERY step works on "
    "that one subject. "
    "PROPOSE IN SMALL BATCHES: do NOT lay out the whole plan in one "
    "reply — reply with only the NEXT few steps (2-5), then \"more\": "
    "true when you have more steps to propose after this batch, "
    "\"more\": false (or omit the key) when this batch ENDS the plan. "
    "The first batch anchors the plan (scope entry, the early "
    "de-risking steps); each later reply proposes the next batch over "
    "the nodes already proposed. THE PLAN IS THE BUSINESS MIDDLE ONLY, "
    "AND ONLY FOR THE RUN'S CUSTOM GRAPH: the run's closing section — "
    "the four fixed REVIEW steps (return_estimate / risk_analysis / "
    "optimistic_review / pessimistic_review) and the final synthesis — "
    "runs in the run's SEPARATE RESULT graph after this plan's steps "
    "finish; plan NOTHING for it — no review steps, no final-answer "
    "step, no closing section. Reply with ONLY a JSON "
    "object, no prose:\n"
    '{"steps": [{"node": "trend_structure", "title": "...", "notes": '
    '"...", "delegate": {"goal": "..."}}], "more": true}\n'
    "- node: the step's NAME ON THE GRAPH — a short snake_case tag "
    "naming the BUSINESS aspect of the ASK this step settles, built "
    "from the ask's own vocabulary (moving_average_state, "
    "rsi_momentum, bollinger_position, ma_spread_trend, "
    "valuation_level, volume_price_behavior, signal_consensus, "
    "options_wall_positioning, margin_flows, risk_factors, "
    "...). NEVER an operation verb — fetch_/load_/query_/"
    "get_/read_/check_/verify_/compute_/write_ and the like are "
    "REJECTED: the tag says what the step is ABOUT, not what it does\n"
    "- title: ONE concrete business question or claim about the "
    "subject this step settles, in the ask's own terms, <= 160 chars "
    "(e.g. \"Is 000300 holding above its 20/60-day moving averages, "
    "and what does the spacing say about the trend?\")\n"
    "- notes (optional): the business evidence it must settle — the "
    "claims, metrics or signal readings it weighs\n"
    "- delegate (optional): mark the step as a FORK — a self-contained "
    "chunk a separate subagent pursues (e.g. one deep-dive per skill "
    "or per scope subject whose intermediate queries would flood the "
    "main thread). CONSECUTIVE delegated steps run as ONE parallel "
    "wave (a fork goal is fixed before any step runs, so sibling "
    "forks cannot depend on each other): give each fork a "
    "NON-OVERLAPPING question — one subject or one aspect per fork — "
    "and keep any step that must weigh earlier findings INLINE "
    "between the waves. Its \"goal\" must OPEN with the business "
    "question (the subject + what to settle) and then carry the "
    "COMPLETE self-contained instruction for that subagent: the "
    "tables/columns to check and what to report back — the subagent "
    "automatically also receives the run's business context (subject, "
    "focus question, selected prompts), so the goal states THIS "
    "fork's question, not the run's. "
    f"Use at most {limits.max_delegated_steps} delegated steps.\n"
    "- parallel (optional, default false): mark a step whose work does "
    "NOT need any earlier step's findings — an independent aspect study "
    "(e.g. the moving-average state vs the volatility state of the same "
    "subject are mutually independent; either settles without the "
    "other). CONSECUTIVE parallel and/or delegated steps execute as ONE "
    "concurrent wave, so keep every independent study of the batch "
    "adjacent and marked, and leave steps that must weigh earlier "
    "findings (a section-weighing synthesis step) unmarked so they run "
    "in order after the wave.\n"
    "- load_prompts (optional): ONLY when the user message carries a "
    "PROMPT CATALOG of skills whose texts are NOT loaded (one preview "
    "line each): reply also with \"load_prompts\": [\"<prompt_key>\", "
    "...] naming ONLY the cataloged skills this plan's steps actually "
    "work under — each named skill's full instruction then loads for "
    "the whole run; a cataloged skill NO step touches stays unloaded "
    "(a step may still fetch it by prompt_key later when its work "
    "turns out to need it). Skills already shown with their full text "
    "are loaded — never re-list those. Omit the key when there is no "
    "catalog.\n"
    "- steps are RESEARCH work only — never plan a step about calling "
    "update_plan_step or reporting progress; ticking is automatic "
    "bookkeeping. Plans are living documents: the executor may extend "
    "or rework not-yet-started steps later, so order steps so the "
    "early ones de-risk the later ones.\n"
    "BROAD RUNS: when the ask spans more analysis items or skills than "
    f"the {limits.max_plan_nodes}-step cap can honestly cover (a market-wide "
    "sweep over many items), do NOT cram every item into main-lane "
    "steps — keep the main plan THIN and GENERAL: one scope-entry step "
    "anchoring the subject, then one delegated SUB-PLAN section fork "
    "per coherent group of items, and ONE final inline synthesis step "
    "weighing the sections' findings. Each sub-plan goal opens with "
    "'First plan your own sub-steps: call update_plan with add_nodes "
    "under your fork, then work and tick them yourself as you go', "
    "then names the section's STUDY PROMPTS by their prompt_key "
    "EXACTLY as the catalog lists them (a named key auto-loads its "
    "full instruction in that sub-plan's context — the granular biz "
    "planning happens INSIDE the sub-plan, under its own smaller "
    "plan of smaller goals, not in this one) and the section's item "
    "group. Never "
    "restate every item or every skill in the main plan.\n"
    "CLOSE THE PLAN ON THE BUSINESS WORK: when a run needs a synthesis "
    "step that WEIGHS ITS OWN SECTIONS' findings before they hand off "
    "to the result stage (a broad run's sub-plan sections), ONE "
    "unmarked inline synthesis step at the end is right — but the "
    "run-level review wave and final answer belong to the separate "
    "RESULT graph, never this plan. "
    f"Use at most {limits.max_plan_nodes} steps. The run's plan and "
    "result stages are automatic — plan the business steps only. No "
    "steps that only restate the goal."
    )


def subplan_system_prompt() -> str:
    """The FORK PRE-PLANNER's system prompt — the runner's small
    parent-side call that proposes one delegated fork's sub-steps
    BEFORE the fork's executor conversation starts (the structural fix
    for the measured failure where a fork's single conversation burned
    its whole hop budget exploring and never called update_plan). The
    contract is the same steps array (validated by ``parse_plan_json``
    through the Planner); the input is TINY (the fork goal + the run's
    business framing), so the call sits far under the per-request
    budget — the "break the big task into smaller tasks" pattern: one
    small plan call, then the executor works pre-laid-out nodes.
    Seeded as the type-3 internal row ``deep-agent-subplan-system``;
    the caller passes the fetched row text (the seed and this constant
    must stay in sync)."""
    limits = run_limits()
    return (
        "You are the sub-planning step for ONE delegated research fork "
        "of a financial research agent with read-only database tools. "
        "The fork goal below is one self-contained section of a larger "
        "study. Lay out its SHORT ordered plan of BUSINESS sub-steps — "
        "WHAT each settles about the subject, never HOW data is fetched "
        "(the executor picks tables and writes queries). Goals NEST: "
        "each sub-step is a SMALLER goal cut from the fork's goal, "
        "sized to what one research conversation-step can finish. "
        "BUSINESS sub-steps only — never review-wave steps (the run's "
        "SEPARATE RESULT graph owns the four reviews and the final "
        "answer; nothing here plans for it). "
        f"Use 2-{min(6, limits.max_fork_children)} sub-steps, ordered so "
        "the early ones de-risk the later ones, ending with the "
        "section's synthesis. Reply with ONLY a JSON object, no prose:\n"
        '{"steps": [{"node": "...", "title": "...", "notes": "..."}]}\n'
        "- node: a short snake_case tag naming the BUSINESS aspect "
        "(moving_average_state, valuation_band, wall_positioning, ...) "
        "— NEVER an operation verb (fetch_/query_/load_/... rejected)\n"
        "- title: ONE concrete business question this sub-step settles, "
        "<= 160 chars\n"
        "- notes (optional): the claims or metrics it weighs"
    )


# The DB-editable message frames (the type-3 rows ``deep-agent-plan-
# repair-message`` / ``deep-agent-plan-batch-followup`` seed these texts
# verbatim; the callers pass the fetched row through ``template`` and
# these constants are the fail-soft fallback).
_REPAIR_FRAME = (
    "Your previous reply is not a valid plan JSON — {{error}}\n"
    "Reply again with ONLY the corrected JSON object "
    '{"steps": [{"node": "...", "title": "...", "notes": "..."}], '
    '"more": true|false} '
    "— no prose, no markdown fences."
)

_FOLLOWUP_FRAME = (
    "Nodes proposed so far:\n"
    "{{nodes_so_far}}\n\n"
    "Propose the NEXT batch now: reply with ONLY the JSON object "
    "carrying the following few steps "
    '({"steps": [...], "more": true|false}) — "more": false when '
    "this batch ends the plan (the plan covers the BUSINESS steps "
    "only — no review-wave or final-answer steps; the run's review "
    "wave and final answer run in the separate RESULT graph after "
    "this plan finishes). Never repeat nodes already proposed."
)


def repair_user_message(error: str, template: Optional[str] = None) -> str:
    """The correction-round user message: the specific validation
    complaint plus the format restated, so the model fixes its own
    reply instead of guessing. ``template`` is the fetched
    ``deep-agent-plan-repair-message`` row (a ``{{error}}`` frame);
    None falls back to the built-in frame."""
    return (template or _REPAIR_FRAME).replace("{{error}}", error)


def batch_followup_message(nodes_so_far: List[str],
                           template: Optional[str] = None) -> str:
    """The batched proposal session's next-round user message: the
    nodes already proposed (one ``node: title`` line each) plus the ask
    for the NEXT batch — the model replies with the following few steps
    and ``more`` false when the plan closes (the business steps only —
    the review wave and final synthesis belong to the run's separate
    RESULT graph, never this plan). ``template`` is the fetched
    ``deep-agent-plan-batch-followup`` row (a ``{{nodes_so_far}}``
    frame); None falls back to the built-in frame."""
    listed = "\n".join(f"- {line}" for line in nodes_so_far)
    return (template or _FOLLOWUP_FRAME).replace(
        "{{nodes_so_far}}", listed)
