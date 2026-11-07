"""llm_agents.deep_agents.planning.store.base — the PlanStore's SHARED
PLUMBING (the mixin root).

``_StoreBase`` carries what every store mixin needs and none of the
domain methods: the DB connection seam (``_connect`` — one short-lived
asyncpg connection per call via ``_common.db_commons``, imported
lazily per the activation-order rule), the contexts table's ONE write
place (``_insert_context`` — the row's is_critical lift + tags
tokenization live here so every entry kind carries them), the
step-input backfill, and the asyncpg-record -> ``StateRow`` mapper.
The mixin classes in prompts/states/outputs/chain/identities subclass
this root; ``store/__init__.py`` composes them into ``PlanStore``.
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from _common.sql_writes import render_sql_write

from llm_agents.deep_agents.planning.store.models import (
    CRITICAL_KEY, StateRow, _kind, _payload_text, _tokenize_tags_sync,
)


class _StoreBase:
    """The store's connection + write plumbing (no domain methods)."""

    async def _connect(self):
        from _common.db_commons import get_db_connection_async

        return await get_db_connection_async()


    async def _insert_context(self,
        conn, thread_id: str, kind_label: str, payload_json: str, *,
        node: Optional[str] = None, state_id: Optional[int] = None,
        llm_context_id: Optional[str] = None,
    ) -> int:
        """Insert one llm.deep_agent_contexts row with its chained-context
        keys — the ONE place the table is written. One entry = one payload
        per thread per node per task/step: *node* names the entry's node,
        *state_id* its task row, *llm_context_id* the ONE chained LLM
        context (uuid hex) of the node execution whose conversation produced
        it — the key that links the entry to its llm.llm_requests rows
        (llm_requests.chain_id). The payload's ``"critical": true`` flag
        (the model's mark of a critical discovery) lifts into the
        is_critical column, and the payload text tokenizes into tags (off
        the event loop; NULL on tokenizer failure — the backfill heals).
        Returns the new context id."""
        try:
            critical = bool(json.loads(payload_json).get(CRITICAL_KEY))
        except (TypeError, ValueError):
            critical = False
        tokens = await asyncio.to_thread(_tokenize_tags_sync, payload_json)
        write = render_sql_write(
            "llm_agents/deep_agents/planning/store/deep_agent_contexts/insert",
            thread_id, _kind(kind_label), payload_json,
            node, state_id, llm_context_id, critical, tokens,
        )
        context_id = await conn.fetchval(write.sql, *write.params)
        return int(context_id)


    async def _backfill_input_state_ids(self, conn, thread_id: str) -> None:
        """Point this thread's unlinked step-input context rows at their
        states: a state insert needs its input_context_id FIRST, so the
        entries mint before their state rows exist — this one scoped UPDATE
        links them after the inserts (and heals any legacy unlinked rows of
        the thread the same way)."""
        write = render_sql_write(
            "llm_agents/deep_agents/planning/store/"
            "deep_agent_contexts/link_input_states",
            thread_id, _kind("step-input"),
        )
        await conn.execute(write.sql, *write.params)

    def _state_row(self, record) -> StateRow:
        return StateRow(
            state_id=int(record[0]),
            thread_id=record[1],
            parent_state_id=int(record[2]) if record[2] is not None else None,
            node=record[3],
            title=record[4],
            status=record[5],
            ordinal=int(record[6]),
            input_context_id=int(record[7]) if record[7] is not None else None,
            result_context_id=int(record[8]) if record[8] is not None else None,
            error=record[9],
            started_at=str(record[10]) if record[10] is not None else None,
            finished_at=str(record[11]) if record[11] is not None else None,
            llm_context_id=record[12] if len(record) > 12 else None,
        )
