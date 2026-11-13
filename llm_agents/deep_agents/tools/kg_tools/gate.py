"""llm_agents.deep_agents.tools.kg_tools.gate — the KG RELEVANCE GATE
(2026-11-12): a kg_search / kg_expand result is judged by the resident
jev model (System One, one ``choice`` read over the ranked entities +
an explicit ``reject`` option) BEFORE its render enters the
conversation.

* ``accepted`` — every candidate whose probability clears
  ``jev_kg_accept_floor`` ships (multi-answer); the service re-renders
  only the accepted entities.
* ``reject`` — the result answered the query with noise: the LLM
  receives ONLY the short synonym-loop nudge (the token saving the
  gate exists for), never the full dossier.
* FAIL-SOFT — a disabled knob, ``JEV_KG_GATE=0``, no injected engine,
  an unavailable host, or a below-``jev_min_confidence`` read ships
  the UNGATED full render unchanged (the caller's existing behavior
  judges; the gate can never blank a result it could not read).

Layering: this class adapts the duck-typed injected decision engine
(``decide_kg_relevance`` primitive kwargs — the same injection pattern
as ``core.loop``'s spawn gate) onto the kg service's ``judge``
callback signature, so ``llm_agents.kg`` stays independent of the
deep-agent package. The reject nudges reuse the agent-flow synonym
vocabulary the tool layer owns.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from llm_agents.deep_agents.limits import run_limits
from llm_agents.kg import KgGateCandidate, KgGateVerdict

logger = logging.getLogger("deep_agents.kg_gate")


class KgJevGate:
    """The kg results' relevance judge — ONE object per kit mount,
    holding the enabled check and the ``judge`` callback the gated
    service flows call."""

    def __init__(self, decision_engine: Any = None,
                 accept_floor: Optional[float] = None) -> None:
        self._engine = decision_engine
        self._accept_floor = accept_floor

    # ------------------------------------------------------------ state
    @property
    def engine(self) -> Any:
        return self._engine

    @classmethod
    def enabled(cls) -> bool:
        """The knob gate: ``jev_kg_gate`` on AND no ``JEV_KG_GATE=0``
        kill-switch (the generative-lane probes' same env contract as
        ``JEV_DECISIONS=0``)."""
        if os.environ.get("JEV_KG_GATE", "").strip().lower() in (
                "0", "false", "off"):
            return False
        return bool(run_limits().jev_kg_gate)

    # ------------------------------------------------------------ judge
    async def judge(self, query: str, scope_label: str,
                    candidates: List[KgGateCandidate]) -> KgGateVerdict:
        """The service-facing callback: one engine read mapped onto the
        tri-state verdict. Any failure lands as ``unavailable`` — the
        UNGATED render ships."""
        if self._engine is None:
            return KgGateVerdict(status="unavailable")
        try:
            decision = await self._engine.decide_kg_relevance(
                query=query, scope_label=scope_label,
                candidates=[c.model_dump() for c in candidates],
                accept_floor=self._accept_floor)
        except Exception as exc:  # noqa: BLE001 — the fail-soft contract
            logger.info("kg relevance gate unavailable (%s)",
                        f"{type(exc).__name__}: {exc}"[:200])
            return KgGateVerdict(status="unavailable")
        if not decision.ok:
            return KgGateVerdict(status="unavailable")
        if decision.action == "reject":
            return KgGateVerdict(status="rejected")
        names = [name for name in decision.action.split(",") if name]
        return (KgGateVerdict(status="accepted", names=names)
                if names else KgGateVerdict(status="rejected"))

    # ----------------------------------------------------------- nudges
    @staticmethod
    def reject_nudge(query: str, scope_label: str) -> str:
        """The REJECTED result's whole answer: no dossier, the
        synonym-loop teaching only (the model proposes 2-3 synonyms /
        paraphrases and re-searches, or computes in the sandbox)."""
        return (
            "[Jev relevance gate — REJECTED] The semantic search for "
            f"'{query.strip()[:200]}' over {scope_label} found no "
            "entity whose meaning matches the query. Do NOT list "
            "schemas blind. PROPOSE 2-3 SYNONYMS / paraphrases "
            "(Chinese AND English trader terms, e.g. 间距→spread→乖离, "
            "排列→alignment, 量→volume/amount) and kg_search them "
            "again; when those miss too, STOP searching and compute "
            "in the sandbox — sandbox_write_file a script + "
            "sandbox_run it, importing study_kit first "
            "(db.fetch_daily_bars / studies.*).")


def gate_for(decision_engine: Any = None,
             accept_floor: Optional[float] = None) -> Optional[KgJevGate]:
    """The kit mount's gate: a :class:`KgJevGate` when the knob is on
    AND an engine is provided, else None (the ungated historical
    path — every unscoped mount and every existing stub test)."""
    if decision_engine is None or not KgJevGate.enabled():
        return None
    return KgJevGate(decision_engine, accept_floor=accept_floor)
