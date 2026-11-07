"""llm_agents.deep_agents.planning.store.identities — the SCOPE →
IDENTITY LINK (the IdentityStoreMixin + the pure scope parser).

``llm.deep_agent_identities`` is the code <-> thread_id link — one row
per scoped run subject, the reverse index the summary history's
per-code read filters on. ``run_thread`` records the rows at RUN START
from the request's classification-nav scope picks (plan persisted or
not); market-wide runs create none.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from _common.sql_writes import render_sql_write

from llm_agents.deep_agents.planning.store.base import _StoreBase
from llm_agents.deep_agents.planning.store.models import ThreadIdentity


def identities_from_scope(
    data: Optional[Dict[str, Any]],
) -> List[ThreadIdentity]:
    """The run's scope picks (the request's structured ``data.scopes``
    list, falling back to the legacy single ``data.scope`` object) as
    :class:`ThreadIdentity` entries — one per picked subject, deduped on
    (sec_type, code), skipping malformed entries; empty when the run is
    market-wide. Identity rows are bookkeeping, never worth failing a
    run over. Each entry's shape is the analysis summary bridge's:
    ``{"secType": ..., "code" | "industry", "name"?}``
    (services/ai-summary.service.ts)."""
    raw = (data or {}).get("scopes")
    entries: List[Any] = []
    if isinstance(raw, list):
        entries = [e for e in raw if isinstance(e, dict)]
    elif isinstance((data or {}).get("scope"), dict):
        entries = [data["scope"]]
    if not entries:
        return []
    from _common.labels import SEC_TYPE_CODE  # lazy: the module pulls numpy

    out: List[ThreadIdentity] = []
    seen: set = set()
    for scope in entries:
        sec_type = SEC_TYPE_CODE.get(str(scope.get("secType") or "").strip())
        code = str(scope.get("code") or scope.get("industry") or "").strip()
        if sec_type is None or not code:
            continue
        if (int(sec_type), code) in seen:
            continue
        seen.add((int(sec_type), code))
        name = scope.get("name")
        out.append(ThreadIdentity(
            sec_type=int(sec_type),
            code=code,
            name=str(name).strip() if isinstance(name, str) and name.strip() else None,
        ))
    return out


class IdentityStoreMixin(_StoreBase):
    """The identity-row write."""

    async def record_thread_identity(self,
        thread_id: str, identity: ThreadIdentity
    ) -> None:
        """Link ONE scope subject to the thread id — one
        llm.deep_agent_identities row (the code <-> thread_id reverse index
        the summary history's per-code read filters on); a multi-pick run
        records one row per subject. Called by ``run_thread`` at RUN START
        from the request's classification-nav scope picks, so every generate
        sets the link — plan persisted or not (rows whose thread never
        planned simply never join to states). Market-wide runs (no scope
        pick) create no row."""
        conn = await self._connect()
        try:
            write = render_sql_write(
                "llm_agents/deep_agents/planning/store/"
                "deep_agent_identities/insert",
                thread_id, identity.sec_type, identity.code, identity.name,
            )
            await conn.execute(write.sql, *write.params)
        finally:
            await conn.close()
