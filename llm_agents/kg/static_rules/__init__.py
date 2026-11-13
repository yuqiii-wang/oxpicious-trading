"""llm_agents.kg.static_rules — the PYTHON-RULE half of the knowledge
graphs: the corpora a rule derives from local directories (instead of
hand-authored seed INSERT SQL), parsed in code and synced into
``llm.kg_entities`` / ``llm.kg_edges`` by
:class:`~llm_agents.kg.static_rules.writer.StaticRulesWriter`.

  sources.py           — the COMMON dir scan-and-read module every rule
                         reuses (one corpus dir → typed SourceFile
                         records: loader name, text, leading comment,
                         mtime; plus the stat-only fingerprint scan the
                         freshness gate runs per graph reload);
  base.py              — the StaticRule contract (one rule = one corpus
                         + its parse rules + its sync; namespace-pinned);
  sql_view_semantics.py — the ns0 DB-semantics rule: every
                         ``database/sql_views`` template becomes a
                         ``view`` entity linked to the base relations it
                         reads by ``reads_from`` edges (references
                         resolved against pg_class; descriptions
                         harvested from the templates' own headers);
  db_semantics_tables.py — the ns0 authored-tables rule: every
                         ``database/sql/`` table file (ONE TABLE = ONE
                         FILE; the entity name is the file's own
                         qualified CREATE TABLE) whose leading banner
                         carries ``-- description:`` / ``-- tags:``
                         seeds one ``table`` entity with its
                         hand-written description + bilingual tags —
                         the migration target of the seed files' table
                         INSERTs (ledger-owned; a payload deleted
                         prunes the entity and every edge touching it);
  freshness.py         — the READ-TIME freshness gate: corpus stat
                         scan vs the ``llm.kg_rule_syncs`` fingerprint
                         row; on a diff (files newer, count changed, no
                         row) the rule syncs INLINE before the graph
                         read (fail-soft to the stored state);
  writer.py            — the write boundary (one transaction, all SQL
                         from ``database/sql_writes/`` action files,
                         the corpus fingerprint stamped last);
  data_definitions.py  — the draft + report pydantic payloads.

The KG service consults ``NAMESPACE_RULES`` on every graph reload, so
a corpus edit is picked up by the next kg_search after the graph TTL
expires — without a daemon restart or a manual runner run. The offline
runner stays for explicit syncs:

    python -m llm_agents.kg.static_rules [--force]

Both paths are idempotent and prune stale rule-owned rows."""
from typing import Dict, Tuple

from llm_agents.kg.static_rules.base import StaticRule
from llm_agents.kg.static_rules.data_definitions import (
    CorpusFingerprint, EdgeDraft, StaticRulesReport, StaticRulesWriteCounts,
    TableEntityDraft, ViewEntityDraft,
)
from llm_agents.kg.static_rules.db_semantics_tables import TableSemanticsRule
from llm_agents.kg.static_rules.freshness import RuleFreshnessGate
from llm_agents.kg.static_rules.sql_view_semantics import ViewSemanticsRule
from llm_agents.kg.static_rules.writer import StaticRulesWriter

# The registry the runner syncs, in order — extend with new rules here.
RULES: tuple[StaticRule, ...] = (ViewSemanticsRule(), TableSemanticsRule())

# The per-namespace read-time freshness guards — the KG service's graph
# loader consults this before reading a namespace (static_rules is the
# WRITER side; the read side only ever calls the gate).
NAMESPACE_RULES: Dict[int, Tuple[StaticRule, ...]] = {}
for _rule in RULES:
    NAMESPACE_RULES[_rule.namespace] = (
        *NAMESPACE_RULES.get(_rule.namespace, ()), _rule)

__all__ = [
    "CorpusFingerprint", "EdgeDraft", "NAMESPACE_RULES", "RULES",
    "RuleFreshnessGate", "StaticRule", "StaticRulesReport",
    "StaticRulesWriteCounts", "StaticRulesWriter", "TableEntityDraft",
    "TableSemanticsRule", "ViewEntityDraft", "ViewSemanticsRule",
]

