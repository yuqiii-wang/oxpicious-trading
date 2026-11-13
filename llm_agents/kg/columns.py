"""llm_agents.kg.columns — the TABLE-ENTITY column auto-load.

Every table in this repo documents its columns with rich
``COMMENT ON COLUMN`` prose (semantics, units, NULL behavior, build
parameters) — exactly what the LLM needs after a kg_search / kg_expand
names a table, and exactly what it would otherwise fetch one
``db_describe_table`` round trip at a time. :class:`TableColumnCatalog`
is the KG service's auto-load layer: given the schema-qualified names
of the tables a retrieval surfaced, it returns a compact per-table
column block (``col type — comment`` lines, longest prose truncated)
built from ONE pg_catalog query and cached per table with the same TTL
the graphs use.

Each block also carries the table's KEY GRAIN — the PRIMARY KEY and its
secondary indexes (``table_keys.sql``; the 2026-11-09 real-run round:
the model queried whole tables because the hints never said what keys
them) — so the model filters on the key columns directly.

Fail-soft by contract: the catalog is an ENRICHMENT layer — an
introspection error degrades to no blocks (the caller logs and renders
without columns), never breaks a search; a keys-read error degrades to
a columns-only block the same way.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, Sequence, Tuple

logger = logging.getLogger(__name__)

# The per-table render budget (a kg_search result is a bounded text
# block, not a schema dump — the tail names the escape hatch).
COLUMN_RENDER_MAX = 32     # columns listed per table before the tail line
COLUMN_COMMENT_MAX = 140   # per-column comment chars (…-truncated)
KEY_RENDER_MAX = 3         # pk + first 2 indexes; tail names the rest
# The lean tier's per-table budget (the gradual-load ladder, attempt 1 —
# the full tier passes COLUMN_RENDER_MAX; the cached lines are shared).
LEAN_COLUMNS_PER_TABLE = 5
# The default TTL — consistent with the graph cache (KG_CACHE_TTL_SEC):
# a DDL apply shows up on the next refresh without a daemon restart.
COLUMN_CACHE_TTL_SEC = 600.0

_WS_RE = re.compile(r"\s+")


class TableColumnCatalog:
    """The TTL cache over pg_catalog column metadata for the tables the
    KG tools retrieve (share the ``TABLE_COLUMNS`` singleton; a fresh
    instance only pays re-reading pg_catalog)."""

    def __init__(self, *,
                 cache_ttl_sec: float = COLUMN_CACHE_TTL_SEC) -> None:
        self._cache_ttl_sec = cache_ttl_sec
        # qualified name -> (loaded_at_monotonic, column_lines, key_lines)
        # — the STRUCTURED lines, cached once and rendered per request
        # (the lean/full tiers share one pg_catalog fetch; empty lists =
        # a cached negative, no columns found).
        self._blocks: Dict[str, Tuple[float, list[str], list[str]]] = {}

    async def blocks(self, names: Sequence[str], *,
                     max_columns: int = COLUMN_RENDER_MAX) -> Dict[str, str]:
        """Rendered column blocks for ``names`` (schema-qualified), the
        input order preserved, every name present in the result (an
        unknown/dropped table maps to no entry — cached negative).
        *max_columns* — the per-table render budget (the gradual-load
        tier's head; the full tier passes the default)."""
        wanted: list[str] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            wanted.append(name)
        now = time.monotonic()
        structured: Dict[str, Tuple[list[str], list[str]]] = {}
        missing: list[str] = []
        for name in wanted:
            cached = self._blocks.get(name)
            if cached is not None and now - cached[0] < self._cache_ttl_sec:
                structured[name] = (cached[1], cached[2])
            else:
                missing.append(name)
        if missing:
            fetched = await self._fetch_blocks(missing)
            for name in missing:
                lines, keys = fetched.get(name, ([], []))
                self._blocks[name] = (now, lines, keys)
                structured[name] = (lines, keys)
        budget = max(1, int(max_columns))
        return {name: self._format_block(
                    name, lines, keys, max_columns=budget)
                for name, (lines, keys) in structured.items()
                if lines}

    async def _fetch_blocks(
            self, names: Sequence[str]
    ) -> Dict[str, Tuple[list[str], list[str]]]:
        """One batched pg_catalog read for all uncached names — per
        table the STRUCTURED column lines (``pg_attribute`` definition
        order) + the PK/index grain lines (``table_keys.sql`` —
        fail-soft to columns-only). Rendering happens per request in
        :meth:`blocks` so the tiers share this one fetch."""
        # Lazy import — the activation-order rule the llm_agents modules
        # follow for _common.
        from _common.sql_views import render_sql_view

        view = render_sql_view(
            "llm_agents/kg/columns/table_columns", tables=list(names))
        from _common.db_commons import get_db_connection_async
        conn = await get_db_connection_async()
        try:
            rows = await conn.fetch(view.sql, *view.params)
            keys = await self._fetch_keys(conn, names)
        finally:
            await conn.close()
        by_table: Dict[str, list[str]] = {}
        for row in rows:
            line = self._column_line(row)
            if line:
                by_table.setdefault(str(row["table_name"]), []).append(line)
        return {name: (lines, keys.get(name) or [])
                for name, lines in by_table.items()}

    async def _fetch_keys(self, conn, names: Sequence[str]
                          ) -> Dict[str, list[str]]:
        """Per-table PK/index render lines (empty dict on ANY error — the
        grain is enrichment, never a search breaker)."""
        try:
            from _common.sql_views import render_sql_view

            view = render_sql_view(
                "llm_agents/kg/columns/table_keys", tables=list(names))
            rows = await conn.fetch(view.sql, *view.params)
        except Exception as exc:  # noqa: BLE001 — fail-soft enrichment
            logger.warning("kg key/index introspection failed: %s", exc)
            return {}
        by_table: Dict[str, list[str]] = {}
        for row in rows:
            kind = str(row["kind"])
            defn = _WS_RE.sub(" ", str(row["key_def"] or "")).strip()
            if not defn:
                continue
            prefix = "pk " if kind == "pk" else "index "
            by_table.setdefault(str(row["table_name"]), []).append(
                f"{prefix}{defn}")
        return by_table

    @staticmethod
    def _column_line(row) -> str | None:
        """One ``col type — comment`` render line (None = skip: dropped
        system attributes are already filtered; this guards empties)."""
        name = str(row["column_name"])
        line = f"{name} {row['data_type']}"
        comment = row["column_comment"]
        if comment:
            comment = _WS_RE.sub(" ", str(comment)).strip()
            if len(comment) > COLUMN_COMMENT_MAX:
                comment = comment[:COLUMN_COMMENT_MAX - 1] + "…"
            line += f" — {comment}"
        return line

    @staticmethod
    def _format_block(name: str, lines: list[str], key_lines: list[str],
                      *, max_columns: int = COLUMN_RENDER_MAX) -> str:
        """The table's block at *max_columns*: a count header, the
        capped column lines, the escape-hatch tail when the cap
        truncated the list, then the PK/index grain (capped at
        KEY_RENDER_MAX items)."""
        total = len(lines)
        if total == 0:
            return ""
        shown = lines[:max(1, int(max_columns))]
        head = f"columns ({len(shown)} of {total}):"
        body = [f"  {line}" for line in shown]
        tail: list[str] = []
        if total > len(shown):
            tail = [f"  … +{total - len(shown)} more column(s) — "
                    "db_describe_table for the rest"]
        if key_lines:
            tail += ["key (filter on the key columns):"]
            tail += [f"  {line}" for line in key_lines[:KEY_RENDER_MAX]]
            if len(key_lines) > KEY_RENDER_MAX:
                tail.append(f"  … +{len(key_lines) - KEY_RENDER_MAX} "
                            "more index(es) — db_describe_table for the rest")
        return "\n".join([head, *body, *tail])


# The process-wide catalog (the service holds it; a fresh instance only
# pays re-reading pg_catalog).
TABLE_COLUMNS = TableColumnCatalog()
