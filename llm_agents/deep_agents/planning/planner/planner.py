"""llm_agents.deep_agents.planning.planner.planner — the PLAN PROPOSAL
SESSION as an independent class (:class:`Planner`).

The deep-agent run's plan node asks the model to translate its intent
into the states JSON the runner compiles the thread from. This module
owns that whole conversation as ONE class:

  * it assembles what the model reads (the assembler's business
    framing, the rendered texts of the loaded plan prompts, and — past
    the eager prompt budget — the PROMPT CATALOG of the not-yet-loaded
    skills whose ``load_prompts`` verdict the reply carries);
  * it calls the LLM and validates the reply against the plan-JSON
    contract; on ANY rejection the SPECIFIC complaint goes BACK to the
    model as a correction turn — its own bad reply plus the repair
    instruction — up to *repair_rounds* corrections after the first
    attempt;
  * ``propose_batches`` streams validated batches (the batched
    proposal protocol — each accepted batch followed by ONE small
    follow-up turn until the model closes the plan or the batch cap
    is spent); ``propose`` drains the stream.

Pure domain: no DB, no LangGraph, no provider import — the chat
callable rides in duck-typed (anything async with
``chat_complete(messages, model=, temperature=)``), so a session
unit-tests against a stub.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.plan_json import (
    PlanJsonError, batch_followup_message, parse_plan_reply,
    plan_system_prompt, repair_user_message, subplan_system_prompt,
)
from llm_agents.deep_agents.planning.planner.assembler import (
    PlanPromptAssembler,
)
from llm_agents.deep_agents.planning.prompt_loading import CatalogRow
from llm_agents.deep_agents.planning.store import PlannedStep

logger = logging.getLogger("deep_agents.planner")


class Planner:
    """One plan-proposal session: propose → validate → correct, until
    the model's reply compiles into accepted thread steps.

    The session's CONVERSATION is the object's state (``messages`` —
    every rejected reply stays in it as an assistant turn followed by
    the repair instruction, so each retry sees the full correction
    history) and ``calls`` counts the LLM calls it spent (the plan
    node's context metric, valid even when the session ends rejected).
    ``verdict_keys`` carries the FIRST validated reply's
    ``load_prompts`` verdict (None until one validates, None again
    when the reply carried none — the caller treats both as "no
    narrowing" per ``PromptLadder.load_set``).

    Built either from the run's business input (``goal`` / ``data`` /
    ``plan_instructions`` — the rendered ``(prompt_key, text)`` pairs
    of the loaded plan prompts) or from an explicit ``messages`` list
    (the functional entry point's shape). ``catalog`` /
    ``label_matches`` add the gradual prompt-loading ladder's catalog
    rung to the user message (previews of the not-yet-loaded skills;
    see ``planning.prompt_loading``). ``chat_complete`` is
    duck-typed; ``repair_rounds`` corrections follow the first attempt.
    """

    def __init__(
        self,
        chat_complete: Any,
        *,
        goal: str = "",
        data: Optional[Dict[str, Any]] = None,
        plan_instructions: Optional[List[Tuple[str, str]]] = None,
        catalog: Optional[Sequence[CatalogRow]] = None,
        label_matches: Optional[Dict[str, str]] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.2,
        repair_rounds: Optional[int] = None,
        system_prompt: Optional[str] = None,
        repair_template: Optional[str] = None,
        followup_template: Optional[str] = None,
    ) -> None:
        # Both default from the static deep_agent_config at session
        # build time (an explicit argument wins) — the config edits
        # apply to the next planning session without a restart.
        limits = run_limits()
        repair_rounds = (limits.plan_repair_rounds if repair_rounds
                         is None else repair_rounds)
        system_prompt = (plan_system_prompt() if system_prompt is None
                         else system_prompt)
        self._chat = chat_complete
        self._model = model
        self._temperature = temperature
        self._repair_rounds = max(0, int(repair_rounds))
        # The DB-editable message frames (2026-11-07): the fetched
        # type-3 rows ``deep-agent-plan-repair-message`` /
        # ``deep-agent-plan-batch-followup`` (None = the plan_json
        # builders' built-in frames apply).
        self._repair_template = repair_template
        self._followup_template = followup_template
        self.calls = 0
        self.verdict_keys: Optional[List[str]] = None
        # The init-message builder inputs (the slim-retry path rebuilds
        # the FIRST user message without its heaviest blocks when the
        # request overruns the per-request budget). A session built
        # from explicit messages (the fork pre-planner) has no
        # components — its input is already tiny, so the slim-retry
        # never applies (a budget overrun there just propagates).
        self._from_messages = messages is not None
        self._assembler = PlanPromptAssembler(
            goal, data, list(plan_instructions or []),
            catalog=catalog, label_matches=label_matches)
        self.messages: List[Dict[str, Any]] = (
            [dict(m) for m in messages] if messages is not None else [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": self._assembler.user_content()},
            ])

    @classmethod
    async def preplan_fork(
        cls,
        chat_complete: Callable[..., Any],
        delegate: str,
        data: Optional[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        repair_template: Optional[str] = None,
    ) -> List[PlannedStep]:
        """One delegated fork's PRE-PLANNED sub-steps — the small parent-
        side proposal call that breaks a fork's "plan yourself, then work"
        big task into (this tiny plan call) + (an executor that works
        pre-laid-out nodes). The structural fix for the measured failure
        (2026-11-03 run: 3 of 4 forks burned their whole conversation-hop
        budget exploring and never called update_plan — the fork's
        planning child row failed at the step limit and the section was
        lost).

        The session reuses the Planner over ``subplan_system_prompt`` (the
        SHORT fork-scoped contract — one JSON reply, no batches): its input
        is the fork goal + the run's business framing alone, far under the
        per-request budget. ``system_prompt`` is the fetched
        ``deep-agent-subplan-system`` row and ``repair_template`` the
        fetched ``deep-agent-plan-repair-message`` frame (both from the
        run's agent_prompts map; None = the built-in constants). Sub-steps
        come back INLINE (delegate markers stripped — they run in the
        fork's own conversation, not as spawns). Raises on any failure;
        the delegated hop's fail-soft wrapper then falls back to the
        child self-planning contract."""
        framing = PlanPromptAssembler("", data, []).framing()
        user = delegate if not framing else (
            f"{delegate}\n\n[The fork's slice of the run's context]\n{framing}")
        planner = cls(
            chat_complete,
            messages=[
                {"role": "system",
                 "content": system_prompt or subplan_system_prompt()},
                {"role": "user", "content": user},
            ],
            model=model, repair_rounds=1, repair_template=repair_template)
        steps = await planner.propose()
        return [s.model_copy(update={'delegate': None}) for s in steps]

    async def propose_batches(self):
        """The INCREMENTAL PROPOSAL STREAM — an async generator of
        validated step batches, the batched protocol of
        ``plan_json.plan_system_prompt``: the first round reads the
        session's init message (the same user content the single-shot
        form built) and proposes the FIRST few steps; every accepted
        batch with ``more`` true is followed by ONE small follow-up
        turn (``batch_followup_message`` — the nodes-so-far list + the
        next-batch ask) until the model closes the plan (``more``
        false/absent) or ``run_limits().plan_proposal_batches`` rounds
        are spent. Each round is the SAME propose → validate → correct
        contract as before — a rejected batch feeds back through
        ``repair_user_message`` within its round (``repair_rounds``
        corrections), and a round that exhausts them ends the STREAM
        (raising PlanJsonError) with every earlier batch already
        yielded — the runner keeps whatever validated and persisted.

        Each yielded batch was accepted by ``parse_plan_reply``;
        ``verdict_keys`` carries the FIRST batch's ``load_prompts``
        verdict (None until one arrives); ``calls`` counts every LLM
        call including repairs. Transport errors propagate mid-stream —
        the runner's fail-soft wrapper keeps the batches so far."""
        limits = run_limits()
        max_batches = max(1, int(limits.plan_proposal_batches))
        nodes_so_far: List[str] = []
        slimmed = False
        for round_index in range(max_batches):
            last_round = round_index + 1 >= max_batches
            more = True
            for attempt in range(self._repair_rounds + 1):
                try:
                    message = await self._chat(
                        self.messages, model=self._model,
                        temperature=self._temperature)
                except Exception as exc:  # noqa: BLE001 — budget slim-retry
                    # THE INPUT-REFACTOR RUNG: the init request outran
                    # the per-request wall-clock budget — the input is
                    # too heavy for one call. Rebuild the init message
                    # SLIM (drop the raw JSON block + the plan-prompt
                    # texts; the business framing keeps naming every
                    # subject/item) and retry once; a slimmed session
                    # that overruns again propagates (the runner keeps
                    # the no-plan degrade). Duck-typed so this pure
                    # module needs no _core import.
                    if (round_index == 0 and attempt == 0 and not slimmed
                            and not self._from_messages
                            and getattr(exc, "budget_exceeded", False)):
                        slimmed = True
                        logger.warning(
                            "plan init request overran the per-request "
                            "budget (%s) — retrying with the slimmed "
                            "input", exc)
                        self.messages = [
                            self.messages[0],
                            {"role": "user",
                             "content": self._assembler.user_content(
                                 slim=True)},
                        ]
                        continue
                    raise
                self.calls += 1
                reply = str(message.get("content") or "")
                try:
                    parsed = parse_plan_reply(reply)
                    steps, verdict, more = (parsed.steps, parsed.verdict,
                                            parsed.more)
                    if self.verdict_keys is None:
                        self.verdict_keys = verdict
                except PlanJsonError as exc:
                    if attempt >= self._repair_rounds:
                        raise
                    logger.info("plan batch rejected (round %d, attempt "
                                "%d/%d), asking the model to correct it: "
                                "%s", round_index + 1, attempt + 1,
                                self._repair_rounds, exc)
                    self.messages = self.messages + [
                        {"role": "assistant", "content": reply},
                        {"role": "user",
                         "content": repair_user_message(
                             str(exc), template=self._repair_template)},
                    ]
                    continue
                yield steps
                nodes_so_far.extend(
                    f"{s.node}: {s.title}" for s in steps)
                break
            # (2026-11-06: the CLOSING NUDGE round is GONE — the review
            # wave and the final synthesis moved to the run's separate
            # RESULT graph, so a closed plan needs no closing section.)
            # The plan closed (or the batch cap lets no follow-up ask
            # through): the stream ends here either way.
            if not more or last_round:
                if more and last_round:
                    logger.info("plan batches: cap %d reached with the "
                                "model still proposing — closing the "
                                "plan at %d node(s)", max_batches,
                                len(nodes_so_far))
                return
            self.messages = self.messages + [
                {"role": "assistant", "content": reply},
                {"role": "user",
                 "content": batch_followup_message(
                     nodes_so_far, template=self._followup_template)},
            ]

    async def propose(self) -> List[PlannedStep]:
        """Drain :meth:`propose_batches` into the full step list — the
        single-shot entry point the runner's template-free lane and the
        tests read; raises when the FIRST round never validated (the
        pre-batch contract), returns the batches so far when a later
        round failed after earlier ones yielded."""
        steps: List[PlannedStep] = []
        async for batch in self.propose_batches():
            steps.extend(batch)
        return steps
