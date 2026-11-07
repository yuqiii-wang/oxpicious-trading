"""llm_agents.deep_agents.planning.store.models — the store's ROW
CONTRACTS + shared vocabulary.

The typed rows the PlanStore methods read and write (pydantic, frozen):
``PromptRow`` / ``HotpathChainRow`` / ``PlannedStep`` / ``StateRow`` /
``ThreadPlan`` / ``ThreadIdentity`` / ``PromptCatalogRow``; the
lane-node tags (PLAN_NODE / RESULT_NODE / SUPERVISOR_NODE — mirrors of
the langgraph state_store tags, planning must not import langgraph);
the tick vocabulary (TICK_STATUSES / IGNORE_NOTE); the critical-
discovery contract (CRITICAL_KEY / CRITICAL_MARKER + ``extract_
critical``) and the pure payload helpers every mixin shares
(``_kind`` / ``_normalize_notes`` / ``_payload_text`` /
``_tokenize_tags_sync``).

The layout of the split store (the former single store.py, 2026-11-07):

  models.py      — this file: row contracts + shared vocabulary;
  base.py        — _StoreBase: logging, _connect, _insert_context (the
                   contexts table's ONE write place), _state_row;
  prompts.py     — PromptStoreMixin: the ai_prompts accessors + the
                   AGENT_PROMPT_* DB-row keys + hotpath evidence;
  states.py      — StateStoreMixin: the plan/step lifecycle writes;
  outputs.py     — OutputStoreMixin: the final-output / graph-output /
                   supervisor-review contexts + finalize;
  chain.py       — ChainStoreMixin: the chain reads / purges / settle;
  identities.py  — IdentityStoreMixin: the scope -> identity link;
  __init__.py    — PlanStore(mixins) + PLAN_STORE + the re-exports.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("deep_agents.plan_store")

# The only statuses the agent-facing tick tool may set; finalize also
# settles with skipped (see finalize_thread).
TICK_STATUSES = ("in_progress", "completed", "failed")

_NOTE_MAX = 500
# Context hygiene: plan step `notes` (what the step must confirm) are the
# ONLY context payload without a writer-side cap (titles 300, delegate
# goals 1500, tick notes 500) — bound them at the _normalize_notes
# boundary so a runaway plan cannot bloat context rows.
_NOTES_MAX = 2000

# The runner-lane PLAN node's work tag on state rows: begin_thread's
# ordinal-0 planning row (and nothing else) carries it — the row that
# makes a run visible from its first instant. Consumers that mean "the
# model-planned steps" (the authorities' main lane, the UI's step-chain
# canvas) filter on it.
PLAN_NODE = "plan"

# The runner-lane RESULT node's tag on context rows (state_store's
# NODE_RESULT mirror — planning must not import langgraph): the hardcoded
# final-result node keeps no state row, so its entries key on this.
RESULT_NODE = "result"

# The runner-lane SUPERVISOR node's tag on context rows (state_store's
# NODE_SUPERVISOR mirror — planning must not import langgraph): the
# supervisor keeps no state row either; its review entry keys on this.
SUPERVISOR_NODE = "supervisor"

# The repair lane's ignore-settle note (ignore_state's default — defined
# BEFORE PlanStore: the default arg evaluates at class-creation time).
IGNORE_NOTE = "supervisor verdict: ignored — run proceeds without this step"

_STATE_COLUMNS = (
    "state_id, thread_id, parent_state_id, node, title, status, ordinal, "
    "input_context_id, result_context_id, error, started_at, finished_at, "
    "llm_context_id"
)


class PromptRow(BaseModel):
    model_config = ConfigDict(frozen=True)
    """One active plan prompt (llm.ai_prompts, prompt_type 2)."""

    prompt_key: str
    version: int
    instruction: str


class HotpathChainRow(BaseModel):
    model_config = ConfigDict(frozen=True)
    """One PROMOTED hotpath chain's evidence row (llm.kg_hotpaths — the
    two-hit-verified replay layer), grouped back from its per-edge rows:
    the query text, the live-graph validity hash, the promotion evidence
    (hit_count) and the chain's edge ids in depth order. The prompt
    loader's corroboration rung resolves the edges through the live
    ``KnowledgeGraph`` (``planning/kg_prompts.HotpathPromptProbe``)."""

    namespace: int
    query: str
    chain_id: str
    entity_hash: str
    hit_count: int
    edge_ids: Tuple[int, ...]


class PlannedStep(BaseModel):
    model_config = ConfigDict(frozen=True)
    """One validated step of the model's plan JSON.

    ``delegate`` (the delegation goal) marks a FORK step: the execute
    node's agent runs it by spawning a subagent with this self-contained
    goal instead of working it inline — the subagent's child state (and
    any nodes the subagent itself adds under it) is the forked path of
    the step. None = an ordinary inline step.

    ``parallel`` marks an INDEPENDENT step: one whose work does not need
    any earlier step's findings, so consecutive parallel/delegated steps
    may execute as ONE concurrent wave (the runner's fork-wave lane). A
    delegated step is parallel by construction; the flag is how an
    INLINE step joins a wave."""

    node: str
    title: str
    notes: Optional[Union[str, Dict[str, Any]]] = None
    delegate: Optional[str] = None
    parallel: bool = False


class StateRow(BaseModel):
    model_config = ConfigDict(frozen=True)
    """One llm.deep_agent_states row (the snapshot/list shape)."""

    state_id: int
    thread_id: str
    parent_state_id: Optional[int]
    node: str
    title: str
    status: str
    ordinal: int
    input_context_id: Optional[int]
    result_context_id: Optional[int]
    error: Optional[str]
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    # The LLM context this state ran under (the store's registry key) —
    # a REFERENCE; the context data itself is never persisted.
    llm_context_id: Optional[str] = None


class ThreadPlan(BaseModel):
    """The persisted plan of one thread — steps + their state rows."""

    thread_id: str
    steps: List[PlannedStep] = Field(default_factory=list)
    states: List[StateRow] = Field(default_factory=list)


class ThreadIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)
    """One scoped run's subject — the classification-nav pick that
    focused the summary (the request's ``data.scope``), as the
    llm.deep_agent_identities row's values."""

    sec_type: int  # static.label_dim 'sec_type' code (etf=0 index=1 stock=2 industry=3)
    code: str      # security code (index/etf/stock) or industry id
    name: Optional[str] = None


class PromptCatalogRow(BaseModel):
    model_config = ConfigDict(frozen=True)
    """One selected prompt's CATALOG face — the preview columns of
    ``llm.ai_prompts``, never the instruction text (the loading
    ladder's first rung; ``planning.prompt_loading.CatalogRow`` is the
    pure mirror the ladder computes on). ``instruction_chars`` is the
    row's instruction LENGTH (not the text) — the eager-budget fast
    path sizes a selection without fetching it."""

    prompt_key: str
    sub_type: str
    prompt_type: int
    title: str
    preview: str
    version: int
    bound_labels: Tuple[str, ...]
    optional_labels: Tuple[str, ...]
    instruction_chars: int = 0


def _kind(label: str) -> int:
    """The context row's kind as the static.label_dim
    'agent_context_kind' SMALLINT code (closed vocabulary: plan /
    step-input / step-result / subagent-result / final-result)."""
    from _common.labels import AGENT_CONTEXT_KIND_CODE  # lazy: numpy

    return AGENT_CONTEXT_KIND_CODE[label]


def _normalize_notes(
        notes: Optional[Union[str, Dict[str, Any]]],
        delegate: Optional[str] = None,
        parallel: bool = False,
) -> Optional[str]:
    """The JSONB text for a step's notes (str -> {"note": str}); a
    delegation goal rides along as {"delegate_goal": ...} and the
    independence mark as {"parallel": true} so the step's input context
    carries what the planner wrote. The note text is capped (_NOTES_MAX
    — context hygiene: the entry keeps the important data, bounded)."""
    if notes is None and delegate is None and not parallel:
        return None
    payload: Dict[str, Any] = {}
    if notes is not None:
        payload["note"] = (json.dumps(notes, ensure_ascii=False)
                           if isinstance(notes, dict) else str(notes))[:_NOTES_MAX]
    if delegate:
        payload["delegate_goal"] = delegate
    if parallel:
        payload["parallel"] = True
    return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------- #
#  CRITICAL DISCOVERIES + PAYLOAD TAGS (the contexts' two flagged-search
#  columns, 2026-11-06): is_critical lifts the payload's "critical" flag
#  (the model's own mark of a run-conclusion-changing finding), tags is
#  the StructBERT-tokenized vector over the payload text — both set in
#  _insert_context, the ONE write place, so every entry kind carries them.
# --------------------------------------------------------------------------- #

# The payload key _insert_context lifts into the is_critical column.
CRITICAL_KEY = "critical"

# The reply-line marker the step / fork / output prompts teach: the
# reply's LAST line "[CRITICAL] <the discovery in one line>" — parsed
# fail-soft out of the raw LLM reply BEFORE the payload is built (the
# same marker-line contract as the supervisor repair lane's Action:
# line). The fork tick tool instead passes an explicit critical
# argument (a tool schema beats prose there).
CRITICAL_MARKER = "[CRITICAL]"

# The tags vector indexes the payload's IMPORTANT text, not the blob:
# every JSON string leaf joined, capped (the search_tsv caps' rule).
_TAGS_TEXT_MAX = 2000


def extract_critical(text: Optional[str]) -> Tuple[bool, str]:
    """Split an LLM reply's trailing ``[CRITICAL]`` marker line off:
    returns ``(critical, clean_text)`` — the marker stripped from the
    text it flagged. Only the LAST non-empty line counts (a marker
    mid-prose is prose); no marker -> ``(False, text unchanged)``.
    Pure + fail-soft: any input parses, never raises."""
    if not text:
        return False, str(text or "")
    lines = text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or not lines[-1].strip().upper().startswith(CRITICAL_MARKER):
        return False, text
    marker = lines.pop()
    discovery = marker.strip()[len(CRITICAL_MARKER):].strip()
    # The discovery line stays visible in the clean text (it IS the
    # finding's headline) — only the machine marker itself goes.
    if discovery:
        lines.append(discovery)
    return True, "\n".join(lines).rstrip()


def _payload_text(payload_json: str) -> str:
    """The payload's searchable text: every JSON STRING leaf, joined
    with spaces (keys and structure excluded — they are not content),
    capped at _TAGS_TEXT_MAX. Pure; a malformed payload degrades to
    the raw JSON text (still tokenizable)."""
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError):
        return payload_json[:_TAGS_TEXT_MAX]
    fragments: List[str] = []

    def _walk(value: Any, depth: int = 0) -> None:
        if isinstance(value, str):
            if value.strip():
                fragments.append(value.strip())
        elif isinstance(value, dict) and depth < 8:
            for item in value.values():
                _walk(item, depth + 1)
        elif isinstance(value, (list, tuple)) and depth < 8:
            for item in value:
                _walk(item, depth + 1)

    _walk(payload)
    return " ".join(fragments)[:_TAGS_TEXT_MAX]


def _tokenize_tags_sync(payload_json: str) -> Optional[str]:
    """Tokenize one payload's text into the space-joined token string
    for to_tsvector('simple') — the SAME SearchIndexer contract as
    llm.llm_requests.search_tsv. None on any tokenizer failure (the
    row stores NULL tags; backfill_context_tags_async heals). Runs on
    a worker thread (callers go through asyncio.to_thread — the
    segmenter's first call loads the model)."""
    try:
        from _common.tokenization import SearchIndexer

        return SearchIndexer.get().tokenize_for_search(
            _payload_text(payload_json))
    except Exception as exc:  # noqa: BLE001 — NULL tags, never a lost row
        logger.warning("context tags tokenization failed (%r) — row "
                       "stored untagged (backfill heals)", exc)
        return None
