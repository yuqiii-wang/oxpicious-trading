"""llm_agents.deep_agents.planning.plan_json — the plan-JSON contract
between the LLM and the thread runner.

The deep-agent run's plan node — the HARDCODED first node of every run —
asks the model to translate its plan into the states JSON the runner
compiles the thread from:

    {"steps": [{"node": "trend_structure", "title": "...", "notes": "...",
                "delegate": {"goal": "..."}}], "more": true}

one reply proposing a BATCH of the plan's steps (``more`` true when
more batches follow — the batched proposal protocol; see
``parse_plan_reply``), one step becoming one pending
``llm.deep_agent_states`` row (the
LangGraph task states the execute node ticks). A step carrying a
``delegate`` goal is a FORK: the execute node's agent runs it by
spawning a background subagent with that goal — the forked path the
thread tree grows (``plan_store.parent_state_id``). This package owns
that contract end to end:

  * the system prompt that states the JSON shape
    (:func:`plan_system_prompt`);
  * THE REVIEW WAVE - since the three-graph refactor (2026-11-06) the
    four fixed review seats (``REVIEW_WAVE_NODES`` - return estimator,
    risk analyst, optimistic reviewer, pessimistic reviewer) belong to
    the run's separate RESULT graph (a type-result template row in
    llm.deep_agent_langgraph_templates, seeded from these constants);
    the plan lane no longer teaches or floors them — the parser still
    normalizes any review-tagged step a stubborn model proposes
    (``_review_fixup``);
  * parse + validate (``parse_plan_json``) — fence/prose tolerant like
    every LLM JSON reply, strict on the load-bearing bits (a JSON object
    with a non-empty ``steps`` list of titled steps); every rejection
    carries a SPECIFIC, model-actionable complaint. The plan is
    BUSINESS-shaped or rejected: node tags naming a data operation
    (fetch_/load_/query_/...) and titles opening with one are bounced
    with a rename ask — the plan says what each step settles about the
    ask's subject, the executor owns the how;
  * ``parse_update_steps`` — the same per-step validation for the
    runtime ``update_plan`` tool's add_steps array (planned and
    runtime-added steps validate identically);
  * ``parse_plan_reply`` — the steps PLUS the optional
    ``load_prompts`` verdict (the gradual prompt-loading ladder's LLM
    rung — which cataloged skills the plan works under; see
    ``planning.prompt_loading``) and the ``more`` continuation flag
    (the batched proposal protocol — True only when the reply
    explicitly promises more batches);
  * the repair instruction builder (``repair_user_message``) — the
    correction turn the proposal session feeds back to the model.

The PROPOSAL SESSION itself — the propose → validate → correct loop
that keeps asking the model until the reply compiles into accepted
thread states (or the rounds run out and the runner degrades to
no-plan) — is the independent :class:`Planner` class in
``planning.planner`` (this package stays pure contract: prompt text,
parse, validate; the runner wraps the whole thing fail-soft as
always).

Pure package: no DB, no provider import (the provider rides in duck-
typed through the Planner), so the validation and the correction loop
unit-test against a stub.

Package layout (the former single plan_json.py, split 2026-11-07):
contract.py (shape constants + review-wave seats + the error type) /
prompts.py (the two system prompts + the DB-editable frames) /
parse.py (parsing + business-naming validation + the review fixup) /
review_wave.py (the built-in four-seat fallback). This root
re-exports the whole contract surface — including the private
``_REPAIR_FRAME`` / ``_FOLLOWUP_FRAME`` the prompts-sync checker
imports — so every importer keeps importing from
``planning.plan_json`` unchanged.
"""
from llm_agents.deep_agents.planning.plan_json.contract import (
    MAX_DELEGATE_LEN, MAX_NODE_LEN, MAX_TITLE_LEN, REVIEW_NODE_TAGS,
    REVIEW_WAVE_NODES, PlanJsonError, ReviewNodeSpec,
)
from llm_agents.deep_agents.planning.plan_json.parse import (
    PlanReply, parse_plan_json, parse_plan_reply, parse_update_steps,
    _parallel_mark, _review_fixup, _step_delegate,
)
from llm_agents.deep_agents.planning.plan_json.prompts import (
    _FOLLOWUP_FRAME, _REPAIR_FRAME, batch_followup_message,
    plan_system_prompt, repair_user_message, subplan_system_prompt,
)
from llm_agents.deep_agents.planning.plan_json.review_wave import (
    review_wave_steps,
)

__all__ = [
    "MAX_DELEGATE_LEN", "MAX_NODE_LEN", "MAX_TITLE_LEN",
    "REVIEW_NODE_TAGS", "REVIEW_WAVE_NODES", "ReviewNodeSpec",
    "PlanJsonError", "PlanReply",
    "parse_plan_json", "parse_plan_reply", "parse_update_steps",
    "plan_system_prompt", "subplan_system_prompt",
    "repair_user_message", "batch_followup_message",
    "review_wave_steps",
]
