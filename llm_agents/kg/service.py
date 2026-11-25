"""llm_agents.kg.service — ``KnowledgeGraphService``, the one face of
the knowledge-graph domain: namespace graphs loaded TTL-cached from
``llm.kg_entities`` / ``llm.kg_edges``, searched through the TWO-LAYER
chain cache, rendered to the compact tool text — with the retrieved
TABLE entities' column names + comments AUTO-LOADED into the result.

Independent of the deep-agent harness by design (the harness's
``kg_search`` / ``kg_expand`` tools in
``llm_agents/deep_agents/tools/kg_tools/kit.py`` are thin adapters
over this class — schema + argument coercion + the agent-flow hints;
everything domain lives HERE). The service owns:

  * the LOAD — one read pair per namespace (entities + edges), turned
    into one in-memory :class:`~llm_agents.kg.graph.KnowledgeGraph`
    (adjacency + token index built once at load). The load face drops
    the SERVING EXCLUSION's schemas (:data:`KG_EXCLUDED_SCHEMAS`) —
    the entities qualified into them and every edge touching one — so
    the served graph carries no live-schema tables. The graph is STATIC
    SEED DATA — rows change only when a seed file is applied — so the
    loader caches per namespace with a TTL (default 10 min): every
    conversation of a run shares one graph without a DB round trip
    per call, and an applied seed shows up on the next run without a
    daemon restart. The connection is the MAIN async handle (not the
    read replica): the graph is llm-schema metadata whose freshness
    matters right after a seed apply.
  * the SEARCH — the two-layer chain-cache flow: replay from the
    promoted ``llm.kg_hotpaths`` (hit_count bumped) → else the live
    traversal, its fresh chain matched against every chain logged in
    ``llm.kg_queries`` under the same graph digest (any query text,
    BEFORE the relog) → an identical twin promotes the query at
    hit_count 2; the fresh chain is relogged either way (both layers
    fail-soft — a cache error never breaks a search).
  * the EXPAND — one node's neighborhood (the drill-down face over
    the same graphs).
  * the COLUMN AUTO-LOAD — every TABLE/VIEW entity a retrieval
    surfaces carries its exact column names + comments (pg_catalog
    ``col_description``, one batched TTL-cached query per result —
    see ``columns.TableColumnCatalog``). The top-ranked tables are
    enriched (search: 3; replay/expand: 4) so the LLM sees the schema
    without a ``db_describe_table`` round trip; fail-soft like every
    other enrichment.
  * the RELATED-SQL-VIEWS BLOCKS — the top-ranked TABLE entities of a
    live search carry the ``reads_from`` fan-in: which sql_views
    templates (the ns0 ``view`` entities
    ``llm_agents/kg/static_rules`` derives from ``database/sql_views``)
    read that table. In-memory over the loaded graph — a hub table's
    100+ reading templates would drown in walk fan-out, so the block
    answers them deterministically.
  * the READ-TIME FRESHNESS GATE — on every graph (re)load, the
    namespace's static rules get their corpus stat-scanned
    (``static_rules.freshness.RuleFreshnessGate``) against the
    ``llm.kg_rule_syncs`` fingerprint: corpus files newer than the last
    sync, a changed file count, or a missing row trigger the rule's
    idempotent sync INLINE, BEFORE the read. Fail-soft — a gate error
    serves the stored graph with a warning; never breaks a KG tool
    result.
  * the SEARCH-CALL STATS (2026-11-23) — every ANSWERED kg_search call
    (a hot-path replay, a live match, a no-match, a gate-rejected
    result) is recorded as ONE ``llm.kg_search_stats`` row after the
    result text is built: the ranked node names + scores, the rung
    counters (``graph.SearchTrace`` — what the seed / walk rungs
    produced, what the relevance floors pruned), the replay and gate
    facts, the latency (``search_stats.SearchStatsLog``). The chain
    tables only ever saw successful non-empty traversals, so a miss was
    invisible; this ledger closes that. Fail-soft like every other
    layer — it never breaks or alters a result — and a pure side ledger:
    nothing in the search path reads it.
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Collection, Dict, FrozenSet, \
    Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from llm_agents.kg.columns import (
    COLUMN_RENDER_MAX, LEAN_COLUMNS_PER_TABLE, TABLE_COLUMNS,
)
from llm_agents.kg.graph import (
    DETAIL_FULL, NODE_MAX_CAP, NODE_MAX_DEFAULT, KgEdge, KgEntity,
    KnowledgeGraph, SearchResult, normalize_detail,
    render_expand, render_replay, render_search,
)
from llm_agents.kg.hotpaths import HotpathCache
from llm_agents.kg.query_log import QueryLog
from llm_agents.kg.search_stats import KgSearchStatRow, SearchStatsLog

# The static-seed TTL: seconds one loaded graph serves before the next
# call re-reads the tables.
KG_CACHE_TTL_SEC = 600.0

# The serving exclusion: schemas whose entities the KG tools NEVER
# serve. The `live` schema holds the operational intraday stores (the
# streamers' raw writes, the breach records) — the KG search lane is
# the study workflow's table finder, and the study tables live in
# analysis/stats, so a "live" hit is a mis-steer, not an answer. The
# load face drops every entity qualified into one of these schemas
# (and every edge touching one — no ghost endpoints) BEFORE the graph
# is built; the rows stay seeded in llm.kg_* — this is a serving-side
# filter, not a corpus edit. The digest is computed over the served
# graph, so enabling this invalidates the pre-exclusion chains once.
KG_EXCLUDED_SCHEMAS: FrozenSet[str] = frozenset({"live"})


def normalize_table_scope(names: Optional[Iterable[str]],
                          ) -> Optional[FrozenSet[str]]:
    """The normalized FORCED-TABLE-SCOPE allowlist: stripped,
    casefolded, deduped table names; ``None`` when nothing usable
    remains — no limitation, every seeded table serves (the default).
    The callers hand raw strings (the deep-agent run data's
    ``forced_tables``, a probe's list); junk entries drop out
    tolerantly."""
    if not names:
        return None
    tokens = frozenset(
        str(name).strip().casefold() for name in names
        if name is not None and str(name).strip())
    return tokens or None


def normalize_schema_scope(names: Optional[Iterable[str]],
                           ) -> Optional[FrozenSet[str]]:
    """The normalized FORCED-SCHEMA-SCOPE allowlist (2026-11-11): the
    same normalization as :func:`normalize_table_scope` over SCHEMA
    names — every schema-qualified entity under one of them serves.
    The AI-summary bridge rides its standing five (analysis / strategy
    / stats / text / multi_media) as the run data's ``forced_schemas``;
    ``None`` = no schema limitation."""
    return normalize_table_scope(names)

# The column auto-load budgets: how many retrieved table entities one
# result enriches (ranked order — the head of the list is what the LLM
# acts on; the tail's schemas stay one kg_expand away). The LEAN pair
# is the gradual-load tier's enrichment budget (attempt 1 — "some
# tables and sqls"); the tier never changes the traversal.
SEARCH_TABLE_COLUMNS_LIMIT = 3
REPLAY_TABLE_COLUMNS_LIMIT = 4
EXPAND_TABLE_COLUMNS_LIMIT = 4
LEAN_TABLE_COLUMNS_LIMIT = 2

# The related-sql-views budgets: how many retrieved TABLE entities one
# live search enriches with their reads_from fan-in, and how many
# reading templates one block lists (+ an "N more" line beyond). The
# LEAN pair is the gradual-load tier's cut of the same fan-in.
SEARCH_SQL_VIEWS_LIMIT = 3
SQL_VIEWS_PER_TABLE = 5
_SQL_VIEW_DESC_MAX = 100
LEAN_SQL_VIEWS_LIMIT = 2
LEAN_VIEWS_PER_TABLE = 3
_LEAN_VIEW_DESC_MAX = 60

logger = logging.getLogger(__name__)


class KgUnavailableError(Exception):
    """The knowledge-graph corpus cannot load (a DB error)."""


class KgGateCandidate(BaseModel):
    """One ranked result entity offered to the relevance judge (the
    capped facts face — name + kind + a short summary, never the
    rendered dossier)."""

    name: str = Field(description="The schema-qualified entity name.")
    kind: str = Field(default="", description="The entity_type.")
    summary: str = Field(
        default="", description="The description's capped head "
                                "(120 chars).")


class KgGateVerdict(BaseModel):
    """One relevance judge's answer over a result's candidates.
    ``accepted`` carries the entity names that ship (multi-answer);
    ``rejected`` means nothing does (the caller sends its own
    no-match nudge); ``unavailable`` = the judge could not read (the
    caller ships the UNGATED render — the fail-soft contract)."""

    status: str = Field(description="'accepted' | 'rejected' | "
                                    "'unavailable'")
    names: List[str] = Field(default_factory=list,
                             description="The accepted names on "
                                         "'accepted'.")


# The judge the gated flows call: (query, scope_label, candidates) →
# a verdict. The kg package stays independent — the callback is
# INJECTED per call (the deep-agent tool layer adapts its duck-typed
# decision engine onto this signature); None judge = ungated.
KgGateJudge = Callable[[str, str, List[KgGateCandidate]],
                       Awaitable[KgGateVerdict]]

# The candidates one gate read judges (the choice question's option cap
# is 26 incl. reject — 16 keeps headroom and matches NODE_MAX_CAP).
_GATE_CANDIDATE_MAX = 16

_GATE_SUMMARY_MAX = 120


# The per-namespace vocabulary the renders carry: the scope label for
# headers (the agent-flow hint AFTER a hit is tool-layer vocabulary —
# ns0 walks into columns/data, ns1 loads a skill, ns2 decodes codes /
# locates the typed tables — and stays with the adapters in the
# deep-agent kit).
NS_SCOPES: Dict[int, str] = {
    0: "ns0 DB semantics: tables/views, the read-side sql_views "
       "templates, and how they relate",
    1: "ns1 prompt catalog: skills & plan prompts",
    2: "ns2 data & labels: the static.label_dim SMALLINT type "
       "vocabularies and the tables each one types",
}


class KnowledgeGraphService:
    """The KG domain service — per-process state is the TTL graph
    cache (share the :data:`KNOWLEDGE_GRAPHS` singleton; a fresh
    instance only pays re-reading the tables)."""

    def __init__(self, *, cache_ttl_sec: float = KG_CACHE_TTL_SEC) -> None:
        self._cache_ttl_sec = cache_ttl_sec
        # namespace -> (loaded_at_monotonic, graph)
        self._graphs: Dict[int, Tuple[float, KnowledgeGraph]] = {}
        # (namespace, table tokens, schema tokens) ->
        # (derived_at_monotonic, graph) — the forced-scope subgraphs,
        # TTL-cached beside the base graphs (one derivation per active
        # scope per TTL window; a scoped graph's digest is the
        # chain-cache identity, so the same run's calls must share one
        # derivation).
        self._scoped_graphs: Dict[
            Tuple[int, FrozenSet[str], FrozenSet[str]],
            Tuple[float, KnowledgeGraph]] = {}
        self._log = QueryLog()
        self._hotpaths = HotpathCache()
        self._stats = SearchStatsLog()
        self._columns = TABLE_COLUMNS

    # -- the column auto-load -------------------------------------------

    async def _table_column_blocks(
        self, entities: Iterable[KgEntity], limit: int,
        *, max_columns: int = COLUMN_RENDER_MAX,
    ) -> Dict[str, str]:
        """The per-table column blocks for the retrieved table/view
        entities — the first ``limit`` TABLE/VIEW entities in iteration
        order (ranked order for search results, chain order for
        replays), each rendered at *max_columns* columns. Any
        introspection failure is fail-soft: ``{}`` and a warning — a
        KG tool result never breaks on the column layer."""
        names: list[str] = []
        for entity in entities:
            if entity.entity_type not in ("table", "view"):
                continue
            if entity.name in names:
                continue
            names.append(entity.name)
            if len(names) >= limit:
                break
        if not names:
            return {}
        try:
            return await self._columns.blocks(
                names, max_columns=max_columns)
        except Exception as exc:  # noqa: BLE001 — fail-soft by contract
            logger.warning("kg column auto-load failed for %s: "
                           "%s: %s", names, type(exc).__name__, exc)
            return {}

    def _sql_view_blocks(self, kg: KnowledgeGraph,
                         entities: Iterable[KgEntity], *, limit: int,
                         per_table: int, desc_max: int) -> Dict[str, str]:
        """The related-sql-views blocks for the retrieved TABLE
        entities — the first ``limit`` tables in iteration order
        (ranked order), each listing (bounded by *per_table*, one-line
        descriptions capped at *desc_max*) the ``reads_from`` template
        entities that read it. Purely in-memory over the loaded graph —
        no DB round trip, cannot fail. The walk would spread toward
        these views too, but a hub table's fan-out dilutes each reading
        template to noise; the block is the deterministic answer."""
        tables: list[KgEntity] = []
        seen: set[str] = set()
        for entity in entities:
            if entity.entity_type != "table" or entity.name in seen:
                continue
            seen.add(entity.name)
            tables.append(entity)
            if len(tables) >= limit:
                break
        blocks: Dict[str, str] = {}
        for entity in tables:
            views = kg.related_views(entity.entity_id)
            if not views:
                continue
            lines = [f"sql views reading this ({len(views)}):"]
            for view in views[:per_table]:
                desc = (view.description or "").strip()
                if len(desc) > desc_max:
                    desc = desc[:desc_max - 1] + "…"
                lines.append(f"  {view.name} — {desc}" if desc
                             else f"  {view.name}")
            if len(views) > per_table:
                lines.append(f"  … {len(views) - per_table} more "
                             "(kg_expand the table)")
            blocks[entity.name] = "\n".join(lines)
        return blocks

    # -- the read-time freshness gate -------------------------------------

    async def _sync_stale_rules(self, conn, namespace: int) -> None:
        """The static rules' freshness gate on the graph (re)load path:
        each namespace-guarding rule stat-scans its corpus against its
        ``llm.kg_rule_syncs`` fingerprint and, on a diff, re-syncs the
        derived rows INLINE (before this load's entity/edge fetch).
        Fail-soft by contract — a gate error logs and serves the
        stored graph; it never breaks a KG tool result. No lock: the
        sync is idempotent and single-transaction, so concurrent
        reloads may at worst duplicate one pass."""
        # Lazy import — the writer side stays out of the module graph
        # until a load actually needs it.
        from llm_agents.kg.static_rules import (
            NAMESPACE_RULES, RuleFreshnessGate, StaticRulesWriter,
        )

        writer = StaticRulesWriter()
        for rule in NAMESPACE_RULES.get(namespace, ()):
            try:
                report = await RuleFreshnessGate(rule).sync_if_stale(
                    conn, writer)
            except Exception as exc:  # noqa: BLE001 — fail-soft by contract
                logger.warning("kg freshness gate failed for rule %s "
                               "(serving the stored graph): %s: %s",
                               rule.name, type(exc).__name__, exc)
                continue
            if report is not None:
                logger.info("kg freshness gate re-synced namespace %d "
                            "before the read:\n%s",
                            namespace, report.render())

    # -- the load -------------------------------------------------------

    @staticmethod
    def _schema_excluded(name: str) -> bool:
        """Whether one entity NAME is qualified into an excluded schema
        (:data:`KG_EXCLUDED_SCHEMAS`) — the serving exclusion's only
        predicate. Only schema-qualified names match (the dot is
        required): prompt keys, label domains and the slash-path sql_views
        template names cannot collide with them."""
        head, dot, _rest = str(name).partition(".")
        return bool(dot) and head in KG_EXCLUDED_SCHEMAS

    async def graph(self, namespace: int = 0) -> KnowledgeGraph:
        """The namespace's graph, cached for the service TTL. On the
        (re)load path the static rules' freshness gate runs first —
        corpus files newer than the ``llm.kg_rule_syncs`` fingerprint
        (or a changed corpus file count) re-sync the derived rows
        INLINE, before the entities/edges are read. The serving
        exclusion (:data:`KG_EXCLUDED_SCHEMAS`) drops the excluded
        schemas' entities and every edge touching one before the graph
        is built. Raises :class:`KgUnavailableError` on a DB error or
        an EMPTY graph (an unseeded namespace is a deployment problem
        to surface)."""
        cached = self._graphs.get(namespace)
        now = time.monotonic()
        if cached is not None and now - cached[0] < self._cache_ttl_sec:
            return cached[1]
        # Lazy import — the activation-order rule the llm_agents modules
        # follow for _common.
        from _common.db_commons import get_db_connection_async

        try:
            conn = await get_db_connection_async()
            try:
                await self._sync_stale_rules(conn, namespace)
                entity_rows = await conn.fetch(
                    "SELECT entity_id, name, entity_type, description, "
                    "tags FROM llm.kg_entities WHERE namespace = $1",
                    namespace)
                edge_rows = await conn.fetch(
                    "SELECT edge_id, relation, sub_relation, entity_ids "
                    "FROM llm.kg_edges WHERE namespace = $1", namespace)
            finally:
                await conn.close()
        except Exception as exc:
            raise KgUnavailableError(
                f"knowledge graph namespace {namespace} could not be "
                f"read: {exc}") from exc
        if not entity_rows:
            raise KgUnavailableError(
                f"knowledge graph namespace {namespace} is empty — the "
                "seed files under database/sql/llm/static/knowledge_graphs/ "
                "have not been applied (temp_scripts/apply_kg.py)")
        # The serving exclusion runs BEFORE the graph is built: the
        # excluded schemas' entities and every edge touching one (no
        # ghost endpoints) drop out, so the adjacency, the token index
        # and the digest are all built over the served graph only.
        excluded_ids = {int(row["entity_id"]) for row in entity_rows
                        if self._schema_excluded(row["name"])}
        if excluded_ids:
            entity_rows = [row for row in entity_rows
                           if int(row["entity_id"]) not in excluded_ids]
            edge_rows = [row for row in edge_rows
                         if all(int(member) not in excluded_ids
                                for member in row["entity_ids"])]
        graph = KnowledgeGraph(
            entities=(KgEntity(
                entity_id=int(row["entity_id"]),
                name=str(row["name"]),
                entity_type=str(row["entity_type"]),
                description=str(row["description"] or ""),
                tags=tuple(str(tag) for tag in (row["tags"] or ())),
            ) for row in entity_rows),
            edges=(KgEdge(
                edge_id=int(row["edge_id"]),
                relation=str(row["relation"]),
                sub_relation=(str(row["sub_relation"])
                              if row["sub_relation"] is not None else None),
                member_ids=tuple(int(member) for member in row["entity_ids"]),
            ) for row in edge_rows))
        self._graphs[namespace] = (now, graph)
        return graph

    # -- the forced-table-scope graph --------------------------------------

    async def _graph_for(self, namespace: int,
                         allowed_tables: Optional[Collection[str]],
                         allowed_schemas: Optional[Collection[str]] = None,
                         ) -> Tuple[KnowledgeGraph,
                                    Optional[FrozenSet[str]],
                                    Optional[FrozenSet[str]]]:
        """The graph one search/expand/hints call serves: the TTL-cached
        namespace graph, narrowed to the forced scope when one rides —
        the table allowlist and/or the schema allowlist. ns1 is EXEMPT
        — the prompt catalog names no tables, so a table/schema
        allowlist cannot constrain it (the scoped derivation would
        yield an empty graph by construction). The scoped subgraph is
        TTL-cached beside the base graph, keyed by the normalized
        scope pair; a cached derivation also goes stale when the base
        graph reloaded after it was derived. Returns the graph and the
        active scopes (``None`` = no limitation on that channel)."""
        tokens = normalize_table_scope(allowed_tables)
        schema_tokens = normalize_schema_scope(allowed_schemas)
        if (tokens is None and schema_tokens is None) or namespace == 1:
            return await self.graph(namespace), None, None
        key = (namespace, tokens or frozenset(),
               schema_tokens or frozenset())
        now = time.monotonic()
        cached = self._scoped_graphs.get(key)
        if cached is not None and now - cached[0] < self._cache_ttl_sec:
            base = self._graphs.get(namespace)
            if base is None or base[0] <= cached[0]:
                return cached[1], tokens, schema_tokens
        graph = (await self.graph(namespace)).scoped(tokens or (),
                                                    schema_tokens or ())
        # Opportunistic eviction: the namespace's other expired scopes
        # drop out so the cache stays bounded to the live ones.
        for stale in [k for k, (derived_at, _g)
                      in self._scoped_graphs.items()
                      if k[0] == namespace
                      and now - derived_at >= self._cache_ttl_sec]:
            self._scoped_graphs.pop(stale, None)
        self._scoped_graphs[key] = (time.monotonic(), graph)
        return graph, tokens, schema_tokens

    @staticmethod
    def _scope_note(tokens: Optional[FrozenSet[str]],
                    schema_tokens: Optional[FrozenSet[str]] = None,
                    ) -> str:
        """The one-line note prepended to a rendered result when the
        forced scope is active — the model must know WHY tables it
        expects are missing (out of scope, not absent from the
        corpus)."""
        parts: list[str] = []
        if tokens:
            names = ", ".join(sorted(tokens)[:6])
            more = f" … +{len(tokens) - 6}" if len(tokens) > 6 else ""
            parts.append(f"{len(tokens)} named table(s) ({names}{more})")
        if schema_tokens:
            names = ", ".join(sorted(schema_tokens))
            parts.append(f"schemas ({names})")
        if not parts:
            return ""
        return (f"[forced scope — serving only "
                f"{' and '.join(parts)} in this result; results "
                "outside the list are out of scope for this run]")

    # -- the search -----------------------------------------------------

    async def search(self, query: str, *, namespace: int = 0,
                     hops: int = 2,
                     max_results: int = NODE_MAX_DEFAULT,
                     detail: str = "lean",
                     allowed_tables: Optional[Collection[str]] = None,
                     allowed_schemas: Optional[Collection[str]] = None,
                     ) -> str:
        """The UNGATED kg_search face (the historical contract — the
        callers that pre-date the relevance gate)."""
        text, _verdict = await self._search(
            query, namespace=namespace, hops=hops,
            max_results=max_results, detail=detail,
            allowed_tables=allowed_tables,
            allowed_schemas=allowed_schemas)
        return text

    async def search_gated(
            self, query: str, *, judge: KgGateJudge, namespace: int = 0,
            hops: int = 2, max_results: int = NODE_MAX_DEFAULT,
            detail: str = "lean",
            allowed_tables: Optional[Collection[str]] = None,
            allowed_schemas: Optional[Collection[str]] = None,
            ) -> Tuple[str, str]:
        """The GATED kg_search face (2026-11-12): a live traversal's
        ranked candidates go through the injected ``judge`` (one
        System One read) BEFORE the render — ``accepted`` renders only
        the accepted entities, ``rejected`` returns an EMPTY text (the
        caller substitutes its no-match nudge), ``unavailable`` / no
        judge / a HOTPATH REPLAY (a proven chain is already validated)
        ships the full render. Returns ``(text, verdict)`` with
        verdict ``ungated | accepted | rejected``."""
        return await self._search(
            query, judge=judge, namespace=namespace, hops=hops,
            max_results=max_results, detail=detail,
            allowed_tables=allowed_tables,
            allowed_schemas=allowed_schemas)

    async def _search(self, query: str, *, judge: Optional[KgGateJudge]
                     = None, namespace: int = 0,
                     hops: int = 2,
                     max_results: int = NODE_MAX_DEFAULT,
                     detail: str = "lean",
                     allowed_tables: Optional[Collection[str]] = None,
                     allowed_schemas: Optional[Collection[str]] = None,
                     ) -> Tuple[str, str]:
        """One kg_search answered: the two-layer chain-cache flow over
        the namespace's graph, rendered to the compact tool text (the
        caller — the deep-agent adapter — appends its agent-flow hint).

        *detail* — the gradual-load tier (``lean`` default | ``full``):
        the ENGINE result is tier-independent — both tiers traverse,
        rank and log identically — but the ANSWER PATH differs: lean
        may replay a promoted hotpath chain (the common repeat), full
        always renders the live ranking (attempt 2 must load "all
        searched", never a cached chain's member subset).

        *allowed_tables* / *allowed_schemas* — the FORCED SCOPE
        (2026-11-11): when a run names its DB tables and/or schemas,
        the search serves the scoped subgraph only (the named tables
        and the schemas' entities + their attached read-side
        views/label domains; ns1 exempt). The scoped graph's own digest
        keys the chain cache, so scoped chains never replay into
        unscoped searches or another scope; the render carries a scope
        note line so the model knows out-of-scope means filtered, not
        absent.

        The llm.kg_hotpaths HOT-PATH replay layer: kg_search replays
        ONLY proven-repeatable chains. The exact-match lookup hits
        llm.kg_hotpaths (a query promoted after its chain was produced
        identically TWICE) and bumps its hit_count; a miss — or a chain
        whose logged edges no longer resolve — runs the live traversal.
        The fresh chain is then matched against EVERY chain logged in
        llm.kg_queries under the same graph digest (any query text,
        BEFORE the relog — a just-logged chain would match itself): an
        identical twin means the chain has TWO hits, and the query is
        promoted into kg_hotpaths with hit_count 2. The relog to
        kg_queries happens either way (both layers fail-soft).

        Raises :class:`KgUnavailableError` when the graph cannot load,
        ``ValueError`` on an empty query or an unknown namespace (an
        invalid call is rejected before anything is recorded).

        THE SEARCH-CALL STATS (2026-11-23): every ANSWERED call — a
        replay, a live match, a no-match, a gate-rejected result — is
        recorded as ONE ``llm.kg_search_stats`` row after the result
        text is built (the row is this call's accumulator —
        :class:`~llm_agents.kg.search_stats.KgSearchStatRow`, written by
        :mod:`llm_agents.kg.search_stats`). Fail-soft: the ledger never
        breaks or alters a result, and nothing in the search path reads
        it."""
        query = query.strip()
        if not query:
            raise ValueError("pass business keywords (e.g. '动量 momentum' "
                             "or 'margin 融资融券')")
        if namespace not in NS_SCOPES:
            raise ValueError(
                f"namespace must be one of {sorted(NS_SCOPES)}")
        hops = max(1, min(3, int(hops)))
        max_results = max(4, min(NODE_MAX_CAP, int(max_results)))
        detail = normalize_detail(detail)
        stat = KgSearchStatRow(
            query=query, namespace=namespace, hops=hops,
            max_results=max_results, full_detail=detail == DETAIL_FULL)
        started = time.perf_counter()
        text, verdict = await self._answer(
            query, judge=judge, namespace=namespace, hops=hops,
            max_results=max_results, detail=detail,
            allowed_tables=allowed_tables,
            allowed_schemas=allowed_schemas, stat=stat)
        stat.latency_ms = round(
            (time.perf_counter() - started) * 1000.0, 3)
        await self._stats.record(stat)
        return text, verdict

    async def _answer(self, query: str, *, judge: Optional[KgGateJudge],
                      namespace: int, hops: int, max_results: int,
                      detail: str,
                      allowed_tables: Optional[Collection[str]],
                      allowed_schemas: Optional[Collection[str]],
                      stat: KgSearchStatRow) -> Tuple[str, str]:
        """The answer path of ``_search`` (its two-layer chain-cache
        flow, the gate and the render — see ``_search``): returns the
        ``(text, verdict)`` and fills *stat* with what it served for the
        stats row. *query* arrives stripped and validated."""
        lean = not stat.full_detail
        kg, tokens, schema_tokens = await self._graph_for(
            namespace, allowed_tables, allowed_schemas)
        note = self._scope_note(tokens, schema_tokens)
        stat.forced_scope = tokens is not None or schema_tokens is not None
        stat.entity_hash = kg.entity_digest
        # The hotpath replay serves the LEAN tier (the common repeat —
        # answer economy). A FULL call is the ladder's escalation, so it
        # RE-TRAVERSES live: a replay's faces are the chain's members,
        # not the ranked list — attempt 2 ("all searched") must never
        # lose faces attempt 1 showed because a ranked node carried no
        # packed edge into the cached chain.
        hot = (await self._hotpaths.lookup(query, namespace,
                                           kg.entity_digest)
               if lean else None)
        if hot is not None:
            edges: Tuple[KgEdge, ...] = tuple(
                edge for edge in (kg.edge(edge_id)
                                  for edge_id in hot.edge_ids)
                if edge is not None)
            if edges:
                chain_entities = [
                    entity for member_id in
                    (mid for edge in edges for mid in edge.member_ids)
                    if (entity := kg.entity(member_id)) is not None
                ]
                # The stats face of a replay: the chain's DISTINCT
                # members in chain order at score 0 (a replay carries no
                # scores — the chain, not a ranking, is its identity).
                members: Dict[int, KgEntity] = {}
                for entity in chain_entities:
                    members.setdefault(entity.entity_id, entity)
                stat.note_replay(hot.chain_id, tuple(members.values()),
                                 len(edges))
                core = render_replay(
                    kg, query, edges,
                    chain_id=hot.chain_id,
                    created_at=hot.created_at,
                    hit_count=hot.hit_count,
                    scope_label=NS_SCOPES[namespace],
                    columns=await self._table_column_blocks(
                        chain_entities,
                        LEAN_TABLE_COLUMNS_LIMIT if lean
                        else REPLAY_TABLE_COLUMNS_LIMIT,
                        max_columns=LEAN_COLUMNS_PER_TABLE if lean
                        else COLUMN_RENDER_MAX),
                    detail=detail)
                return (f"{note}\n{core}" if note else core), "ungated"
        result = kg.search(query, hops=hops, max_nodes=max_results)
        stat.note_result(result)
        if result.edges:
            edge_ids = tuple(edge.edge_id for edge in result.edges)
            twin = await self._log.find_identical_chain(
                edge_ids, namespace, kg.entity_digest)
            await self._log.log_chain(
                query, namespace, kg.entity_digest, edge_ids)
            if twin is not None:
                stat.promoted = True
                await self._hotpaths.promote(
                    query, namespace, kg.entity_digest, edge_ids)
        verdict = "ungated"
        if judge is not None and result.nodes:
            stat.gate_read = True
            gate = await judge(
                query, NS_SCOPES[namespace],
                [KgGateCandidate(
                    name=node.entity.name,
                    kind=node.entity.entity_type,
                    summary=(node.entity.description or "")
                    [:_GATE_SUMMARY_MAX])
                 for node in result.nodes[:_GATE_CANDIDATE_MAX]])
            if gate is not None and gate.status == "rejected":
                stat.gate_rejected = True
                return "", "rejected"
            if (gate is not None and gate.status == "accepted"
                    and gate.names):
                stat.gate_accepted = True
                keep = frozenset(gate.names)
                kept = tuple(node for node in result.nodes
                             if node.entity.name in keep)
                kept_edges = tuple(
                    edge for edge in result.edges
                    if all(
                        (entity := kg.entity(mid)) is not None
                        and entity.name in keep
                        for mid in edge.member_ids))
                result = SearchResult(
                    nodes=kept, edges=kept_edges,
                    n_entities=len(kept), n_edges=len(kept_edges))
                verdict = "accepted"
        ranked = (node.entity for node in result.nodes)
        core = render_search(kg, query, result, hops=hops,
                             scope_label=NS_SCOPES[namespace],
                             columns=await self._table_column_blocks(
                                 ranked,
                                 LEAN_TABLE_COLUMNS_LIMIT if lean
                                 else SEARCH_TABLE_COLUMNS_LIMIT,
                                 max_columns=LEAN_COLUMNS_PER_TABLE if lean
                                 else COLUMN_RENDER_MAX),
                             views=self._sql_view_blocks(
                                 kg, (node.entity
                                      for node in result.nodes),
                                 limit=LEAN_SQL_VIEWS_LIMIT if lean
                                 else SEARCH_SQL_VIEWS_LIMIT,
                                 per_table=LEAN_VIEWS_PER_TABLE if lean
                                 else SQL_VIEWS_PER_TABLE,
                             desc_max=_LEAN_VIEW_DESC_MAX if lean
                             else _SQL_VIEW_DESC_MAX),
                             detail=detail)
        return (f"{note}\n{core}" if note else core), verdict

    # -- the read-only table-hint lane -----------------------------------

    async def table_hits(self, query: str, *, namespace: int = 0,
                         hops: int = 1, max_results: int = 3,
                         allowed_tables: Optional[Collection[str]] = None,
                         allowed_schemas: Optional[Collection[str]] = None,
                         ) -> Dict[str, str]:
        """The retrieved TABLE/VIEW entities for ``query`` as a plain
        ``{schema-qualified name: one-line description}`` mapping — the
        READ-ONLY face the runner's per-step data hints consume (the
        kg_search TOOL lane stays the logged / hot-path-promotable
        surface; this lane neither logs nor promotes, it only reads the
        TTL-cached graph). *allowed_tables* / *allowed_schemas* — the
        forced scope, same serving rule as :meth:`search`. Raises
        ``ValueError`` on an empty query or an unknown namespace."""
        query = query.strip()
        if not query:
            raise ValueError("empty query")
        if namespace not in NS_SCOPES:
            raise ValueError(f"unknown namespace {namespace}")
        kg, _tokens, _schemas = await self._graph_for(
            namespace, allowed_tables, allowed_schemas)
        result = kg.search(query, hops=max(1, min(3, int(hops))),
                           max_nodes=max(1, int(max_results)))
        return {node.entity.name: (node.entity.description or "")
                for node in result.nodes
                if node.entity.entity_type in ("table", "view")
                and node.entity.description}

    # -- the expand -----------------------------------------------------

    async def expand(self, names: Sequence[str], *, namespace: int = 0,
                     hops: int = 1,
                     allowed_tables: Optional[Collection[str]] = None,
                     allowed_schemas: Optional[Collection[str]] = None,
                     ) -> str:
        """The UNGATED kg_expand face (the historical contract)."""
        text, _verdict = await self._expand(
            names, namespace=namespace, hops=hops,
            allowed_tables=allowed_tables,
            allowed_schemas=allowed_schemas)
        return text

    async def expand_gated(
            self, names: Sequence[str], *, judge: KgGateJudge,
            namespace: int = 0, hops: int = 1,
            allowed_tables: Optional[Collection[str]] = None,
            allowed_schemas: Optional[Collection[str]] = None,
            ) -> Tuple[str, str]:
        """The GATED kg_expand face (2026-11-12): the neighborhood
        candidates (the added context around the requested names — the
        requested names themselves always stay) go through the injected
        ``judge`` before the render; ``rejected`` returns an EMPTY
        text, ``unavailable`` / no judge ships the full render.
        Returns ``(text, verdict)``."""
        return await self._expand(
            names, judge=judge, namespace=namespace, hops=hops,
            allowed_tables=allowed_tables,
            allowed_schemas=allowed_schemas)

    async def _expand(self, names: Sequence[str], *,
                      judge: Optional[KgGateJudge] = None,
                      namespace: int = 0,
                     hops: int = 1,
                     allowed_tables: Optional[Collection[str]] = None,
                     allowed_schemas: Optional[Collection[str]] = None,
                     ) -> Tuple[str, str]:
        """One kg_expand answered: the named nodes' neighborhood over
        the namespace's graph, rendered to the compact tool text.
        *allowed_tables* / *allowed_schemas* — the forced scope, same
        serving rule as :meth:`search` (an out-of-scope name resolves
        to nothing here — the drill-down cannot leak what the search
        may not show). Raises :class:`KgUnavailableError` when the
        graph cannot load, ``ValueError`` on no names or an unknown
        namespace."""
        names = [str(name).strip() for name in names if str(name).strip()]
        if not names:
            raise ValueError("pass name(s), e.g. "
                             "['analysis.mov_ave_rsi'] (ns0) or ['pe-guide'] "
                             "(ns1)")
        if namespace not in NS_SCOPES:
            raise ValueError(
                f"namespace must be one of {sorted(NS_SCOPES)}")
        hops = max(1, min(2, int(hops)))
        kg, tokens, schema_tokens = await self._graph_for(
            namespace, allowed_tables, allowed_schemas)
        resolved, edges, neighbors, unresolved = kg.neighborhood(
            names, hops=hops)
        if not resolved:
            raise ValueError(
                f"none of {names} resolved in namespace {namespace} — "
                "use kg_search to find the right name first")
        verdict = "ungated"
        keep = (frozenset(entity.name for entity in resolved)
                | frozenset(entity.name for entity in neighbors))
        if judge is not None and neighbors:
            gate = await judge(
                ", ".join(entity.name for entity in resolved),
                NS_SCOPES[namespace],
                [KgGateCandidate(
                    name=entity.name, kind=entity.entity_type,
                    summary=(entity.description or "")[:_GATE_SUMMARY_MAX])
                 for entity in neighbors[:_GATE_CANDIDATE_MAX]])
            if gate is not None and gate.status == "rejected":
                return "", "rejected"
            if (gate is not None and gate.status == "accepted"
                    and gate.names):
                keep = (keep & frozenset(
                    entity.name for entity in resolved)) | \
                    frozenset(gate.names)
                neighbors = [entity for entity in neighbors
                             if entity.name in keep]
                edges = [edge for edge in edges
                         if all((entity := kg.entity(mid)) is not None
                                and entity.name in keep
                                for mid in edge.member_ids)]
                verdict = "accepted"
        core = render_expand(kg, resolved, edges, neighbors, unresolved,
                             hops=hops,
                             columns=await self._table_column_blocks(
                                 resolved, EXPAND_TABLE_COLUMNS_LIMIT))
        note = self._scope_note(tokens, schema_tokens)
        return (f"{note}\n{core}" if note else core), verdict
