"""llm_agents.deep_agents.planning.prompt_loading — GRADUAL INCREMENTAL
DISCRETIONARY PROMPT LOADING (the prompt-budget stage of a run).

The problem this package owns (measured 2026-11-02 on live runs): a run
with every scope and every skill picked carried a 76 KB structured-data
block (16 analysis items with full metric query recipes) in its FIRST
user message — the planner's — and the goal text then taught EVERY
conversation (step hops, forks) to fetch ALL selected skill rows
(prompt_type 1, 25 active rows ≈ 19 KB) wholesale before working. No
stage of the run ever decided whether a skill is RELEVANT to the ask;
loading was all-or-nothing at every level.

The loading ladder implemented here (each rung cheaper than the next):

  1. CATALOG — one cheap DB read of the preview columns
     (:meth:`PLAN_STORE.fetch_prompt_catalog`: key / sub_type / type /
     title / preview / labels — never the instruction texts).
  2. KEYWORD MATCH — a pure-code label filter over the catalog
     (:meth:`PromptLadder.match`): a row whose ``bound_labels`` phrase
     appears in the run's question/scope/item vocabulary is FORCED
     (loaded, no LLM judgment needed — the ai_prompts DDL's own
     force-load contract); an ``optional_labels`` hit marks it a
     CANDIDATE (preview shown, verdict may load it).
  3. HOTPATH CORROBORATION — (2026-11-07, evidence gathered by
     ``planning/kg_prompts.HotpathPromptProbe`` over the promoted
     ``llm.kg_hotpaths`` chains, matched here by
     :meth:`PromptLadder.hotpath_rung`): an optional-labeled row the
     hotpath evidence implicates (its prompt_key in a promoted ns1
     chain, or its optional labels in a chain's entity vocabulary)
     returns ``"hotpath"`` and LOADS like a forced row — a question
     whose domain the KG already resolved twice is known territory.
  4. VERDICT — only when the selected skills still outnumber the eager
     budget does the planner's reply carry ``load_prompts``: the keys
     the PLAN itself structures work around. Everything else stays a
     catalog line.
  5. EXECUTION-TIME DISCRETION — the unloaded rows ride every step hop
     and fork as one preview line each (:meth:`PromptLadder.pass_down`
     → the messages' catalog blocks); a step that turns out to work a
     cataloged skill's domain fetches THAT ONE row by prompt_key
     through its read-only DB tools (never wholesale).

Small runs keep today's behavior: a skills selection at or under the
configured prompt_eager_rows — or a matched set whose full texts fit
the configured prompt_eager_chars (both from
``limits.run_limits()``) — loads eagerly with NO verdict dependency;
the ladder only engages when the selection is big enough to bloat.

This package also owns the PLANNER-SCOPED data compaction
(:func:`planner_scoped_data`): the planner names steps, it does not
ground metric values, so its message carries the analysis items as
label + metric names without the per-metric ``db_keywords`` query
recipes and without the docs bodies (the STEP hops keep the full data —
recipes ground the numbers they must report).

Pure domain: no DB, no provider, no LangGraph import — stub-testable
like ``planner`` / ``plan_json``. The rungs are methods of ONE
:class:`PromptLadder` bound to the run's catalog rows (2026-11-06 — the
former module functions); the pure renderers stay module-level.

Package layout (the former single prompt_loading.py, split
2026-11-07): catalog.py (CatalogRow + _normalize + merge_hotpath_
matches) / ladder.py (PromptLadder) / scoped_data.py
(planner_scoped_data). This root re-exports the whole public surface,
so every importer keeps importing from ``planning.prompt_loading``
unchanged.
"""
from llm_agents.deep_agents.planning.prompt_loading.catalog import (
    PREVIEW_LINE_MAX, CatalogRow, _normalize, merge_hotpath_matches,
)
from llm_agents.deep_agents.planning.prompt_loading.ladder import (
    PromptLadder,
)
from llm_agents.deep_agents.planning.prompt_loading.scoped_data import (
    planner_scoped_data,
)

__all__ = [
    "CatalogRow", "PromptLadder", "PREVIEW_LINE_MAX",
    "merge_hotpath_matches", "planner_scoped_data",
]
