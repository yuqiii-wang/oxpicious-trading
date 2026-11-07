"""llm_agents.deep_agents.planning.session.session — the plan agent's
ENGINE (:class:`PlanSession` + :class:`PlanSessionResult`).

Propose + persist the plan through the independent proposal session
(:class:`planning.planner.Planner`), and resolve the GRADUAL
PROMPT-LOADING LADDER alongside it. One object per planning run;
:meth:`PlanSession.run` returns the :class:`PlanSessionResult` the
orchestrator and the graphs' entry states consume. Never raises: a
partial plan beats none — the result carries whatever landed.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from llm_agents.deep_agents.planning.plan_json import plan_system_prompt
from llm_agents.deep_agents.planning.planner import (
    STUDY_SCOPE_PLAN_PROMPTS, Planner, default_scope_plan_key,
    render_template, template_args,
)
from llm_agents.deep_agents.planning.kg_prompts import HotpathPromptProbe
from llm_agents.deep_agents.planning.prompt_loading import (
    CatalogRow, PromptLadder, merge_hotpath_matches, planner_scoped_data,
)
from llm_agents.deep_agents.planning.session.pass_down import (
    ForkPassDown, PromptBudget, _catalog_mirror, item_labels,
    plan_payload_steps,
)
from llm_agents.deep_agents.planning.session.standing import (
    AGENT_STANDING_SYSTEM, standing_system,
)
from llm_agents.deep_agents.planning.store import (
    AGENT_PROMPT_PLAN_BATCH_FOLLOWUP,
    AGENT_PROMPT_PLAN_REPAIR_MESSAGE,
    AGENT_PROMPT_PLAN_SYSTEM,
    AGENT_PROMPT_STANDING_SYSTEM,
    PlannedStep,
    PromptCatalogRow,
    ThreadPlan,
    PLAN_STORE,
)
from llm_agents.deep_agents.planning.templates import TEMPLATE_LIBRARY
from llm_agents.deep_agents.skills.loader import SkillLoader

logger = logging.getLogger("deep_agents.planning.session")


class PlanSession:
    """The plan agent's ENGINE (2026-11-06 — the former
    :func:`propose_and_persist_plan` module function): propose + persist
    the plan through the independent proposal session
    (:class:`planning.planner.Planner`), and resolve the GRADUAL
    PROMPT-LOADING LADDER alongside it. One object per planning run;
    :meth:`run` returns the :class:`PlanSessionResult` the orchestrator
    and the graphs' entry states consume.

    The plan agent's engine (the PLAN graph's core, thread P of the
        three-graph chain): propose + persist the plan through the
        independent proposal session (:class:`planning.planner.Planner`),
        and resolve the GRADUAL PROMPT-LOADING LADDER alongside it — the
        planner's ``load_prompts`` verdict (when the selection outgrew the
        eager budget) picks which skills' full texts load for the run; the
        rest ride as catalog lines the steps fetch per-key at work time
        (:func:`fetch_prompt_catalog_ladder` / :func:`load_decided_prompts`).

        THE THREAD SPLIT (2026-11-06): *thread_id* is the chain's ROOT (the
        plan thread P — its plan row and plan context row live there);
        *super_thread_id* stamps every row of the run's threads (defaults
        to *thread_id*). The plan's STEP rows live in a FRESH CUSTOM thread
        minted at the first validated batch (``open_custom_thread`` lands
        P's partial plan context + C's first step rows in one connection —
        the graph shows real nodes from the first proposal), later batches
        appending via ``add_plan_states`` on C. The returned ThreadPlan's
        ``thread_id`` is the CUSTOM thread (the step lane's working
        thread). The plan covers the BUSINESS MIDDLE only — the four review
        seats and the final synthesis belong to the RESULT graph (no
        closing-section floor here anymore).

        The proposal is the BATCHED protocol: each batch rides the same
        propose → validate → correct round-trip (ANY rejection — bad JSON,
        missing titles, ops-named steps — goes back to the model with the
        specific complaint); a round that exhausts its corrections ends the
        stream and the run proceeds on whatever batches persisted (the
        PARTIAL plan; only a failed FIRST batch degrades all the way to
        the plain execute lane). The planner reads the COMPACT data
        variant (``planner_scoped_data``: item identities and metric
        names, never the per-metric query recipes) — the step hops keep
        the full data.

        *plan_template_id* short-circuits all of that — THE TEMPLATE LANE
        (``planning.templates``): a run started from a SAVED plan template
        (a type-plan row of ``llm.deep_agent_langgraph_templates`` — the
        Thread Graph Save button's placeholder-templated copy of a
        finished run's plan) fetches the row, renders its ``{{key}}``
        placeholders with THIS run's input args, and persists the steps —
        zero planning LLM calls, the ladder resolving by keyword match
        alone (no verdict). Fail-soft like every planning rung: a missing
        or unusable template logs and falls back to the normal proposal
        session below.

        Returns ``(plan, llm_calls, prompt_budget, plan_payload)``: the
        plan is None only when the FIRST batch never validated or its
        persistence failed — logged, never raised (the run degrades to the
        plain execute lane, the budget still resolving by keyword match so
        whatever lane runs carries the ladder); a mid-stream failure
        returns the PARTIAL plan (the persisted batches, the payload
        stamped ``partial``). *plan_payload* is the COMPLETE plan payload
        (None with the plan) — the plan graph's FINAL OUTPUT NODE lands it
        over the partial first-batch row (:func:`replace_plan_payload`);
        this engine no longer lands it itself. *llm_calls* is the session's
        LLM call count either way (the plan node's context metric — 0 on
        the template lane).
    """

    def __init__(
        self,
        chat_complete: Callable[..., Any],
        goal: str,
        data: Optional[Dict[str, Any]],
        prompt_keys: List[str],
        *,
        model: Optional[str],
        thread_id: str,
        super_thread_id: Optional[str] = None,
        agent_prompts: Optional[Dict[str, str]] = None,
        plan_state_id: Optional[int] = None,
        llm_context_id: Optional[str] = None,
        plan_template_id: Optional[int] = None,
    ) -> None:
        self._chat = chat_complete
        self._goal = goal
        self._data = data
        self._prompt_keys = list(prompt_keys)
        self._model = model
        self._thread_id = thread_id
        self._super_thread_id = super_thread_id
        self._agent_prompts = dict(agent_prompts or {})
        self._plan_state_id = plan_state_id
        self._llm_context_id = llm_context_id
        self._plan_template_id = plan_template_id

    def _result(
        self,
        plan: Optional[ThreadPlan],
        n_calls: int,
        budget: PromptBudget,
        plan_payload: Optional[Dict[str, Any]],
    ) -> PlanSessionResult:
        """The session's outcome from its parts (the custom thread id is
        the plan's own — ThreadPlan.thread_id is the CUSTOM thread)."""
        return PlanSessionResult(
            thread_id=self._thread_id,
            custom_thread_id=(plan.thread_id if plan is not None else None),
            prompt_texts=budget.texts,
            prompt_catalog=budget.catalog,
            plan_ok=plan is not None,
            plan=plan,
            n_calls=n_calls,
            plan_payload=plan_payload,
        )

    @staticmethod
    async def fetch_prompt_catalog_ladder(
        prompt_keys: List[str],
        data: Optional[Dict[str, Any]],
        args: Dict[str, str],
    ) -> Tuple[List[CatalogRow], Dict[str, str]]:
        """The ladder's first two rungs: the catalog fetch (preview
        columns + instruction LENGTHS, never the texts) and the pure
        keyword match over the run's question / scope / item vocabulary.
        Fail-soft to ([], {}) — no catalog, no ladder, the run plans with
        the plan prompts alone."""
        if not prompt_keys:
            return [], {}
        try:
            rows = await PLAN_STORE.fetch_prompt_catalog(prompt_keys)
        except Exception as exc:  # noqa: BLE001 — plan without the ladder
            logger.warning("prompt catalog fetch failed (%s) — planning "
                           "without the loading ladder", exc)
            return [], {}
        catalog = _catalog_mirror(rows)
        prompt_labels = (data or {}).get("prompt_labels")
        matches = PromptLadder(catalog).match(
            question=args.get("question", ""),
            scope_name=args.get("name", ""),
            item_labels=item_labels(data),
            prompt_labels=prompt_labels if isinstance(prompt_labels, dict)
            else None)
        return catalog, matches

    @staticmethod
    async def load_decided_prompts(
        catalog: List[CatalogRow],
        label_matches: Dict[str, str],
        verdict: Optional[List[str]],
        args: Dict[str, str],
    ) -> PromptBudget:
        """The ladder's last rung: the decided load set's FULL instruction
        texts (rendered as dynamic templates) and the not-loaded rows'
        catalog lines. The set is the plan rows ∪ the eager/verdict+forced
        skills (``PromptLadder.load_set``); the fetch is fail-soft —
        a failed fetch leaves the texts absent and the rows cataloged
        (still per-step fetchable), never failing the run."""
        ladder = PromptLadder(catalog)
        keys = ladder.load_set(
            label_matches=label_matches, verdict=verdict,
            instruction_chars={r.prompt_key: r.instruction_chars
                               for r in catalog})
        budget = PromptBudget()
        if keys:
            try:
                rows = await PLAN_STORE.fetch_prompt_instructions(keys)
                budget.texts = SkillLoader.from_rows(rows).render_all(args)
            except Exception as exc:  # noqa: BLE001 — catalog them instead
                logger.warning("decided prompt fetch failed (%s) — the rows "
                               "stay cataloged for per-step fetch", exc)
                budget.texts = {}
        # A row is CATALOGED unless its rendered text actually landed — a
        # failed/partial fetch leaves the decided rows per-step fetchable,
        # not silently dropped.
        budget.catalog = ladder.pass_down(
            [r for r in catalog if r.prompt_key not in set(budget.texts)])
        return budget

    async def run(self) -> PlanSessionResult:
        """The engine: the batched proposal stream persisting each
        validated batch NOW (or the zero-LLM template lane), the ladder
        resolved alongside; never raises (a partial plan beats none —
        the result carries what landed).
        """
        super_id = self._super_thread_id or self._thread_id
        custom_thread_id = uuid.uuid4().hex
        args = template_args(self._data)
        # STUDY-SCOPE DEFAULT: the four study-scope plan prompts are
        # mutually exclusive — one governs the run's scope. When the pick
        # names none of them, the applicable default joins self._prompt_keys
        # BEFORE the ladder's catalog fetch so it rides like any picked
        # plan row (codes named → study-codes; no code tag, All Market →
        # study-market-codes); any explicit family pick suppresses it.
        prompt_keys = list(self._prompt_keys)
        if not STUDY_SCOPE_PLAN_PROMPTS.intersection(prompt_keys):
            prompt_keys = [*prompt_keys, default_scope_plan_key(args)]
        # The ladder's catalog + keyword rungs — BEFORE the proposal: the
        # catalog rides the planner's user message, and a failed proposal
        # still returns a keyword-resolved budget.
        catalog, label_matches = await PlanSession.fetch_prompt_catalog_ladder(
            prompt_keys, self._data, args)
        # THE HOTPATH CORROBORATION RUNG (2026-11-07): optional-labeled
        # rows the PROMOTED kg_hotpaths chains implicate auto-load (the
        # "hotpath" verdict — precedence forced > hotpath > matched);
        # fail-soft to a no-op inside the probe. Bound-label rows keep
        # their unconditional load via the keyword rung above.
        label_matches = merge_hotpath_matches(label_matches, await
            HotpathPromptProbe().probe(
                question=args.get("question", ""),
                scope_name=args.get("name", ""),
                item_labels=item_labels(self._data), catalog=catalog))
        ladder = PromptLadder(catalog)
        ladder_verdict = not ladder.eager(
            instruction_chars={r.prompt_key: r.instruction_chars
                               for r in catalog})

        if self._plan_template_id is not None:
            try:
                template = await TEMPLATE_LIBRARY.fetch_plan_template(
                int(self._plan_template_id))
                if template is None:
                    raise ValueError(
                        f"no plan template {self._plan_template_id}")
                steps = TEMPLATE_LIBRARY.materialize_plan_steps(
                    template.plan_json, args)
                budget = await PlanSession.load_decided_prompts(
                    catalog, label_matches, None, args)
                plan_payload = {
                    "goal": self._goal,
                    # Which template the plan came from — the zero-LLM
                    # provenance block (the UI's plan context shows it).
                    "template": {"template_id": template.template_id,
                                 "name": template.name},
                    "args": args.model_dump(),
                    "prompts_loaded": sorted(budget.texts),
                    "prompts_catalog": sorted(budget.catalog),
                    "steps": plan_payload_steps(steps),
                }
                plan = await PLAN_STORE.open_custom_thread(
                    self._thread_id, plan_payload, custom_thread_id, steps,
                    super_thread_id=super_id,
                    plan_state_id=self._plan_state_id,
                    llm_context_id=self._llm_context_id)
                logger.info("plan materialized from template '%s' (%d steps, "
                            "0 llm calls)", template.name, len(steps))
                return self._result(plan, 0, budget, plan_payload)
            except Exception as exc:  # noqa: BLE001 — template → LLM fallback
                logger.warning("plan template %s unusable (%s) — falling back "
                               "to the LLM proposal session",
                               self._plan_template_id, exc)

        prompts: List[Any] = []
        if prompt_keys:
            try:
                prompts = await PLAN_STORE.fetch_plan_prompts(prompt_keys)
            except Exception as exc:  # noqa: BLE001 — plan with the default
                logger.warning("plan prompts fetch failed, planning with the "
                               "built-in instruction: %s", exc)

        # The plan prompts are DYNAMIC TEMPLATES: their {{key}} placeholders
        # (sec_type / code / name / question) fill from the run's input —
        # the classification-nav scope pick and the focus question. The
        # planner's system prompt is the DB-managed internal row when one is
        # active (static/ai_prompts/01_deep_agent_prompts.sql), else the
        # built-in constant. Past the eager budget the catalog + keyword
        # matches ride too — the reply's load_prompts verdict then narrows
        # the skills load.
        planner = Planner(
            self._chat, goal=self._goal,
            data=planner_scoped_data(self._data),
            plan_instructions=[
                (p.prompt_key, render_template(p.instruction, args))
                for p in prompts],
            catalog=catalog if ladder_verdict else None,
            label_matches=label_matches if ladder_verdict else None,
            model=self._model,
            system_prompt=(
                f"{standing_system(self._agent_prompts)}\n\n"
                f"{(self._agent_prompts or {}).get(AGENT_PROMPT_PLAN_SYSTEM) or plan_system_prompt()}"),
            repair_template=(self._agent_prompts or {}).get(
                AGENT_PROMPT_PLAN_REPAIR_MESSAGE),
            followup_template=(self._agent_prompts or {}).get(
                AGENT_PROMPT_PLAN_BATCH_FOLLOWUP),
        )
        # THE BATCHED PROPOSAL STREAM (plan_json's batched protocol): each
        # validated batch persists NOW — open_custom_thread on the first
        # batch (the PLAN thread's partial plan context + the CUSTOM
        # thread's first pending states land together, so the UI graph
        # shows real nodes from the first proposal instead of waiting out
        # the whole planning conversation), add_plan_states appends every
        # later batch on the custom thread, and the COMPLETE plan payload
        # (returned below) lands over the partial one at the plan graph's
        # output node once the stream closes. A mid-stream failure
        # (transport timeout, a round that exhausted its repairs) keeps
        # whatever already persisted: the run proceeds on the PARTIAL plan
        # — strictly better than the no-plan degrade; only a run whose
        # FIRST batch never validated degrades all the way.
        steps: List[PlannedStep] = []
        plan: Optional[ThreadPlan] = None
        batches = 0
        proposal_error: Optional[Exception] = None
        try:
            async for batch in planner.propose_batches():
                if plan is None:
                    plan = await PLAN_STORE.open_custom_thread(
                        self._thread_id,
                        {"goal": self._goal, "args": args.model_dump(),
                         "steps": plan_payload_steps(batch)},
                        custom_thread_id, batch,
                        super_thread_id=super_id,
                        plan_state_id=self._plan_state_id,
                        llm_context_id=self._llm_context_id)
                else:
                    added = await PLAN_STORE.add_plan_states(
                        plan.thread_id, batch, llm_context_id=self._llm_context_id,
                        super_thread_id=super_id)
                    plan.states.extend(added)
                    plan.steps.extend(batch)
                steps.extend(batch)
                batches += 1
        except Exception as exc:  # noqa: BLE001 — a partial plan beats none
            proposal_error = exc
            if plan is None:
                logger.warning("plan proposal failed (%s), running without "
                               "a plan", exc)
            else:
                logger.warning("plan proposal interrupted after %d batch(es) "
                               "/ %d step(s) (%s) — running with the partial "
                               "plan", batches, len(steps), exc)

        verdict = planner.verdict_keys
        budget = await PlanSession.load_decided_prompts(
            catalog, label_matches,
            verdict if ladder_verdict else None, args)
        plan_payload_out: Optional[Dict[str, Any]] = None
        if plan is not None:
            plan_payload_out = {
                "goal": self._goal,
                # Which prompt texts the plan was laid out under — prompt_key +
                # the version that was current at run time (provenance: an edited
                # prompt bumps its version, so the payload says which revision
                # drove this run's plan), beside the ladder's outcome: the
                # loaded rows (the pass-down) and the cataloged keys (per-step
                # fetches).
                "plan_prompts": [
                    {"prompt_key": p.prompt_key, "version": p.version}
                    for p in prompts
                ],
                "args": args.model_dump(),
                "prompts_loaded": sorted(budget.texts),
                "prompts_catalog": sorted(budget.catalog),
                "steps": plan_payload_steps(steps),
            }
            if proposal_error is not None:
                # The interrupted-stream marker: the UI's plan context shows
                # the plan is partial (the reason rides along).
                plan_payload_out["partial"] = str(proposal_error)[:300]
        return self._result(plan, planner.calls, budget, plan_payload_out)


class PlanSessionResult(BaseModel):
    """The plan agent session's outcome — everything the orchestrator
    and the graphs' entry states need (plain values, checkpoint-safe).
    The plan tree itself stays in the plan store; nothing plan-shaped
    rides the graph state."""
    # The chain's ROOT thread id (the plan thread P — identities, the
    # plan context row and the summary history key on it). None only
    # when the runner started unrecorded (begin_thread degraded).
    thread_id: Optional[str] = None
    # The CUSTOM graph's thread id — the plan's step rows (None on the
    # no-plan degrade: no custom graph is minted, the run stays in P on
    # the degraded execute lane).
    custom_thread_id: Optional[str] = None
    # The ladder's outcome — rides the CUSTOM graph's INITIAL state.
    prompt_texts: Dict[str, str] = Field(default_factory=dict)
    prompt_catalog: Dict[str, str] = Field(default_factory=dict)
    # The entry flag: True → the custom graph's step lane, False → the
    # degraded execute lane (in P, no custom/result threads).
    plan_ok: bool = False
    plan: Optional[ThreadPlan] = None
    n_calls: int = 0
    # The COMPLETE plan payload — the PLAN graph's final output node
    # lands it over the first batch's partial row (replace_plan_payload).
    plan_payload: Optional[Dict[str, Any]] = None


# NOTE (2026-11-06): the session LIFECYCLE (writer + stamp + chain +
# settles) moved to agents/plan_agent.py::PlanAgent — ONE agent == ONE
# node; this package keeps the PURE planning domain (the engine, the
# ladder, the payload helpers, PlanSessionResult). No back-compat
# wrapper: the runner constructs PlanAgent, and the import direction
# planning <- agents keeps the layering (a wrapper here would import
# the agent layer).
