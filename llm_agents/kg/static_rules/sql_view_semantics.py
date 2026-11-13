"""llm_agents.kg.static_rules.sql_view_semantics — the namespace-0
DB-SEMANTICS rule over the ``database/sql_views/`` corpus: the read-side
SELECT templates become ns0 ``view`` entities (ONE ENTITY = ONE
template, name = its loader slash path) linked to the base relations
they read by ``reads_from`` edges — HEAD-FIRST (entity_ids[0] = the
template, the reader), ``sub_relation`` NULL (bare verb).

The edges are built by PYTHON RULES, not INSERT-seed SQL: each
template's text is scanned for schema-qualified references
(``schema.relation``), every reference is resolved against the
database's own relation inventory (pg_class — alias refs like
``t.date`` / ``b.code`` never carry a schema and drop out), and only
references that resolve to a LIVE relation become edges. A referenced
relation that has no ns0 entity yet is drafted as a MECHANICAL table
entity (description = the relation's own ``COMMENT ON TABLE``, empty
tags — a WALK target, never a lexical seed; ON CONFLICT DO NOTHING so
a hand-seeded row is never overridden).

The descriptions are GROUNDED, never invented: each view entity's
one-liner is harvested from the template's own leading ``--`` header
(the pipeline author's business sentence — the ns2 generator's
``harvest_prose`` principle); tags are mechanical findability
vocabulary — the referenced relations' bare names (searching
``index_basic_stats`` lands the templates that read it) plus the
template's own path segments."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Tuple

from pydantic import BaseModel

from llm_agents.kg.graph.vocabulary import READS_FROM
from llm_agents.kg.static_rules.base import StaticRule
from llm_agents.kg.static_rules.data_definitions import (
    CorpusFingerprint, EdgeDraft, StaticRulesReport, StaticRulesWriteCounts,
    TableEntityDraft, ViewEntityDraft,
)
from llm_agents.kg.static_rules.sources import (
    SqlDirSource, SqlSourceScan, SourceFile, SourceStat,
)

if TYPE_CHECKING:  # pragma: no cover - annotation-only
    import asyncpg

# The ns0 rule's corpus: the repo's read-side query templates. The dir
# constant comes from the loader module itself so one path change moves
# both (lazy import — the activation-order rule for _common).
SQL_VIEWS_DIR_NAME = "database/sql_views"

# The mechanical tags stay lean: every referenced relation's bare name
# plus every path segment usually lands well under this.
_TAG_CAP = 16
# A reference this short after the dot is an alias noise-guard anyway —
# the resolve step is the real gate; the cap only keeps pathological
# templates from bloating tags.
_DESC_MAX = 200
# The mechanical table entities inherit the relation's COMMENT prose,
# which some DDL headers write long — the one-liner contract (the
# incremental verifier's < 400 one-liner check) caps it here.
_TABLE_DESC_MAX = 200

# Schema-qualified references: schema names start lowercase and so do
# relation names in this repo's SQL. Aliases (t, b, sc, …) match the
# same shape and are dropped by the pg_class resolve, not here.
_QUALIFIED_REF_RE = re.compile(r"\b([a-z][a-z0-9_]*)\.([a-z_][a-z0-9_]*)\b")


class ViewTemplateFacts(BaseModel):
    """One template's parsed KG facts — its entity row draft inputs."""

    name: str
    description: str
    tags: Tuple[str, ...]
    table_refs: Tuple[str, ...]


def _cap(text: str, limit: int = _TABLE_DESC_MAX) -> str:
    """One cap for every description the rule emits — the KG's
    one-liner contract; an over-long comment prose keeps its head."""
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


class ViewSemanticsRule(StaticRule):
    """ns0 db semantics over the sql_views corpus: view entities +
    ``reads_from`` edges derived by parsing the templates."""

    name = "sql_view_semantics"
    namespace = 0

    def __init__(self, source: SqlDirSource | None = None) -> None:
        self._source = source or SqlDirSource()

    # -- the run --------------------------------------------------------

    async def run(self, conn: "asyncpg.Connection",
                  writer: "StaticRulesWriter", *, force: bool = False,
                  ) -> StaticRulesReport:
        scan = self._collect()
        relations, entity_names, tagged_names = await self._load_db(conn)
        facts, unresolved = self._parse(scan, relations)
        view_rows, missing_rows, edge_pairs, edges_resolved = self._draft(
            facts, relations, entity_names, tagged_names)
        counts: StaticRulesWriteCounts = await writer.apply(
            conn,
            view_rows=view_rows,
            missing_table_rows=missing_rows,
            edge_pairs=edge_pairs,
            keep_names=[item.name for item in facts],
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
        unresolved_refs = sorted(unresolved)
        return StaticRulesReport(
            rule=self.name,
            namespace=self.namespace,
            scanned=len(scan),
            corpus_files=scan.file_count,
            views_upserted=counts.views_upserted,
            tables_created=counts.tables_created,
            edges_expected=len(edge_pairs),
            edges_resolved=edges_resolved,
            edges_deleted=counts.edges_deleted,
            entities_pruned=counts.entities_pruned,
            unresolved_refs=len(unresolved_refs),
            unresolved_ref_samples=tuple(unresolved_refs[:5]),
            force=force,
        )

    # -- scan -----------------------------------------------------------

    def _corpus_dir(self):
        # Lazy import — the activation-order rule for _common.
        from _common.sql_views import SQL_VIEWS_DIR

        return SQL_VIEWS_DIR

    @property
    def corpus_root(self) -> str:
        """The corpus dir, repo-relative posix — the fingerprint row's
        provenance record (and the error messages')."""
        return SQL_VIEWS_DIR_NAME

    def stat_corpus(self) -> SourceStat:
        """The corpus's bare fingerprint — the freshness gate's cheap
        stat-only check (no content read on the graph-reload path)."""
        return self._source.stats(self._corpus_dir())

    def _collect(self) -> SqlSourceScan:
        return self._source.scan(self._corpus_dir())

    # -- DB facts -------------------------------------------------------

    async def _load_db(self, conn: "asyncpg.Connection",
                       ) -> Tuple[Dict[str, str], set[str], set[str]]:
        """The database's own relation inventory (name → COMMENT prose)
        and the ns0 entity names already seeded, split into all names
        (the edge-resolution universe) and the TAGGED subset (the hand
        seeds — the only rows the rule never re-drafts). Lazy-imported
        loaders, zero inline SQL."""
        from _common.sql_views import render_sql_view

        relations_view = render_sql_view(
            "llm_agents/kg/static_rules/db_relations")
        entities_view = render_sql_view(
            "llm_agents/kg/static_rules/ns0_entity_ids")
        relation_rows = await conn.fetch(relations_view.sql,
                                         *relations_view.params)
        entity_rows = await conn.fetch(entities_view.sql, *entities_view.params)
        relations = {
            str(row["relation_name"]): str(row["relation_comment"] or "")
            for row in relation_rows}
        entity_names = {str(row["name"]) for row in entity_rows}
        tagged_names = {str(row["name"]) for row in entity_rows
                        if row["tagged"]}
        return relations, entity_names, tagged_names

    # -- the parse rules ------------------------------------------------

    def _parse(self, scan: SqlSourceScan, relations: Dict[str, str],
               ) -> Tuple[List[ViewTemplateFacts], set[str]]:
        """Each template's text → its resolved schema-qualified
        references. Returns the per-file facts plus the DISTINCT
        unresolved references for the report — a reference that does
        not resolve produces no edge, never a guess. Unresolved means
        the SCHEMA is real but the relation is not (a typo, a dropped
        table — worth surfacing); refs whose schema is not a database
        schema at all are query ALIASES (``t.date``, ``b.code`` — the
        bulk of any template's qualified-shaped matches) and are
        ignored silently."""
        schemas = {name.rsplit(".", 1)[0] for name in relations}
        facts: List[ViewTemplateFacts] = []
        unresolved: set[str] = set()
        for file in scan.files:
            resolved: List[str] = []
            for schema, name in sorted(set(_QUALIFIED_REF_RE.findall(
                    file.text))):
                if schema.startswith("pg_") or schema == "information_schema":
                    continue  # system helpers, not repo data
                qualified = f"{schema}.{name}"
                if qualified in relations:
                    resolved.append(qualified)
                elif schema in schemas:
                    unresolved.add(qualified)
            facts.append(ViewTemplateFacts(
                name=file.name,
                description=self._harvest_description(file),
                tags=self._tags(file, resolved),
                table_refs=tuple(dict.fromkeys(resolved)),
            ))
        return facts, unresolved

    def _harvest_description(self, file: SourceFile) -> str:
        """The template's own one-line business meaning: the first
        content line of its leading ``--`` header, with the corpus's
        ``<stem> — …`` headline prefix stripped. Empty headers fall
        back to a factual identity line (never an invented semantic)."""
        for line in file.leading_comment.splitlines():
            line = line.strip()
            if not line:
                continue
            stem = file.stem
            if line.lower().startswith(stem.lower()):
                line = line[len(stem):].lstrip(" \t-—–:;")
            if line:
                return _cap(line, _DESC_MAX)
        return (f"Read-side SQL view template '{file.stem}' under "
                f"{SQL_VIEWS_DIR_NAME}/")

    @staticmethod
    def _tags(file: SourceFile, resolved: List[str]) -> Tuple[str, ...]:
        """The mechanical findability vocabulary: referenced relations'
        bare names first (they carry the business nouns), then the
        template's own path segments (its service's location words)."""
        tags: List[str] = []
        seen: set[str] = set()
        for ref in resolved:
            bare = ref.rsplit(".", 1)[-1]
            if bare.lower() not in seen:
                seen.add(bare.lower())
                tags.append(bare)
        for segment in file.path_segments:
            if segment.lower() not in seen:
                seen.add(segment.lower())
                tags.append(segment)
        return tuple(tags[:_TAG_CAP])

    # -- drafting -------------------------------------------------------

    @staticmethod
    def _draft(facts: List[ViewTemplateFacts], relations: Dict[str, str],
               entity_names: set[str], tagged_names: set[str],
               ) -> Tuple[List[ViewEntityDraft], List[TableEntityDraft],
                          List[EdgeDraft], int]:
        """The write-boundary drafts: one view-entity draft per parsed
        template, one mechanical table draft per referenced relation the
        hand seeds have NOT tagged (mechanical rows re-draft every run —
        the write action refreshes their description while still
        mechanical — so comment-prose fixes heal), and one
        ``reads_from`` pair per (template, resolved relation). The
        resolved count is computed HERE where both resolve sets live;
        the writer's executemany relies on the same by-name resolution."""
        view_rows = [ViewEntityDraft(name=item.name,
                                     description=item.description,
                                     tags=item.tags)
                     for item in facts]
        # Every table_ref is resolved by construction (the parse gate),
        # so the known set is the seeded names plus the drafts the
        # mechanical inserts are about to create.
        known_tables = set(entity_names) | {
            ref for item in facts for ref in item.table_refs}
        missing_tables = sorted(
            {ref for item in facts for ref in item.table_refs
             if ref not in tagged_names})
        missing_rows = [TableEntityDraft(name=ref,
                                         description=_cap(
                                             relations.get(ref, "")))
                        for ref in missing_tables]
        edge_pairs = [
            EdgeDraft(subject=item.name, target=ref)
            for item in sorted(facts, key=lambda item: item.name)
            for ref in item.table_refs
        ]
        edges_resolved = sum(
            1 for pair in edge_pairs
            if pair.target in known_tables)
        return view_rows, missing_rows, edge_pairs, edges_resolved
