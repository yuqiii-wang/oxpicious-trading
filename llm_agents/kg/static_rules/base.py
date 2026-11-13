"""llm_agents.kg.static_rules.base — the contract every static rule
implements. A static rule is the PYTHON-RULE counterpart of a seed SQL
file: it owns one corpus (scanned by
:mod:`llm_agents.kg.static_rules.sources`), parses that corpus into
KG facts by code (never hand-authored INSERT SQL), and syncs the
derived entity/edge rows into ``llm.kg_entities`` / ``llm.kg_edges``
through the :class:`~llm_agents.kg.static_rules.writer.StaticRulesWriter`
— idempotently, inside one transaction, touching only the rule's own
rows (its namespace, its entity names, its relation)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from llm_agents.kg.static_rules.data_definitions import StaticRulesReport
from llm_agents.kg.static_rules.sources import SourceStat

if TYPE_CHECKING:  # pragma: no cover - annotation-only
    import asyncpg

    from llm_agents.kg.static_rules.writer import StaticRulesWriter


class StaticRule(ABC):
    """One Python static rule over one corpus. ``name`` identifies the
    rule in reports; ``namespace`` pins which KG subgraph it writes
    (edges never span namespaces — the DDL contract)."""

    name: ClassVar[str]
    namespace: ClassVar[int]

    @abstractmethod
    async def run(self, conn: "asyncpg.Connection",
                  writer: "StaticRulesWriter", *, force: bool = False,
                  ) -> StaticRulesReport:
        """Scan → parse → draft → sync. Returns the run's report.
        ``force`` is the standing ``--force`` convention — purge the
        rule's own rows first, then rebuild (a fully derived rule's
        sync already converges to the same state; the flag makes the
        purge explicit)."""

    def stat_corpus(self) -> SourceStat:
        """The corpus's bare fingerprint (file count + MAX mtime, stat
        only) — the freshness gate's cheap check. Corpus-less rules
        (none today) raise; the gate only consults corpus rules."""
        raise NotImplementedError(f"{type(self).__name__} has no corpus")

    @property
    def corpus_root(self) -> str:
        """The scanned directory, repo-relative posix — the fingerprint
        row's record of WHERE the rows came from."""
        raise NotImplementedError(f"{type(self).__name__} has no corpus")
