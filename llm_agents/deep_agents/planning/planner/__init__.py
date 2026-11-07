"""llm_agents.deep_agents.planning.planner — the PLAN PROPOSAL SESSION
as an independent class.

The deep-agent run's plan node asks the model to translate its intent
into the states JSON the runner compiles the thread from
(``{"steps": [{"node", "title", "notes", "delegate"?}]}`` — the
contract in ``plan_json``). This package owns that whole conversation
as ONE class, :class:`Planner`:

  * it assembles what the model reads — the DYNAMIC-TEMPLATE
    machinery (``template_args`` / ``render_template``, moved here
    from langgraph.prompts so planning is self-contained), the compact
    BUSINESS framing of the run's input (``PlanPromptAssembler``: scope
    subject, focus question, selected skills, analysis items), and —
    past the eager prompt budget — the PROMPT CATALOG of the
    not-yet-loaded skills whose ``load_prompts`` verdict the reply
    carries (the gradual loading ladder, ``planning.prompt_loading``);
  * it calls the LLM and validates the reply against the plan-JSON
    contract (``plan_json.parse_plan_json`` — structure AND business
    naming); on ANY rejection (no JSON object, malformed JSON, missing
    titles, ops-named steps, ...) the SPECIFIC complaint goes BACK to
    the model as a correction turn — its own bad reply plus the repair
    instruction — up to *repair_rounds* corrections after the first
    attempt;
  * ``propose()`` returns ONLY steps that validated: the
    guaranteed-compile-able thread states (the runner persists them
    one-to-one as the pending ``llm.deep_agent_states`` rows the step
    lane walks). After the last rejected round it raises
    :class:`PlanJsonError`; transport errors propagate — the runner's
    fail-soft wrapper degrades to the plain execute lane either way,
    never failing a run over a plan.

Pure domain: no DB, no LangGraph, no provider import — the chat
callable rides in duck-typed (anything async with
``chat_complete(messages, model=, temperature=)``), so a session
unit-tests against a stub.

Package layout (the former single planner.py, split 2026-11-07):
template_args.py (TemplateArgs + render_template + the study-scope
family) / assembler.py (PlanPromptAssembler) / planner.py (Planner:
init, preplan_fork, propose_batches, propose). This root re-exports
the whole public surface, so every importer keeps importing from
``planning.planner`` unchanged.
"""
from llm_agents.deep_agents.planning.planner.assembler import (
    PlanPromptAssembler,
)
from llm_agents.deep_agents.planning.planner.planner import Planner
from llm_agents.deep_agents.planning.planner.template_args import (
    STUDY_SCOPE_PLAN_PROMPTS, TemplateArgs, default_scope_plan_key,
    render_template, template_args,
)

__all__ = [
    "Planner", "PlanPromptAssembler", "TemplateArgs",
    "render_template", "template_args",
    "STUDY_SCOPE_PLAN_PROMPTS", "default_scope_plan_key",
]
