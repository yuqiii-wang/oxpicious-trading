"""llm_agents.kg.static_rules.freshness — the READ-TIME freshness
gate: before the KG service reads a namespace graph (on the graph
cache's reload path), the corpus rules guarding that namespace get
their corpus stat-scanned (file count + MAX mtime — ms-cheap, NO
content read) and compared against their ``llm.kg_rule_syncs``
fingerprint row. On a diff — corpus files newer than the last sync,
the file count changed (templates added/deleted), or NO row yet (a
fresh install's bootstrap) — the rule's idempotent sync runs INLINE,
BEFORE the read: stale rule-owned entities pruned, drafts upserted,
derived edges re-derived, fingerprint restamped (one transaction,
"delete/insert across the KG tables"), and the graph is then loaded
from the freshly synced rows.

Fail-soft by the KG contract: any gate error logs a warning and serves
the stored DB state — a KG tool result never breaks on the freshness
layer. No lock around the gate: two concurrent reloads may both sync,
which is idempotent and single-transaction — the worst case is one
wasted pass (and an asyncio.Lock on the process-wide service singleton
would bind to one event loop and break the next ``asyncio.run``)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from llm_agents.kg.static_rules.data_definitions import StaticRulesReport
from llm_agents.kg.static_rules.sources import SourceStat

if TYPE_CHECKING:  # pragma: no cover - annotation-only
    import asyncpg

    from llm_agents.kg.static_rules.base import StaticRule
    from llm_agents.kg.static_rules.writer import StaticRulesWriter

logger = logging.getLogger(__name__)


class RuleFreshnessGate:
    """One rule's corpus-vs-DB freshness check + inline re-sync. Cheap
    when fresh (one stat walk + one fingerprint fetch); the sync only
    runs on the diff."""

    def __init__(self, rule: "StaticRule") -> None:
        self._rule = rule

    async def sync_if_stale(self, conn: "asyncpg.Connection",
                            writer: "StaticRulesWriter",
                            ) -> Optional[StaticRulesReport]:
        """Compare the live corpus against ``llm.kg_rule_syncs``; stale
        → run the rule's sync and return its report, fresh → None."""
        live = self._rule.stat_corpus()
        stored = await self._stored_fingerprint(conn)
        if not self._stale(live, stored):
            return None
        logger.info("kg static rule %s: corpus moved since the last sync "
                    "(live %d file(s), max mtime %.0f vs stored %s) — "
                    "syncing before read",
                    self._rule.name, live.file_count, live.max_mtime,
                    "absent" if stored is None else
                    f"{stored['file_count']} file(s)")
        return await self._rule.run(conn, writer)

    async def _stored_fingerprint(
            self, conn: "asyncpg.Connection",
    ) -> Optional["asyncpg.Record"]:
        from _common.sql_views import render_sql_view

        view = render_sql_view("llm_agents/kg/static_rules/rule_fingerprint",
                               rule_name=self._rule.name)
        return await conn.fetchrow(view.sql, *view.params)

    @staticmethod
    def _stale(live: SourceStat, stored: Optional["asyncpg.Record"]) -> bool:
        if stored is None:
            return True  # never synced — bootstrap the fingerprint row
        if int(stored["file_count"]) != live.file_count:
            return True  # templates added/deleted — the delete/insert diff
        stored_mtime = stored["max_mtime"]
        live_mtime = datetime.fromtimestamp(live.max_mtime, tz=timezone.utc)
        return live_mtime > stored_mtime  # a file edited after the sync
