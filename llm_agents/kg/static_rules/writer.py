"""llm_agents.kg.static_rules.writer — the KG WRITE BOUNDARY of the
static rules. Every statement is an action file under
``database/sql_writes/llm_agents/kg/static_rules/`` (the repo rule:
SQL lives in files, logic keeps arg values); this class owns the
transaction and the row mapping only — drafts in, counts out.

The sync order inside ONE transaction:

  1. ``delete_stale_views``   — prune the rule-owned ns0 view entities
     (names containing ``/``) whose template left the corpus; ``--force``
     passes an empty keep-list (full purge + rebuild).
  2. ``upsert_sql_view``      — the corpus's view entities, idempotent
     on ``uq_kg_entities_ns_type_name`` (ids stable — edges reference
     them).
  3. ``insert_missing_table`` — mechanical entities for referenced
     relations no hand seed covers (ON CONFLICT DO NOTHING).
  4. ``delete_reads_from``    — the derived edge set is replaced
     wholesale (only ``reads_from`` — hand-seeded edges untouched).
  5. ``insert_reads_from``    — the fresh pairs, members resolved BY
     NAME, NOT EXISTS-guarded like every seed apply.
  6. ``kg_rule_syncs/upsert`` — the corpus fingerprint (file count +
     MAX mtime) lands in the SAME transaction: a crash before the
     commit leaves no stamp, and the read-time freshness gate
     (``freshness.RuleFreshnessGate``) re-syncs on the next load —
     the safe direction.

The second flow, :meth:`apply_tables`, serves the ``database/sql`` DDL
header rule: prune ledger-owned entities absent from the corpus (with
every edge touching them) → upsert the authored rows → replace the
rule's ownership ledger → stamp. The two flows are explicit and
separate — a rule never runs the other's steps.

Volumes are small (a corpus ≈ hundreds of rows, one commit chunk), so
plain ``executemany`` is the right rung — no COPY/chunk machinery."""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Sequence

from llm_agents.kg.static_rules.data_definitions import (
    CorpusFingerprint, EdgeDraft, StaticRulesWriteCounts, TableEntityDraft,
    ViewEntityDraft,
)

if TYPE_CHECKING:  # pragma: no cover - annotation-only
    import asyncpg

# The sql_writes action prefix — this file's own package path (the
# layout convention: the path names the executing code).
_ACTION_PREFIX = "llm_agents/kg/static_rules"


class StaticRulesWriter:
    """Applies one rule's drafts to ``llm.kg_entities`` /
    ``llm.kg_edges`` in a single transaction. Stateless — share one
    instance across the registry's rules."""

    async def apply(
            self, conn: "asyncpg.Connection", *,
            view_rows: Sequence[ViewEntityDraft],
            missing_table_rows: Sequence[TableEntityDraft],
            edge_pairs: Sequence[EdgeDraft],
            keep_names: Sequence[str],
            fingerprint: CorpusFingerprint, force: bool = False,
    ) -> StaticRulesWriteCounts:
        from _common.sql_writes import load_sql_write, render_sql_write

        # Lazy import — the activation-order rule for _common.
        kept: List[str] = [] if force else list(keep_names)
        async with conn.transaction():
            stale_sql = render_sql_write(
                f"{_ACTION_PREFIX}/kg_entities/delete_stale_views", kept)
            pruned = self._deleted_count(await conn.execute(
                stale_sql.sql, *stale_sql.params))
            if view_rows:
                await conn.executemany(
                    load_sql_write(
                        f"{_ACTION_PREFIX}/kg_entities/upsert_sql_view"),
                    [(row.name, row.description, list(row.tags))
                     for row in view_rows])
            if missing_table_rows:
                await conn.executemany(
                    load_sql_write(
                        f"{_ACTION_PREFIX}/kg_entities/insert_missing_table"),
                    [(row.name, row.description)
                     for row in missing_table_rows])
            edges_sql = load_sql_write(
                f"{_ACTION_PREFIX}/kg_edges/delete_reads_from")
            edges_deleted = self._deleted_count(
                await conn.execute(edges_sql))
            if edge_pairs:
                await conn.executemany(
                    load_sql_write(
                        f"{_ACTION_PREFIX}/kg_edges/insert_reads_from"),
                    [(pair.subject, pair.target) for pair in edge_pairs])
            await self._stamp(conn, fingerprint)
        return StaticRulesWriteCounts(
            views_upserted=len(view_rows),
            tables_created=len(missing_table_rows),
            entities_pruned=pruned,
            edges_deleted=edges_deleted,
        )

    async def apply_tables(
            self, conn: "asyncpg.Connection", *,
            table_rows: Sequence[TableEntityDraft],
            owned_names: Sequence[str],
            fingerprint: CorpusFingerprint, force: bool = False,
    ) -> StaticRulesWriteCounts:
        """The DDL-header corpus flow (``db_semantics_tables``): prune
        ledger-owned entities whose payload left the corpus (edges touched
        by them die in the same statement), upsert the authored rows
        (the rule owns them — description AND tags refresh
        unconditionally), replace the ownership ledger, stamp."""
        from _common.sql_writes import load_sql_write, render_sql_write

        # Lazy import — the activation-order rule for _common.
        kept: List[str] = [] if force else list(owned_names)
        async with conn.transaction():
            stale_edge_sql = render_sql_write(
                f"{_ACTION_PREFIX}/kg_edges/delete_stale_rule_edges",
                fingerprint.rule_name, kept)
            edges_deleted = self._deleted_count(await conn.execute(
                stale_edge_sql.sql, *stale_edge_sql.params))
            stale_sql = render_sql_write(
                f"{_ACTION_PREFIX}/kg_entities/delete_stale_rule_tables",
                fingerprint.rule_name, kept)
            pruned = self._deleted_count(await conn.execute(
                stale_sql.sql, *stale_sql.params))
            if table_rows:
                await conn.executemany(
                    load_sql_write(
                        f"{_ACTION_PREFIX}/kg_entities/upsert_table"),
                    [(row.name, row.description, list(row.tags))
                     for row in table_rows])
            await conn.execute(load_sql_write(
                f"{_ACTION_PREFIX}/kg_rule_entities/delete_for_rule"),
                fingerprint.rule_name)
            await conn.executemany(
                load_sql_write(
                    f"{_ACTION_PREFIX}/kg_rule_entities/insert_row"),
                [(fingerprint.rule_name, name) for name in kept])
            await self._stamp(conn, fingerprint)
        return StaticRulesWriteCounts(
            tables_upserted=len(table_rows),
            entities_pruned=pruned,
            edges_deleted=edges_deleted,
        )

    async def _stamp(self, conn: "asyncpg.Connection",
                     fingerprint: CorpusFingerprint) -> None:
        """The corpus fingerprint (last statement of the sync
        transaction — crash before commit ⇒ no stamp ⇒ the gate
        re-syncs on the next load)."""
        from _common.sql_writes import render_sql_write

        stamp_sql = render_sql_write(
            f"{_ACTION_PREFIX}/kg_rule_syncs/upsert",
            fingerprint.rule_name,
            fingerprint.namespace,
            fingerprint.corpus_root,
            fingerprint.file_count,
            fingerprint.max_mtime,
        )
        await conn.execute(stamp_sql.sql, *stamp_sql.params)

    @staticmethod
    def _deleted_count(status: str) -> int:
        """asyncpg's command status (``'DELETE 4'``) → the row count the
        DELETE actually removed — the honest prune/report number."""
        kind, _, count = status.partition(" ")
        try:
            return int(count) if kind == "DELETE" else 0
        except ValueError:
            return 0
