"""llm_agents.deep_agents.planning.store.outputs — the RUN OUTPUT
CONTEXTS + the final settle (the OutputStoreMixin).

The context rows that are DOCUMENTS, not step traffic: the run's
final-result answer (the UI's Result view), a middle graph's
graph-output synthesis (the three-graph chain's handoff artifact),
the supervisor's supervisor-review diagnosis — and ``finalize_thread``,
the run-end settle for whatever the lane left running or pending.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from _common.sql_writes import render_sql_write
from llm_agents.deep_agents import events

from llm_agents.deep_agents.planning.store.base import _StoreBase
from llm_agents.deep_agents.planning.store.models import (
    CRITICAL_KEY, RESULT_NODE, SUPERVISOR_NODE, _NOTE_MAX,
    extract_critical,
)


class OutputStoreMixin(_StoreBase):
    """The output-document context rows + the run-end settle."""

    async def record_final_result(self,
        thread_id: str, *,
        answer: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Optional[int]:
        """The run's FINAL RESULT — the HARDCODED result node's fixed output:
        one context row of kind final-result whose payload is the run's
        answer document ({"answer": ...}, NOT truncated — it is the document
        the UI's Result view displays) or, when execute failed, its failure
        reason ({"error": ...}). The answer is the output seat's RAW final
        reply: a trailing [CRITICAL] marker line (the output directives'
        contract) is parsed out here and flags the row. Keyed to the result
        node (no state row, no chained context — the node does no LLM
        work). Returns the context id, or None when there was neither an
        answer nor an error to record (nothing written)."""
        payload: Optional[Dict[str, Any]] = None
        if answer:
            critical, clean = extract_critical(str(answer))
            payload = {"answer": clean}
            if critical:
                payload[CRITICAL_KEY] = True
        elif error:
            payload = {"error": str(error)[:_NOTE_MAX]}
        if payload is None:
            return None
        conn = await self._connect()
        try:
            context_id = await self._insert_context(
                conn, thread_id, "final-result",
                json.dumps(payload, ensure_ascii=False),
                node=RESULT_NODE)
        finally:
            await conn.close()
        events.publish(events.DeepAgentEvent(
            type=events.EventType.RESULT_RECORDED,
            thread_id=thread_id,
        ))
        return int(context_id) if context_id is not None else None


    async def record_graph_output(self,
        thread_id: str,
        output: Optional[str],
        *,
        node: str = "output",
        error: Optional[str] = None,
        llm_context_id: Optional[str] = None,
    ) -> Optional[int]:
        """A MIDDLE graph's FINAL OUTPUT NODE row — the three-graph chain's
        handoff artifact: one context row of kind graph-output whose payload
        is the custom graph's synthesis document ({"output": ...}, NOT
        truncated — it is what the RESULT graph's four reviews and final
        synthesis read as their input, and what the UI shows on the handoff
        edge). The output is the seat's RAW final reply: a trailing
        [CRITICAL] marker line (the output directives' contract) is parsed
        out here and flags the row. Keyed to the output node and its ONE
        chained LLM context (*llm_context_id* — the synthesis conversation
        that produced it). Returns the context id, or None when there was
        nothing to record."""
        payload: Optional[Dict[str, Any]] = None
        if output:
            critical, clean = extract_critical(str(output))
            payload = {"output": clean}
            if critical:
                payload[CRITICAL_KEY] = True
        elif error:
            payload = {"error": str(error)[:_NOTE_MAX]}
        if payload is None:
            return None
        conn = await self._connect()
        try:
            context_id = await self._insert_context(
                conn, thread_id, "graph-output",
                json.dumps(payload, ensure_ascii=False),
                node=node, llm_context_id=llm_context_id)
        finally:
            await conn.close()
        events.publish(events.DeepAgentEvent(
            type=events.EventType.RESULT_RECORDED,
            thread_id=thread_id,
        ))
        return int(context_id) if context_id is not None else None


    async def record_supervisor_review(self,
        thread_id: str, review: str, *,
        llm_context_id: Optional[str] = None,
    ) -> Optional[int]:
        """The supervisor node's MAIN-PATH REVIEW — one context row of kind
        supervisor-review whose payload is the bounded cross-check document
        ({"review": ...}, capped by the engine). Keyed to the supervisor
        node (no state row) and its ONE chained LLM context
        (*llm_context_id* — the conversation that produced it, the same key
        as its node item and request chain), so the UI's Thread Graph shows
        the review beside the counts of the calls behind it. Returns the
        context id."""
        conn = await self._connect()
        try:
            context_id = await self._insert_context(
                conn, thread_id, "supervisor-review",
                json.dumps({"review": str(review)}, ensure_ascii=False),
                node=SUPERVISOR_NODE, llm_context_id=llm_context_id)
        finally:
            await conn.close()
        events.publish(events.DeepAgentEvent(
            type=events.EventType.RESULT_RECORDED,
            thread_id=thread_id,
        ))
        return int(context_id) if context_id is not None else None


    async def finalize_thread(self, thread_id: str, *, failure: Optional[str] = None) -> None:
        """Settle whatever the run ended with: steps still in_progress are
        failed (the run died inside them / ended mid-step), still-pending
        steps are skipped (never reached). Children follow the same rule."""
        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_states/finalize_fail_running",
                thread_id,
                (failure or "run ended inside this step")[:_NOTE_MAX],
            )
            await conn.execute(write.sql, *write.params)
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_states/finalize_skip_pending",
                thread_id,
            )
            await conn.execute(write.sql, *write.params)
        finally:
            await conn.close()
        events.publish(events.DeepAgentEvent(
            type=events.EventType.STATES_SETTLED,
            thread_id=thread_id,
        ))
