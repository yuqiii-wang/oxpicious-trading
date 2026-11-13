"""llm_agents.kg.graph.rendering — the bounded TEXT BLOCKS the tools
return: the kg_search live render, the kg_search CACHE-HIT replay
render (a logged chain replayed against the live graph), and the
kg_expand neighborhood render. Only presentation lives here — sizing,
truncation, tag policy; the traversal and its scores come from the
engine.

The GRADUAL-LOAD TIERS (2026-11-10): a kg_search result loads in two
render tiers — ``lean`` (default) renders the head of the ranking with
lean payloads (top tables + their key columns + reading SQL templates),
``full`` renders every ranked node with the complete blocks. The tier
is PRESENTATION ONLY: the traversal, the ranking, the chain logging and
the hotpath replay identity never see it — a lean call logs the FULL
chain, so the same query re-searched with ``detail:'full'`` replays the
whole chain (the ladder's attempt 2 genuinely loads "all searched").
Attempt 3 (taught in the hints, not here): both tiers missed → stop
searching, compute in the sandbox."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

from llm_agents.kg.graph.values import KgEdge, KgEntity, SearchResult
from llm_agents.kg.graph.vocabulary import _EDGE_RENDER_CAP, _HUB_DEGREE

if TYPE_CHECKING:  # pragma: no cover - annotation-only
    from llm_agents.kg.graph.engine import KnowledgeGraph

_DESC_MAX = 120
_TAG_RENDER_NODES = 6
NODE_MAX_DEFAULT = 10
NODE_MAX_CAP = 16

# -- the gradual-load tiers ----------------------------------------------
DETAIL_LEAN = "lean"
DETAIL_FULL = "full"
DETAIL_TIERS = (DETAIL_LEAN, DETAIL_FULL)

# The lean tier's budgets (attempt 1 — "some tables and sqls"). The
# full tier keeps the constants above / the caller's caps.
_LEAN_NODE_RENDER_MAX = 4   # ranked nodes rendered (rest → the tail line)
_LEAN_DESC_MAX = 80         # per-node description chars
_LEAN_TAG_NODES = 0         # tags stay a full-tier face
_LEAN_EDGE_CAP = 6          # rendered edges among the chosen set

_LEAN_TAIL = ("[lean tier] {hidden} more ranked node(s) not rendered — "
              "kg_search again with detail:'full' to load them")
_LEAN_HEADER_TAG = ("[lean tier — kg_search detail:'full' loads every "
                    "ranked node + full schemas]")

# The ladder's last rung, appended to the no-match render: synonyms
# missed too → stop searching, compute.
_NO_MATCH_LAST_RUNG = (
    "if the synonyms also miss, STOP searching and compute in the "
    "sandbox: sandbox_write_file a script + sandbox_run it, importing "
    "study_kit first (db.fetch_daily_bars / studies.*) to load the raw "
    "bars and calculate")


def normalize_detail(value: object) -> str:
    """The validated render tier — anything but an explicit ``'full'``
    is ``'lean'`` (the safe default: attempt 1 loads some, never all)."""
    return DETAIL_FULL if str(value or "").strip().lower() == DETAIL_FULL \
        else DETAIL_LEAN


def _append_block(lines: List[str], block: Optional[str],
                  indent: str = "    ") -> None:
    """Append one pre-rendered enrichment block (the column auto-load
    payload or the related-sql-views block) indented under its node
    line."""
    if not block:
        return
    lines.extend(indent + line for line in block.splitlines())


def _append_column_block(lines: List[str], block: Optional[str],
                         indent: str = "    ") -> None:
    """Append a table's pre-rendered column block (the TableColumnCatalog
    auto-load payload) indented under its node line — the exact schema
    the LLM would otherwise fetch via db_describe_table."""
    _append_block(lines, block, indent)


def render_search(graph: KnowledgeGraph, query: str,
                  result: SearchResult, *, hops: int,
                  scope_label: str = "ns0 DB semantics: tables/views, "
                                     "the read-side sql_views templates, "
                                     "and how they relate",
                  columns: Optional[Dict[str, str]] = None,
                  views: Optional[Dict[str, str]] = None,
                  detail: str = DETAIL_LEAN) -> str:
    """The kg_search result text: header, ranked nodes (score +
    provenance + one-line semantics), the join/derivation edges among
    them, and the drill-down hints. *scope_label* names the namespace
    searched (the kit owns the vocabulary). *columns* — the
    TableColumnCatalog auto-load payload keyed by entity name; table
    nodes carrying an entry get their exact schema appended.
    *views* — the related-sql-views blocks keyed by table name (the
    ``reads_from`` fan-in the located tables serve); a table carrying
    an entry gets its reading templates listed under its node.
    *detail* — the gradual-load tier: ``lean`` renders the head of the
    ranking only (the rest named in the tail line, attempt 2 is the
    same query at ``full``); ``full`` renders every ranked node."""
    detail = normalize_detail(detail)
    lean = detail == DETAIL_LEAN
    nodes = (result.nodes[:_LEAN_NODE_RENDER_MAX] if lean
             else result.nodes)
    lines = [f"KG search '{query.strip()}' ({scope_label}: "
             f"{result.n_entities} nodes, {result.n_edges} edges) "
             f"— {len(nodes)} node(s) of {len(result.nodes)} ranked, "
             f"{len(result.edges)} edge(s), hops ≤ {hops}"]
    if lean:
        lines[-1] += f" {_LEAN_HEADER_TAG}"
    if not result.nodes:
        lines.append("(no match — PROPOSE 2-3 SYNONYMS / paraphrases of "
                     "your concept in trader vocabulary and kg_search "
                     "them again: 均线/动量/估值/融资融券/期权/情绪 or "
                     "English momentum/valuation/margin/options; or "
                     "kg_expand a known name; " + _NO_MATCH_LAST_RUNG + ")")
        return "\n".join(lines)
    desc_cap = _LEAN_DESC_MAX if lean else _DESC_MAX
    tag_cap = _LEAN_TAG_NODES if lean else _TAG_RENDER_NODES
    for position, node in enumerate(nodes, start=1):
        entity = node.entity
        kind = entity.entity_type
        desc = entity.description or ""
        if len(desc) > desc_cap:
            desc = desc[:desc_cap - 1] + "…"
        lines.append(f"[{position}] {entity.name} ({kind}) "
                     f"score {node.score:.2f} — {node.why}")
        if desc:
            lines.append(f"    {desc}")
        # Tags render for the head of the ranking only — the tail's
        # vocabulary is one kg_expand away, and the block stays lean.
        if entity.tags and position <= tag_cap:
            tags = ", ".join(entity.tags[:8])
            lines.append(f"    tags: {tags}")
        _append_column_block(lines, (columns or {}).get(entity.name))
        _append_block(lines, (views or {}).get(entity.name))
    if lean and len(result.nodes) > len(nodes):
        lines.append(_LEAN_TAIL.format(
            hidden=len(result.nodes) - len(nodes)))
    if result.edges:
        lines.append("edges (how the nodes relate / join; later lines "
                     "may name partners outside the ranked list):")
        edge_cap = _LEAN_EDGE_CAP if lean else _EDGE_RENDER_CAP
        rendered = 0
        for edge in result.edges:
            if rendered >= edge_cap:
                lines.append(f"  … {len(result.edges) - rendered} more "
                             "edge(s) omitted")
                break
            lines.append("  " + _render_edge(graph, edge))
            rendered += 1
    return "\n".join(lines)


def _render_edge(graph: KnowledgeGraph, edge: KgEdge) -> str:
    label = edge.relation if not edge.sub_relation else (
        f"{edge.relation}/{edge.sub_relation}")
    members = []
    for member_id in edge.member_ids:
        entity = graph.entity(member_id)
        members.append(entity.name if entity else f"#{member_id}")
    return f"{members[0]} -[{label}]-> {', '.join(members[1:])}"


def render_replay(graph: KnowledgeGraph, query: str,
                  edges: Sequence[KgEdge], *, chain_id: str,
                  created_at: datetime, hit_count: Optional[int] = None,
                  scope_label: str = "ns0 DB semantics: tables/views, "
                                     "the read-side sql_views templates, "
                                     "and how they relate",
                  columns: Optional[Dict[str, str]] = None,
                  detail: str = DETAIL_LEAN) -> str:
    """The kg_search CACHE-HIT text: a chain logged in the cache
    (``llm.kg_queries``; replays come from its promoted copy in
    ``llm.kg_hotpaths``) replayed against the LIVE graph — the same
    edge list in logged order (the packing tiers' order, preserved by
    each row's depth ordinal) and the member nodes' one-line faces.
    The header says the traversal was not re-run (a hot-path replay
    also carries its hit_count); there are no scores/why lines (those
    belong to the live render — only the chain itself is logged).
    *detail* — the gradual-load tier: the chain's edges always render
    whole (they ARE the cached identity); ``lean`` slices the member
    faces to the ranking head, ``full`` renders every one of them."""
    detail = normalize_detail(detail)
    lean = detail == DETAIL_LEAN
    logged = created_at.astimezone(timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC")
    hits = f", hits {hit_count}" if hit_count is not None else ""
    lines = [f"KG search '{query.strip()}' ({scope_label}: "
             f"{graph.n_entities} nodes, {graph.n_edges} edges) — "
             f"cached chain {chain_id[:8]} (logged {logged}{hits}): "
             f"{len(edges)} edge(s)"]
    if lean:
        lines[-1] += f" {_LEAN_HEADER_TAG}"
    lines.append("edges (logged order — how the nodes relate / join):")
    for edge in edges:
        lines.append("  " + _render_edge(graph, edge))
    faces: List[tuple] = []
    seen: set = set()
    for edge in edges:
        for member_id in edge.member_ids:
            if member_id in seen:
                continue
            seen.add(member_id)
            entity = graph.entity(member_id)
            if entity is None:
                continue
            faces.append(entity)
    lines.append(f"nodes of the chain: {len(faces)}")
    position = 0
    desc_cap = _LEAN_DESC_MAX if lean else _DESC_MAX
    tag_cap = _LEAN_TAG_NODES if lean else _TAG_RENDER_NODES
    for entity in (faces[:_LEAN_NODE_RENDER_MAX] if lean else faces):
        position += 1
        desc = entity.description or ""
        if len(desc) > desc_cap:
            desc = desc[:desc_cap - 1] + "…"
        lines.append(f"[{position}] {entity.name} "
                     f"({entity.entity_type})"
                     + (f" — {desc}" if desc else ""))
        # Same tag policy as the live render: head of the list only.
        if entity.tags and position <= tag_cap:
            lines.append(f"    tags: {', '.join(entity.tags[:8])}")
        _append_column_block(lines, (columns or {}).get(entity.name))
    if lean and len(faces) > _LEAN_NODE_RENDER_MAX:
        lines.append(_LEAN_TAIL.format(
            hidden=len(faces) - _LEAN_NODE_RENDER_MAX))
    return "\n".join(lines)


def render_expand(graph: KnowledgeGraph, resolved: List[KgEntity],
                  edges: List[KgEdge], neighbors: List[KgEntity],
                  unresolved: List[str], *, hops: int,
                  columns: Optional[Dict[str, str]] = None) -> str:
    """The kg_expand result text: per named entity its degree and edges
    (arrowed by direction), then the one-line face of every neighbor.
    *columns* — the TableColumnCatalog auto-load payload; each resolved
    table entity carrying an entry gets its exact schema right under
    its header line."""
    lines: List[str] = []
    if unresolved:
        for name in unresolved:
            candidates = graph.name_candidates(name)
            hint = (f" — did you mean: {', '.join(candidates)}"
                    if candidates else "")
            lines.append(f"unresolved name '{name}'{hint}")
    for entity in resolved:
        degree = graph.degree(entity.entity_id)
        lines.append(f"{entity.name} ({entity.entity_type}, {degree} edge "
                     f"end(s)) — neighborhood ≤ {hops} hop(s), "
                     f"{len(neighbors) - 1} other node(s)")
        _append_column_block(lines, (columns or {}).get(entity.name),
                             indent="  ")
        for edge in edges:
            if entity.entity_id not in edge.member_ids:
                continue
            subject = entity.entity_id == edge.member_ids[0]
            label = edge.relation if not edge.sub_relation else (
                f"{edge.relation}/{edge.sub_relation}")
            others = []
            for member_id in edge.member_ids:
                if member_id == entity.entity_id:
                    continue
                other = graph.entity(member_id)
                others.append(other.name if other else f"#{member_id}")
            arrow = "->" if subject else "<-"
            lines.append(f"  -[{label}]{arrow} {', '.join(others)}")
    if neighbors:
        lines.append("neighbors:")
        core = {entity.entity_id for entity in resolved}
        for entity in neighbors:
            if entity.entity_id in core:
                continue
            desc = entity.description or ""
            if len(desc) > _DESC_MAX:
                desc = desc[:_DESC_MAX - 1] + "…"
            tag = " *" if graph.degree(entity.entity_id) > _HUB_DEGREE else ""
            lines.append(f"  {entity.name} ({entity.entity_type}){tag} — "
                         f"{desc}")
    lines.append("(* = hub: high-degree node many edges key on — damped "
                 "in kg_search ranking)")
    return "\n".join(lines)
