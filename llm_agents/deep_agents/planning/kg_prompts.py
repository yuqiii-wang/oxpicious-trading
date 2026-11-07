"""llm_agents.deep_agents.planning.kg_prompts — the prompt loader's
KG-HOTPATH corroboration probe (2026-11-07).

The loading ladder's optional lane gains an evidence rung: a skill the
run's question does not name literally may still be KNOWN territory —
the PROMOTED ``llm.kg_hotpaths`` chains (the KG chain cache's
two-hit-verified replay layer: a (query, namespace) whose traversal
resulted edge-for-edge identical twice) record which domains past
questions resolved to. This probe consults them at plan time, before
the proposal, so the implicated optional-labeled rows LOAD (the
``"hotpath"`` verdict — :meth:`PromptLadder.load_set` treats it like a
bound-label force) instead of waiting for the planner's discretion:

  1. trigram-probe ``llm.kg_hotpaths`` for promoted queries SIMILAR to
     the run's needles (question / scope name / item labels — the same
     haystack pieces the ladder matches; short keyword needles against
     short promoted keyword queries),
  2. resolve each chain's edges through the TTL-cached live
     ``KnowledgeGraph``, skipping chains whose ``entity_hash`` no
     longer matches the namespace's ``entity_digest`` (the graph
     changed since promotion),
  3. ns1 chains (the prompt catalog — entity ``name`` IS the
     ``prompt_key`` per the kg-db-semantics contract) implicate their
     member catalog rows DIRECTLY; both namespaces' entity names + tags
     join the EVIDENCE TEXTS (ns0's bilingual trader vocabulary bridges
     the label↔question synonym gap — a row labeled "margin flows"
     matches a question asking 两融 through the margin tables' tags),
  4. ``PromptLadder.hotpath_rung`` matches the catalog's optional
     labels against the evidence texts; the ns1 direct implications
     merge on top; the final verdicts cap at ``prompt_hotpath_rows``
     (0 disables the lane) in catalog order.

READ-ONLY by design: the TTL-cached ``graph()`` loads plus the graph's
sync internals — never ``KnowledgeGraphService.search`` (that logs
chains, bumps hit_counts and promotes; the probe is evidence
CONSUMPTION, not a traversal). Fail-soft end to end: a DB outage, an
unseeded namespace, an empty catalog or no needles yield ZERO verdicts
and the ladder behaves exactly as before the lane existed. Bound-label
rows are untouched — their force-load runs on the question text in
``PromptLadder.match`` regardless of this lane.

Layering: planning may import ``llm_agents.kg`` (the independent KG
module); kg never imports back. The pure matching lives in
``planning.prompt_loading`` (stub-testable without a DB); this module
owns only the evidence gathering (duck-typed ``store`` / ``graphs``
collaborators — the stub tests inject fakes).
"""
from __future__ import annotations

import logging
from typing import Dict, List, Sequence

from llm_agents.deep_agents.limits import run_limits
from llm_agents.deep_agents.planning.prompt_loading import CatalogRow, PromptLadder
from llm_agents.deep_agents.planning.store import PLAN_STORE
from llm_agents.kg import KNOWLEDGE_GRAPHS

logger = logging.getLogger("deep_agents.planning")

# The namespaces the probe reads (the kg DDL header's shipped codes):
# 0 = DB semantics (one entity per table; tags carry the bilingual
# trader vocabulary), 1 = the prompt catalog (one entity per cataloged
# prompt row; entity name == prompt_key).
NS_DB_SEMANTICS = 0
NS_PROMPT_CATALOG = 1


class HotpathPromptProbe:
    """The plan-time consult of the promoted KG hotpath chains for the
    prompt loader's optional lane — prompt_key -> ``"hotpath"`` verdicts
    for the catalog rows the evidence implicates (empty dict = the lane
    sits out: no hotpaths yet, KG unavailable, or the cap/disabled
    knob). One instance per probe is fine; the graphs loader caches per
    process."""

    def __init__(self, store=PLAN_STORE, graphs=KNOWLEDGE_GRAPHS) -> None:
        self._store = store
        self._graphs = graphs

    async def probe(
        self,
        *,
        question: str = "",
        scope_name: str = "",
        item_labels: Sequence[str] = (),
        catalog: Sequence[CatalogRow] = (),
    ) -> Dict[str, str]:
        """The run's hotpath-corroborated load verdicts —
        prompt_key -> ``"hotpath"``, capped at
        ``run_limits().prompt_hotpath_rows`` in catalog order. Never
        raises."""
        catalog = list(catalog)
        if not catalog:
            return {}
        limits = run_limits()
        if limits.prompt_hotpath_rows <= 0:
            return {}
        needles = self._needles(question, scope_name, item_labels)
        if not needles:
            return {}
        try:
            chains = await self._store.fetch_hotpath_evidence(
                needles,
                namespaces=(NS_DB_SEMANTICS, NS_PROMPT_CATALOG),
                limit=limits.hotpath_probe_chains)
        except Exception as exc:  # noqa: BLE001 — the lane never blocks
            logger.warning("hotpath evidence fetch failed (%s) — the "
                           "corroboration lane sits out this run", exc)
            return {}
        if not chains:
            return {}

        catalog_keys = {r.prompt_key for r in catalog}
        evidence_texts: List[str] = []
        implicated: Dict[str, str] = {}
        for namespace in (NS_DB_SEMANTICS, NS_PROMPT_CATALOG):
            ns_chains = [c for c in chains if c.namespace == namespace]
            if not ns_chains:
                continue
            try:
                kg = await self._graphs.graph(namespace)
            except Exception as exc:  # noqa: BLE001 — fail-soft per ns
                logger.warning("knowledge graph namespace %d unavailable "
                               "(%s) — its hotpath chains sit out",
                               namespace, exc)
                continue
            digest = kg.entity_digest
            for chain in ns_chains:
                if chain.entity_hash != digest:
                    # Stale — the namespace's entities changed since the
                    # chain was promoted; its edge ids may resolve wrong.
                    continue
                for edge_id in chain.edge_ids:
                    edge = kg.edge(edge_id)
                    if edge is None:
                        continue
                    for member_id in edge.member_ids:
                        entity = kg.entity(member_id)
                        if entity is None:
                            continue
                        if (namespace == NS_PROMPT_CATALOG
                                and entity.name in catalog_keys):
                            # The chain names THIS cataloged skill
                            # outright — the strongest corroboration.
                            implicated[entity.name] = "hotpath"
                        evidence_texts.append(entity.name)
                        evidence_texts.extend(entity.tags)

        matches = PromptLadder(catalog).hotpath_rung(evidence_texts)
        for key, verdict in implicated.items():
            matches.setdefault(key, verdict)
        capped: Dict[str, str] = {}
        for row in catalog:
            if len(capped) >= limits.prompt_hotpath_rows:
                break
            if row.prompt_key in matches:
                capped[row.prompt_key] = matches[row.prompt_key]
        if capped:
            logger.info("hotpath corroboration: %d skill(s) load — %s",
                        len(capped), ", ".join(sorted(capped)))
        return capped

    @staticmethod
    def _needles(question: str, scope_name: str,
                 item_labels: Sequence[str]) -> List[str]:
        """The probe's query texts — the run's own vocabulary, deduped,
        blank/1-char pieces dropped (a one-char needle trigram-matches
        noise)."""
        needles: List[str] = []
        for text in [question, scope_name, *item_labels]:
            normalized = str(text or "").strip()
            if len(normalized) >= 2 and normalized not in needles:
                needles.append(normalized)
        return needles
