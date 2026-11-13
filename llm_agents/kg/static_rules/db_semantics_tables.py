"""llm_agents.kg.static_rules.db_semantics_tables — the namespace-0
DB-SEMANTICS rule over the ``database/sql/`` DDL tree: the AUTHORED
table entities (ONE TABLE = ONE FILE, filename = the table name; the
entity name comes from the file's own ``CREATE TABLE schema.table``
statement) as ``table`` entities with the hand-written business
description and bilingual trader tags carried in the file's LEADING
``--`` banner, upserted as OWNED rows (the rule's ledger records the
names; the hand seeds no longer carry them).

    -- kg entity (ns0 db_semantics_tables reads the payload below):
    -- description: Each index's daily open / high / low / close, ...
    -- tags: index, 指数, ohlcv, K线, trading amount, 成交额, ...

This is where the table payloads moved to (2026-11-10, from the former
``database/kg_tables`` parallel corpus): the DDL file that DECLARES a
table is also where its KG semantics live, so there is one home per
table. Adding a table to the KG = adding the two payload lines to its
own DDL file's banner; the freshness gate picks it up on the next graph
reload.

The parse rules: a scanned file with no ``CREATE TABLE`` is not a table
file (seeds, views, master lists, utils — ignored); a file declaring
SEVERAL tables breaks the one-table-one-file invariant and is reported
as a violation (never seeded); each entity name must resolve against
the database's own relation inventory (a header for a dropped relation
is skipped and counted, never seeded blind); a file WITHOUT a
``description:`` line is an unauthored table — not an entity and not
owned (the sql_views rule's mechanical fallback covers those by
reference). The sync prunes ledger-listed entities whose payload left
the corpus — together with every edge touching them — inside the one
sync transaction."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Set, Tuple

from pydantic import BaseModel

from llm_agents.kg.static_rules.base import StaticRule
from llm_agents.kg.static_rules.data_definitions import (
    CorpusFingerprint, StaticRulesReport, StaticRulesWriteCounts,
    TableEntityDraft,
)
from llm_agents.kg.static_rules.sources import (
    SqlDirSource, SqlSourceScan, SourceFile, SourceStat,
)

if TYPE_CHECKING:  # pragma: no cover - annotation-only
    import asyncpg

CORPUS_DIR_NAME = "database/sql"

# The one-liner contract's own bounds (the incremental verifier checks
# < 400-char descriptions); the authored header text must migrate
# VERBATIM — the caps only guard pathological lines.
_DESC_MAX = 380
_TAG_CAP = 40

# The DDL file's payload convention: `-- description: …` and
# `-- tags: a, b, …` lines in the leading comment block.
_DESCRIPTION_PREFIX = "description:"
_TAGS_PREFIX = "tags:"

# The entity name comes from the file's own DDL: the (single) qualified
# CREATE TABLE statement. TEMP tables never match (CREATE TEMP TABLE);
# the partition utils' format-string `%I` placeholders don't either.
_TABLE_RE = re.compile(
    r"^\s*CREATE\s+(?:UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"([a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*)\b",
    re.IGNORECASE,
)


class TableEntityFacts(BaseModel):
    """One DDL file's parsed entity payload — the authored row."""

    name: str
    description: str
    tags: Tuple[str, ...]


class TableSemanticsRule(StaticRule):
    """ns0 db semantics over the DDL tree: authored table entities from
    the tables' own file headers, ledger-owned, pruned on corpus diff."""

    name = "db_semantics_tables"
    namespace = 0

    def __init__(self, source: SqlDirSource | None = None) -> None:
        self._source = source or SqlDirSource()

    # -- the run --------------------------------------------------------

    async def run(self, conn: "asyncpg.Connection",
                  writer: "StaticRulesWriter", *, force: bool = False,
                  ) -> StaticRulesReport:
        scan = self._collect()
        relations = await self._load_relations(conn)
        rows, unresolved, violations = self._parse(scan, relations)
        counts: StaticRulesWriteCounts = await writer.apply_tables(
            conn,
            table_rows=[TableEntityDraft(name=row.name,
                                         description=row.description,
                                         tags=row.tags)
                        for row in rows],
            owned_names=[row.name for row in rows],
            fingerprint=CorpusFingerprint(
                rule_name=self.name,
                namespace=self.namespace,
                corpus_root=self.corpus_root,
                file_count=scan.file_count,
                max_mtime=datetime.fromtimestamp(
                    scan.max_mtime, tz=timezone.utc),
            ),
            force=force,
        )
        unresolved_names = sorted(unresolved)
        return StaticRulesReport(
            rule=self.name,
            namespace=self.namespace,
            scanned=len(scan),
            corpus_files=scan.file_count,
            tables_upserted=counts.tables_upserted,
            entities_pruned=counts.entities_pruned,
            unresolved_refs=len(unresolved_names),
            unresolved_ref_samples=tuple(unresolved_names[:5]),
            refactor_violations=tuple(sorted(violations)[:5]),
            force=force,
        )

    # -- corpus ---------------------------------------------------------

    def _corpus_dir(self) -> Path:
        # Lazy import — the activation-order rule for _common; the repo
        # root comes from the loader module's own resolution.
        from _common.sql_views import SQL_VIEWS_DIR

        return SQL_VIEWS_DIR.parent / "sql"

    @property
    def corpus_root(self) -> str:
        """The corpus dir, repo-relative posix — the fingerprint row's
        provenance record."""
        return CORPUS_DIR_NAME

    def stat_corpus(self) -> SourceStat:
        """The corpus's bare fingerprint — the freshness gate's cheap
        stat-only check (no content read on the graph-reload path)."""
        return self._source.stats(self._corpus_dir())

    def _collect(self) -> SqlSourceScan:
        return self._source.scan(self._corpus_dir())

    # -- DB facts -------------------------------------------------------

    async def _load_relations(self, conn: "asyncpg.Connection",
                              ) -> Dict[str, str]:
        """The database's own relation inventory (name → COMMENT prose)
        — the resolve set: an authored header whose table no longer
        exists is skipped and counted, never seeded blind."""
        from _common.sql_views import render_sql_view

        relations_view = render_sql_view(
            "llm_agents/kg/static_rules/db_relations")
        relation_rows = await conn.fetch(relations_view.sql,
                                         *relations_view.params)
        return {
            str(row["relation_name"]): str(row["relation_comment"] or "")
            for row in relation_rows}

    # -- the parse rules ------------------------------------------------

    def _parse(self, scan: SqlSourceScan, relations: Dict[str, str],
               ) -> Tuple[List[TableEntityFacts], Set[str], Set[str]]:
        """Each scanned file → its authored entity row, if it is one.
        The entity name is the file's own qualified ``CREATE TABLE``
        name (a file with none is not a table file; a file with several
        breaks the one-table-one-file invariant and is reported). The
        payload is the file's leading ``description:`` / ``tags:`` lines
        — a file without a ``description:`` line is an UNAUTHORED table
        (no entity, not owned; the mechanical fallback's business). A
        name that does not resolve to a live relation (dropped/renamed
        table) is skipped and counted for the report."""
        rows: List[TableEntityFacts] = []
        unresolved: Set[str] = set()
        violations: Set[str] = set()
        for file in scan.files:
            names = self._table_names(file)
            if not names:
                continue  # seeds, views, master lists, utils
            if len(names) > 1:
                violations.add(file.name)
                continue
            name = names[0]
            if name not in relations:
                unresolved.add(name)
                continue
            description, tags = self._payload(file)
            if not description:
                continue  # unauthored table — never seeded, never owned
            rows.append(TableEntityFacts(
                name=name,
                description=description,
                tags=tags,
            ))
        return rows, unresolved, violations

    @staticmethod
    def _table_names(file: SourceFile) -> List[str]:
        """The qualified table names the file's own ``CREATE TABLE``
        statements declare — the entity-name source (deduped, order
        kept)."""
        names: List[str] = []
        for line in file.text.splitlines():
            match = _TABLE_RE.match(line)
            if match:
                name = match.group(1).lower()
                if name not in names:
                    names.append(name)
        return names

    @staticmethod
    def _payload(file: SourceFile) -> Tuple[str, Tuple[str, ...]]:
        """The DDL header's authored payload: the ``description:`` line
        (capped at the one-liner contract) and the ``tags:`` list
        (comma-split — the vocabulary contract keeps commas out of
        tags). Missing lines seed empty (an empty description also means
        the table is not an authored KG entity at all)."""
        description = ""
        tags: List[str] = []
        for line in file.leading_comment.splitlines():
            line = line.strip()
            if line.lower().startswith(_DESCRIPTION_PREFIX):
                description = line[len(_DESCRIPTION_PREFIX):].strip()
            elif line.lower().startswith(_TAGS_PREFIX):
                tags = [part.strip()
                        for part in line[len(_TAGS_PREFIX):].split(",")
                        if part.strip()]
        return (description[:_DESC_MAX], tuple(tags[:_TAG_CAP]))
