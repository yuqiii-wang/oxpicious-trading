"""llm_agents.deep_agents.planning.plan_json.contract — the plan-JSON
CONTRACT's shape constants and the REVIEW WAVE seat definitions.

The shape bounds (a plan is a checklist, not the work itself — the
RUN-BUDGET caps live in the static deep_agent_config and render into
the system prompt; these stay code constants), the review-wave seat
specs (the RESULT graph's fixed four seats — the type-result template
row seeds from these), and the contract's error type.

Package layout (the former single plan_json.py, split 2026-11-07):

  contract.py   — this file: shape constants + review-wave seats +
                  PlanJsonError;
  prompts.py    — plan_system_prompt / subplan_system_prompt + the
                  DB-editable repair / batch-followup frames;
  parse.py      — fence/prose-tolerant parsing + business-naming
                  validation + the review fixup;
  review_wave.py— the built-in four-seat fallback steps;
  __init__.py   — re-exports the whole contract surface.
"""
from typing import Tuple

from pydantic import BaseModel, ConfigDict

# Plan guardrails: a plan is a checklist, not the work itself. The
# RUN-BUDGET caps (planned step count, delegated-fork count) live in
# the static deep_agent_config (limits.run_limits() — max_plan_nodes /
# max_delegated_steps, read at the use sites and rendered into the
# system prompt); the shape bounds below stay code constants.
MAX_TITLE_LEN = 300
MAX_NODE_LEN = 60
# A delegation goal must stay a goal, not a second plan document.
MAX_DELEGATE_LEN = 1500


# ----------------------------------------------------------------------
# THE REVIEW WAVE - the RESULT graph's fixed four seats (2026-11-06
# three-graph refactor; formerly every plan's closing section). Four
# INDEPENDENT agents, each a parallel-marked INLINE step, execute as
# ONE concurrent wave of fresh conversations (own context each) that
# read the accumulated FINDINGS bridge through their own lens - the
# generator-critic pattern with independent reviewers instead of
# self-review. They are NEVER delegated: a delegated fork inherits
# only the run goal + business context (never the findings), so a fork
# cannot review what the run found; any delegate goal on these tags is
# stripped and the parallel mark forced (``_review_fixup`` - defensive
# normalization for stubborn models). The seat definitions seed the
# type-result template row the result graph is built from
# (templates.fetch_result_template falls back to
# :func:`review_wave_steps`).
#
# The tag list is the CONTRACT (exact snake_case tags, this order);
# the titles are the four GOALS - self-sufficient step directives
# (titles are all an inline step's conversation sees of its charter).
# ----------------------------------------------------------------------
class ReviewNodeSpec(BaseModel):
    """One review-wave seat: the fixed node tag plus the default goal
    title (these defaults seed the type-result template row and are
    what :func:`review_wave_steps` materializes)."""

    model_config = ConfigDict(frozen=True)

    tag: str
    title: str


REVIEW_WAVE_NODES: Tuple[ReviewNodeSpec, ...] = (
    ReviewNodeSpec(
        tag="return_estimate",
        title="Expected return over the next 1-3 months: direction, indicative "
        "range and confidence, weighed from the findings so far"),
    ReviewNodeSpec(
        tag="risk_analysis",
        title="Material risks to the view ranked by probability times impact, "
        "each with its trigger and the evidence that signals it"),
    ReviewNodeSpec(
        tag="optimistic_review",
        title="Strongest bull case the accumulated evidence supports: steelman "
        "the upside, its conditions and catalysts"),
    ReviewNodeSpec(
        tag="pessimistic_review",
        title="Strongest bear case against the view: stress the weakest "
        "findings, conflicts and failure modes with their triggers"),
)
REVIEW_NODE_TAGS = frozenset(spec.tag for spec in REVIEW_WAVE_NODES)


class PlanJsonError(ValueError):
    """The model's plan reply violates the plan-JSON contract — the
    message is the specific complaint the repair round feeds back."""
