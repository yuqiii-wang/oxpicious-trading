"""llm_agents.deep_agents.core.authorization.gate — the SQL SCOPE GATE
(2026-11-13): every agent SQL lane's pre-execution check against the
resolved :class:`TablePermission`.

Enforcement seams (all harness-side, before the shared MCP daemon or
the stats readers see anything):

- ``db_run_query``    — :meth:`SqlScopeGate.check_sql` over the SQL text
- ``db_describe_table`` — :meth:`SqlScopeGate.check_table`
- ``db_list_tables``  — :meth:`SqlScopeGate.check_schema` (a denied
  schema lists nothing)
- ``text_search``     — :meth:`SqlScopeGate.check_text_table` over the
  mirrored registry below
- the stats study tools — :meth:`SqlScopeGate.check_table` over the
  standing ``stats.v_*`` views

How SQL becomes referenced relations (fit for a cooperating model under
the read-only role — not an adversarial parser): strip comments, collect
CTE names (excluded), then resolve (a) ``schema.table`` token pairs and
(b) bare identifiers after FROM / JOIN / TABLE against a TTL-cached
pg_catalog relation map (the read replica). Alias.column pairs drop out
(the alias is not a schema); an ambiguous bare name is allowed only when
EVERY same-named relation is in scope. Degraded mode (map unavailable —
replica down): qualified pairs check directly against the permission
(the schema leg decides), bare names pass with one logged warning —
deny-by-default otherwise.

The py-sandbox lane is the documented gap this round: its container
connects as the shared readonly_analytics role (see the module
docstring); a per-run PG role is the follow-up hardening layer.

``DEEP_AGENT_AUTHZ=0`` is the kill-switch (the JEV_ROUTE env pattern).
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Dict, Optional, Set, Tuple

from llm_agents.deep_agents.core.authorization.models import TablePermission

logger = logging.getLogger("deep_agents.core.authorization")

# The agent-facing MCP tools the gate intercepts (everything else on the
# shared daemon — web_search and friends — touches no tables).
GATED_MCP_TOOLS = frozenset({
    "db_run_query", "db_describe_table", "db_list_tables", "text_search",
})

# The text_search registry MIRROR (llm_agents/mcp/readonly_server.py::
# _TEXT_SEARCH_TABLES) — key -> the schema-qualified table the search
# actually reads. Keep in sync with the server when it grows a table.
TEXT_SEARCH_TABLES: Dict[str, str] = {
    "news": "text.news",
    "news_comments": "text.news_comments",
    "news_digestions": "text.news_digestions",
    "llm_qa": "llm.llm_qa",
    "llm_qa_by_ask": "llm.llm_qa_by_ask",
    "llm_qa_by_research": "llm.llm_qa_by_research",
}

_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_QUALIFIED_RE = re.compile(
    r"\b([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)", re.IGNORECASE)
_BARE_RE = re.compile(
    r"\b(?:FROM|JOIN|TABLE)\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)
# CTE names: `WITH name AS (` and the `, name AS (` continuation legs.
_CTE_RE = re.compile(
    r"(?:\bwith\b|,)\s*([a-z_][a-z0-9_]*)\s+as\s*\(", re.IGNORECASE)

# The pg_catalog relation scan: every user schema's tables / partitioned
# tables / (materialized) views. relkind r/p/v/m — the same set the
# readonly server's list/describe lane accepts.
_RELATIONS_SQL = (
    "SELECT n.nspname, c.relname FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p', 'v', 'm') "
    "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND n.nspname NOT LIKE 'pg_%'"
)

_RELATION_TTL_SEC = 600.0

# The repo's user schemas — the degraded mode's first-leg filter: a
# ``a.b`` pair counts as a relation candidate only when ``a`` is a known
# schema (kills alias.column false rejects like ``t.ts`` when the map is
# unavailable).
_KNOWN_SCHEMAS = frozenset({
    "analysis", "analysis_signals", "analysis_composites",
    "analysis_insight_metrics", "strategy", "stats", "static", "live",
    "text", "llm", "monitor", "multi_media", "public", "langgraph",
})


def gate_enabled() -> bool:
    """The kill-switch: ``DEEP_AGENT_AUTHZ=0`` disables every gate check
    (emergency lane; the authorization rows still record)."""
    return os.environ.get("DEEP_AGENT_AUTHZ", "1") != "0"


class ScopeViolation(Exception):
    """One out-of-scope read, rejected before execution — the message is
    the tool-result text the model sees (never raised past the loop)."""

    def __init__(self, denied: Tuple[str, ...],
                 permission: TablePermission) -> None:
        self.denied = tuple(denied)
        listing = ", ".join(sorted(set(denied))[:8])
        schemas = ", ".join(permission.schemas) or "none"
        extra = ""
        if permission.tables:
            extra = (f" plus the {len(permission.tables)} table(s) this "
                     f"run ticked")
        super().__init__(
            f"[Scope] rejected — outside this node's authorized DB "
            f"scope: {listing}. Allowed schemas: {schemas}{extra}. "
            f"Remedy: kg_search the business meaning (propose 2-3 "
            f"synonyms, Chinese AND English trader terms) to find an "
            f"in-scope table; if the study genuinely needs the rejected "
            f"table, the PARENT must delegate a dedicated node with an "
            f"explicit study_focus naming that domain — a node cannot "
            f"widen its own scope."
        )


class _RelationMap:
    """The cached ``{qualified name}`` set + the bare-name resolution
    index — built from one pg_catalog scan, TTL-refreshed."""

    def __init__(self, qualified: Set[str],
                 by_bare: Dict[str, Set[str]]) -> None:
        self.qualified = qualified
        self.by_bare = by_bare


_CACHE: Optional[Tuple[float, _RelationMap]] = None


async def _relation_map() -> Optional[_RelationMap]:
    """The TTL-cached relation map; None when the catalog read fails
    (the gate's degraded mode — see the module docstring)."""
    global _CACHE
    now = time.monotonic()
    if _CACHE is not None and now - _CACHE[0] < _RELATION_TTL_SEC:
        return _CACHE[1]
    try:
        from _common.db_commons import get_read_db_connection_async

        conn = await get_read_db_connection_async()
        try:
            rows = await conn.fetch(_RELATIONS_SQL)
        finally:
            await conn.close()
    except Exception as exc:  # noqa: BLE001 — degraded mode, not a crash
        logger.warning("scope gate relation map unavailable: %s: %s",
                       type(exc).__name__, exc)
        return None
    qualified: Set[str] = set()
    by_bare: Dict[str, Set[str]] = {}
    for schema, name in rows:
        qualified.add(f"{schema}.{name}".lower())
        by_bare.setdefault(str(name).lower(), set()).add(
            f"{schema}.{name}".lower())
    _CACHE = (now, _RelationMap(qualified, by_bare))
    return _CACHE[1]


def extract_relations(sql: str,
                      relations: Optional[_RelationMap]) -> Set[str]:
    """The SQL text's referenced relations — pure, testable. CTE names
    are excluded; alias.column pairs drop out (the first leg is not a
    schema); bare FROM/JOIN/TABLE identifiers resolve through the map
    (unresolvable or ambiguous-unknown names pass — the map, not the
    guess, decides)."""
    text = _COMMENT_RE.sub(" ", sql or "")
    ctes = {m.group(1).lower() for m in _CTE_RE.finditer(text)}
    found: Set[str] = set()
    for match in _QUALIFIED_RE.finditer(text):
        schema = match.group(1).lower()
        qualified = f"{schema}.{match.group(2)}".lower()
        if relations is not None:
            if qualified in relations.qualified:
                found.add(qualified)
        elif schema in _KNOWN_SCHEMAS:
            # degraded mode: no map — a known-schema first leg decides
            found.add(qualified)
    if relations is not None:
        for match in _BARE_RE.finditer(text):
            bare = match.group(1).lower()
            if bare in ctes:
                continue
            found.update(relations.by_bare.get(bare, ()))
    return found


class SqlScopeGate:
    """The per-call gate — one instance, pure methods over a
    :class:`TablePermission` (inject your own relation map in tests via
    ``relation_map``)."""

    def __init__(self, *, relation_map: Optional[_RelationMap] = None,
                 ) -> None:
        self._override_map = relation_map

    async def _map(self) -> Optional[_RelationMap]:
        return self._override_map if self._override_map is not None \
            else await _relation_map()

    async def check_sql(self, sql: str,
                        permission: TablePermission) -> None:
        """Reject the statement when ANY referenced relation is outside
        the scope (ScopeViolation; the caller renders it as the tool
        result)."""
        if not gate_enabled():
            return
        relations = extract_relations(sql, await self._map())
        denied = tuple(r for r in sorted(relations)
                       if not permission.allows(r))
        if denied:
            raise ScopeViolation(denied, permission)

    def check_table(self, schema: str, table: str,
                    permission: TablePermission) -> None:
        """One named table (db_describe_table / the stats tools)."""
        if not gate_enabled():
            return
        qualified = f"{schema}.{table}".strip(".").lower()
        if not permission.allows(qualified):
            raise ScopeViolation((qualified,), permission)

    def check_schema(self, schema: str,
                     permission: TablePermission) -> None:
        """One whole schema (db_list_tables — a denied schema lists
        nothing, not even its table names)."""
        if not gate_enabled():
            return
        if str(schema).strip().lower() not in permission.schemas:
            raise ScopeViolation((f"{schema}.*",), permission)

    def check_text_table(self, key: str,
                         permission: TablePermission) -> None:
        """One text_search registry key — mirrored to the table the
        server actually reads (unknown keys pass: the server rejects
        them itself)."""
        if not gate_enabled():
            return
        table = TEXT_SEARCH_TABLES.get(str(key).strip().lower())
        if table is not None and not permission.allows(table):
            raise ScopeViolation((table,), permission)
