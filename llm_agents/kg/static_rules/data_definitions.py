"""llm_agents.kg.static_rules.data_definitions — the typed payloads
crossing the static rules' module boundaries: one draft model per row
shape a rule hands the writer, and the report models the runner prints.
Pydantic per the repo convention — rows materialize at the write
boundary (``StaticRulesWriter.apply``), never mid-parse."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CorpusFingerprint(BaseModel):
    """The corpus state one rule's sync was derived from — stamped into
    ``llm.kg_rule_syncs`` in the sync's own transaction. The read-time
    freshness gate compares the live corpus against exactly this:
    a count diff or a newer ``max_mtime`` (or a missing row) means the
    KG rows are derived from an older corpus and it re-syncs before
    the read."""

    rule_name: str
    namespace: int
    corpus_root: str
    file_count: int
    max_mtime: datetime


class ViewEntityDraft(BaseModel):
    """One ns0 ``view`` entity a rule seeds — a corpus file's loader
    name, its harvested business meaning, its mechanical tags."""

    name: str
    description: str
    tags: tuple[str, ...] = ()


class TableEntityDraft(BaseModel):
    """One ns0 ``table`` entity a rule seeds. Two regimes share the
    draft: the sql_views rule's MECHANICAL rows (description from the
    relation's own ``COMMENT ON TABLE``, empty tags — refreshed only
    while still mechanical; hand-seeded rows stay authoritative) and
    the DDL-header rule's AUTHORED rows (hand-written description +
    bilingual tags, owned via the rule's ledger and refreshed
    unconditionally)."""

    name: str
    description: str = ""
    tags: tuple[str, ...] = ()


class EdgeDraft(BaseModel):
    """One head-first hyperedge pair a rule derives — resolved to
    entity ids BY NAME at the write boundary."""

    subject: str
    """The head member's entity name (``entity_ids[0]``)."""

    target: str
    """The tail member's entity name (``entity_ids[1]``)."""


class StaticRulesWriteCounts(BaseModel):
    """What one writer transaction did — the DB-side half of a report
    (the rule supplies its own parse-side numbers on top)."""

    views_upserted: int = 0
    tables_created: int = 0
    tables_upserted: int = 0
    entities_pruned: int = 0
    edges_deleted: int = 0


class StaticRulesReport(BaseModel):
    """One rule run's outcome — the runner prints ``render()``."""

    rule: str
    namespace: int
    scanned: int = 0
    corpus_files: int = 0
    views_upserted: int = 0
    tables_created: int = 0
    tables_upserted: int = 0
    edges_expected: int = 0
    edges_resolved: int = 0
    edges_deleted: int = 0
    entities_pruned: int = 0
    unresolved_refs: int = 0
    unresolved_ref_samples: tuple[str, ...] = ()
    refactor_violations: tuple[str, ...] = ()
    """Files breaking the one-table-one-file invariant — reported, never
    seeded (empty unless the DDL tree regressed)."""
    force: bool = False

    def render(self) -> str:
        lines = [
            f"[{self.rule}] namespace {self.namespace}"
            + (" (--force purge + rebuild)" if self.force else ""),
            f"  scanned {self.scanned} corpus file(s) "
            f"(fingerprint stamped: {self.corpus_files} file(s))",
        ]
        if self.views_upserted or self.tables_created:
            lines.append(
                f"  view entities upserted {self.views_upserted}; "
                f"mechanical table entity rows drafted "
                f"{self.tables_created} (refreshed in place; hand seeds "
                f"untouched)")
            lines.append(
                f"  reads_from edges derived {self.edges_expected} "
                f"(resolved {self.edges_resolved}; prior edges deleted "
                f"{self.edges_deleted})")
        if self.tables_upserted:
            lines.append(
                f"  authored table entities upserted "
                f"{self.tables_upserted} (ledger-owned; hand seeds "
                f"carry none of them)")
        lines.append(
            f"  stale entities pruned {self.entities_pruned} (with "
            f"every edge touching them)")
        if self.unresolved_refs:
            samples = ", ".join(self.unresolved_ref_samples)
            lines.append(f"  unresolved refs skipped: "
                         f"{self.unresolved_refs} distinct (e.g. {samples})")
        else:
            lines.append("  unresolved refs skipped: 0")
        if self.refactor_violations:
            lines.append(f"  ONE-TABLE-ONE-FILE violations: "
                         f"{', '.join(self.refactor_violations)}")
        return "\n".join(lines)
