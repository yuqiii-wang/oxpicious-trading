"""llm_agents.deep_agents.langgraph.decision.engine — the System One
READ: :class:`JevDecisionEngine` turns a decision template's facts
into a validated calibrated verdict from the resident local-model
host.

The contract with the CALLERS — and why the convenience methods take
PRIMITIVE kwargs: the two live call sites sit OUTSIDE the langgraph
package (``core.loop``'s spawn gate, ``supervisor.workers``' verdict
ask) and the layering rule says core knows nothing of langgraph, so
they hold a duck-typed injected engine and never import this package;
the engine builds the typed facts and runs the template internally.
Inside langgraph (the package that composes everything) the typed
:meth:`run` + templates are the direct API.

* INJECT, don't reach: the engine instance rides
  ``SupervisorSession(decision_engine=...)`` /
  ``DeepAgent(decision_engine=...)``; ``None`` keeps the legacy lanes
  (every existing stub test and dummy probe stays deterministic). The
  production wiring (``langgraph.runner.base._hop_common`` and
  ``langgraph.super_run.chain``) passes :meth:`JevDecisionEngine.active`,
  None unless the ``jev_decisions`` knob is on (``JEV_DECISIONS=0``
  env kill-switch for generative-lane probes).
* NEVER RAISES, NEVER BLOCKS LONG: every read is fail-soft
  (``JevDecision(ok=False, note=...)`` — transport error, HTTP error,
  contract breach, below the confidence floor all land there) and
  bounded by a SHORT read timeout — the repair lane's pause window is
  run latency; a ~0.1s local read must never stretch it.
* the confidence floor is POLICY (``jev_min_confidence``): at or above
  it the template's ``interpret`` produces the verdict; below it the
  caller's existing lane judges.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

from llm_agents.deep_agents.langgraph.decision.facts import (
    ContentFacts, ForkPointCandidate, ForkPointFacts, SpawnFacts,
    JevDecision, KgCandidate, KgRelevanceFacts, ProbeFacts, ResumeFacts,
    RouteFacts,
)
from llm_agents.deep_agents.langgraph.decision.templates import (
    ContentDomainDecision, ContentIndustryDecision,
    ContentSentimentDecision, DecisionTemplate, FailureProbeDecision,
    KgRelevanceDecision, NodeSpawnDecision, ResumeJudgmentDecision,
    StepRouteForkDecision, StepRouteForkPointDecision,
    StepRouteSpawnDecision, StepRouteJoinDecision, TEMPLATES,
)
from llm_agents.deep_agents.limits import run_limits

logger = logging.getLogger("deep_agents.decision")

# The System One contract answers in ~0.1s on the resident GPU; the
# bound only has to cover a cold rehydrate (weights back from CPU).
_READ_TIMEOUT_S = 20.0


class JevDecisionEngine:
    """The jev-backed decision reader (see module docstring). One
    instance per run is typical; the underlying HTTP client is cheap
    and shared."""

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        min_confidence: Optional[float] = None,
        client: Any = None,
        base_url: Optional[str] = None,
    ) -> None:
        self._model = model
        self._min_confidence = (
            min_confidence if min_confidence is not None
            else run_limits().jev_min_confidence)
        if client is not None:
            self._client = client
        else:
            # The in-repo HTTP client (requests only — no
            # fastapi/torch); the endpoint resolves env → default.
            from server_cluster.llm.client import LocalModelHostClient

            self._client = LocalModelHostClient(base_url=base_url)

    # ------------------------------------------------------------ factory
    @classmethod
    def active(cls) -> Optional["JevDecisionEngine"]:
        """The production engine when the ``jev_decisions`` knob is on,
        else None (the caller's legacy lane judges). Callers treat None
        as "no System One lane". ``JEV_DECISIONS=0`` in the environment
        is the kill-switch (probes that target the GENERATIVE lanes set
        it explicitly — the same precedence pattern as
        ``LOCAL_MODEL_HOST_URL``)."""
        if os.environ.get("JEV_DECISIONS", "").strip().lower() in (
                "0", "false", "off"):
            return None
        if not run_limits().jev_decisions:
            return None
        return cls()

    @property
    def min_confidence(self) -> float:
        return self._min_confidence

    # ------------------------------------------------------------- reads
    async def run(self, template: DecisionTemplate,
                  facts: Any) -> JevDecision:
        """One read through a TEMPLATE (the typed in-langgraph API):
        build the request → validated calibrated answers → the argmax
        option + its probability → the template's verdict mapping.
        ``ok`` requires the confidence floor; a below-floor read still
        carries its raw probabilities for the log/telemetry but is
        ok=False so the caller falls back."""
        state, questions = template.build_request(facts)
        decision = await self._read(
            state, questions, action_key=template.action_key,
            template_key=template.key)
        # Facts-carried knobs override the engine defaults (the
        # multi-answer KG read carries its OWN ok-floor — the
        # single-pick floor does not fit a spread distribution).
        own_floor = getattr(facts, "min_confidence", None)
        # The candidates' answer tokens: KgCandidate exposes .name,
        # ForkPointCandidate .label (the loop's turn_<msg_index> token).
        names = ([getattr(c, "name", None) or getattr(c, "label", "")
                  for c in getattr(facts, "candidates", []) or []]
                 or list(getattr(facts, "industry_candidates", []) or []))
        if own_floor is not None:
            confident = decision.confidence >= own_floor
            decision = decision.model_copy(update={
                "ok": confident,
                "note": "" if confident else
                f"below confidence floor ({decision.confidence:.2f} < "
                f"{own_floor:.2f})",
                "accept_floor": getattr(facts, "accept_floor",
                                        decision.accept_floor),
                "option_names": names})
        else:
            decision = decision.model_copy(update={
                "accept_floor": getattr(facts, "accept_floor",
                                        decision.accept_floor),
                "option_names": names})
        if decision.ok:
            decision = template.interpret(decision)
        return decision

    async def decide_resume(
        self, *, error_text: str, node_title: str = "",
        rate_limited: bool, attempts: int, max_node_retries: int,
        repairs_used: int, max_repairs_per_run: int,
    ) -> JevDecision:
        """The supervisor's NODE RESUMPTION judgment
        (``templates.ResumeJudgmentDecision``) — primitive kwargs so
        the supervisor package never imports this one (the layering
        rule; the engine is injected duck-typed)."""
        return await self.run(
            TEMPLATES[ResumeJudgmentDecision.key],
            ResumeFacts(
                error_text=error_text, node_title=node_title,
                rate_limited=rate_limited, attempts=attempts,
                max_node_retries=max_node_retries,
                repairs_used=repairs_used,
                max_repairs_per_run=max_repairs_per_run))

    async def decide_spawn(
        self, *, syntax_streak: int, threshold: int, spawns_used: int,
        max_spawns: int, script_path: str = "", fail_lines: str = "",
    ) -> JevDecision:
        """The NODE SPAWN gate (``templates.NodeSpawnDecision``) —
        primitive kwargs so core.loop never imports this package."""
        return await self.run(
            TEMPLATES[NodeSpawnDecision.key],
            SpawnFacts(
                syntax_streak=syntax_streak, threshold=threshold,
                spawns_used=spawns_used, max_spawns=max_spawns,
                script_path=script_path, fail_lines=fail_lines))

    async def decide_route_spawn(
        self, *, node_role: str = "", depth: int = 0,
        steps_used: int, max_steps: int, response_head: str = "",
        pending_tools: str = "", sandbox_calls: int = 0,
        fail_streak: int = 0, syntax_streak: int = 0,
        spawns_used: int = 0, max_spawns: int = 0,
    ) -> JevDecision:
        """The ROUTER's SPAWN gate (``templates.StepRouteSpawnDecision``)
        — primitive kwargs so core.loop never imports this package. The
        raw yes/no IS the verdict (affirm-only at the call site: only a
        confident YES preempts the model's own sandbox calls)."""
        return await self.run(
            TEMPLATES[StepRouteSpawnDecision.key],
            RouteFacts(
                node_role=node_role, depth=depth, steps_used=steps_used,
                max_steps=max_steps, response_head=response_head,
                pending_tools=pending_tools, sandbox_calls=sandbox_calls,
                fail_streak=fail_streak, syntax_streak=syntax_streak,
                spawns_used=spawns_used, max_spawns=max_spawns))

    async def decide_route_fork(
        self, *, node_role: str = "", depth: int = 0,
        goal_head: str = "",
        steps_used: int, max_steps: int, response_head: str = "",
        pending_tools: str = "", sandbox_calls: int = 0,
        fail_streak: int = 0, syntax_streak: int = 0,
        spawns_used: int = 0, max_spawns: int = 0,
    ) -> JevDecision:
        """The ROUTER's DRIFT-FORK gate
        (``templates.StepRouteForkDecision``, 2026-11-13) — primitive
        kwargs so core.loop never imports this package. Asked after
        every tool-calling response in a non-scripting node with fork
        budget: has the latest context gradually drifted from the
        node's purpose (goal_head)? The raw yes/no IS the verdict
        (affirm-only at the call site)."""
        return await self.run(
            TEMPLATES[StepRouteForkDecision.key],
            RouteFacts(
                node_role=node_role, depth=depth, goal_head=goal_head,
                steps_used=steps_used,
                max_steps=max_steps, response_head=response_head,
                pending_tools=pending_tools, sandbox_calls=sandbox_calls,
                fail_streak=fail_streak, syntax_streak=syntax_streak,
                spawns_used=spawns_used, max_spawns=max_spawns))

    async def decide_route_fork_point(
        self, *, goal_head: str = "",
        candidates: Optional[List[Dict[str, str]]] = None,
        steps_used: int, max_steps: int,
    ) -> JevDecision:
        """The ROUTER's FORK-POINT gate
        (``templates.StepRouteForkPointDecision``, 2026-11-13) — asked
        only after the drift fork affirmed: from WHICH previous LLM
        context does the fork continue? ``candidates`` are plain
        ``{label, head}`` dicts (the conversation's checkpoint turns)
        so core.loop never imports this package. The raw argmax label
        IS the verdict; the caller maps it back to a cut index
        (unknown / 'now' / below-floor → fork-from-now)."""
        return await self.run(
            TEMPLATES[StepRouteForkPointDecision.key],
            ForkPointFacts(
                goal_head=goal_head,
                candidates=[ForkPointCandidate.model_validate(c)
                            for c in (candidates or [])],
                steps_used=steps_used, max_steps=max_steps))

    async def decide_route_join(
        self, *, node_role: str = "scripting",
        steps_used: int, max_steps: int, response_head: str = "",
        pending_tools: str = "", fail_streak: int = 0,
        syntax_streak: int = 0,
    ) -> JevDecision:
        """The ROUTER's JOIN gate (``templates.StepRouteJoinDecision``)
        — primitive kwargs; the raw yes/no IS the verdict (affirm-only:
        only a confident YES stops the node)."""
        return await self.run(
            TEMPLATES[StepRouteJoinDecision.key],
            RouteFacts(
                node_role=node_role, steps_used=steps_used,
                max_steps=max_steps, response_head=response_head,
                pending_tools=pending_tools, fail_streak=fail_streak,
                syntax_streak=syntax_streak))

    async def decide_failure_probe(
        self, *, failed_role: str = "scripting", failed_goal: str = "",
        error_text: str = "", probes_used: int = 0, max_probes: int = 1,
    ) -> JevDecision:
        """The FAILURE-PROBE gate (``templates.FailureProbeDecision``)
        — primitive kwargs. The caller's policy spawns on every read
        EXCEPT a confident NO (the strongly-prompted inverse of the
        affirm-only lanes)."""
        return await self.run(
            TEMPLATES[FailureProbeDecision.key],
            ProbeFacts(
                failed_role=failed_role, failed_goal=failed_goal,
                error_text=error_text, probes_used=probes_used,
                max_probes=max_probes))

    async def decide_kg_relevance(
        self, *, query: str, scope_label: str = "",
        candidates: List[Dict[str, str]], accept_floor: Optional[float] = None,
    ) -> JevDecision:
        """The KG RELEVANCE gate (``templates.KgRelevanceDecision``) —
        one ``choice`` read over the result entities + ``reject``.
        Primitive kwargs (``candidates`` = plain
        ``{name, kind, summary}`` dicts) so the tool layer never
        imports this package; the verdict's ``action`` is the
        comma-joined accepted names or ``reject``."""
        floor = (accept_floor if accept_floor is not None
                 else run_limits().jev_kg_accept_floor)
        return await self.run(
            TEMPLATES[KgRelevanceDecision.key],
            KgRelevanceFacts(
                query=query, scope_label=scope_label,
                candidates=[KgCandidate.model_validate(c) for c in candidates],
                accept_floor=floor,
                min_confidence=run_limits().jev_kg_confidence))

    async def decide_content(
        self, *, text_head: str, total_chars: int, source: str = "tool",
        industry_candidates: Optional[List[str]] = None,
        accept_floor: Optional[float] = None,
    ) -> Dict[str, JevDecision]:
        """The TEXT PREVIEW classification (``templates.Content*Decision``,
        2026-11-12) — THREE soft reads over one tool result's text:
        sentiment (noul), coarse domain (8-way choice), industry
        attempt (≤26 prefiltered candidates; skipped when fewer than
        two — a one-candidate read answers itself). All three are
        fail-soft soft evidence; the caller (the preview envelope)
        rides what cleared the floors and ignores the rest. Primitive
        kwargs so ``core.loop`` never imports this package; the
        industry candidates resolve INSIDE this package (the resolver
        is the DB-reading half of the classification, not core's
        concern). Returns the three decisions keyed
        ``sentiment`` / ``domain`` / ``industry`` — an omitted question
        carries ``JevDecision(ok=False, note='skipped')``."""
        floor = (accept_floor if accept_floor is not None
                 else 0.5)
        facts = ContentFacts(
            text_head=text_head, total_chars=total_chars, source=source,
            industry_candidates=list(industry_candidates or []),
            accept_floor=floor)
        industry_task = (
            self.run(TEMPLATES[ContentIndustryDecision.key], facts)
            if len(facts.industry_candidates) >= 2 else self._skipped())
        sentiment, domain, industry = await asyncio.gather(
            self.run(TEMPLATES[ContentSentimentDecision.key], facts),
            self.run(TEMPLATES[ContentDomainDecision.key], facts),
            industry_task)
        return {"sentiment": sentiment, "domain": domain,
                "industry": industry}

    async def _skipped(self) -> JevDecision:
        return JevDecision(ok=False, note="skipped — no candidates")

    # ------------------------------------------------------------ plumbing
    async def _read(
        self, state: Any, questions: Any, *,
        action_key: str, template_key: str,
    ) -> JevDecision:
        started = time.monotonic()
        try:
            response = await asyncio.to_thread(
                self._client.systemone, questions=questions,
                state=state, model=self._model,
                read_timeout_s=_READ_TIMEOUT_S)
            answer = response.answers[action_key]
        except Exception as exc:  # noqa: BLE001 — the fail-soft contract
            note = f"{type(exc).__name__}: {exc}"
            logger.info("jev decision lane unavailable (%s)", note[:200])
            _record_read(template_key, action_key, state, started,
                         decision=None, is_failed=True, error=note)
            return JevDecision(ok=False, note=note, template=template_key)
        probabilities = getattr(answer, "probabilities", None)
        if probabilities is None:  # the noul answer — a binary read
            p_yes = float(answer.noul)
            probabilities = {"yes": p_yes, "no": 1.0 - p_yes}
        best = max(probabilities, key=probabilities.get)
        confidence = float(probabilities[best])
        decision = JevDecision(
            action=best, confidence=round(confidence, 4),
            probabilities={k: round(v, 4)
                           for k, v in probabilities.items()},
            ok=confidence >= self._min_confidence,
            note="" if confidence >= self._min_confidence else
            f"below confidence floor ({confidence:.2f} < "
            f"{self._min_confidence:.2f})",
            model=response.model,
            template=template_key,
        )
        logger.info(
            "jev decision: %s[%s]=%s p=%.2f ok=%s (%s)",
            template_key, action_key, decision.action,
            decision.confidence, decision.ok, decision.model)
        _record_read(template_key, action_key, state, started,
                     decision=decision)
        return decision


# The single recording seam — every read above lands here as ONE
# llm.deep_agent_aux_steps decision row (the UI's "decision" step).
# Observability only: the verdict payload is deliberately excluded from
# every LLM context (the callers' resume instructions and envelope
# riders carry action text, never the raw read); fail-soft — the
# recorded step must never break the lane it observed.
_FACT_SUMMARY_KEYS = ("error_text", "response_head", "query", "text_head",
                      "failed_goal", "node_title")


def _facts_head(facts: Any) -> str:
    """A one-line face of the typed facts for the steps list."""
    for key in _FACT_SUMMARY_KEYS:
        value = getattr(facts, key, None)
        if value:
            return f"{key}: {str(value)[:160]}"
    return ""


def _record_read(template_key: str, action_key: str, facts: Any,
                 started: float, *, decision: Optional[Any],
                 is_failed: bool = False, error: Optional[str] = None) -> None:
    try:
        from llm_agents._core.aux_steps import record_decision

        duration_ms = round((time.monotonic() - started) * 1000)
        payload = None
        dump = getattr(facts, "model_dump", None)
        if callable(dump):
            payload = dump()
        if decision is None:  # the exception path — the read never landed
            output_text = "unavailable"
            output_payload = None
        else:
            output_text = (
                f"{decision.action} p={decision.confidence:.2f} "
                f"ok={str(decision.ok).lower()}"
                + (f" — {decision.note}" if decision.note else ""))
            output_payload = decision.model_dump()
        record_decision(
            lane=template_key,
            title=f"System One read — {template_key}",
            input_text=_facts_head(facts),
            output_text=output_text,
            input_payload=payload,
            output_payload=output_payload,
            duration_ms=duration_ms,
            is_failed=is_failed, error=error)
    except Exception:  # noqa: BLE001 — the recorded step never breaks the lane
        logger.debug("aux decision step unavailable", exc_info=True)
