"""llm_agents.kg.graph.rendering — the bounded TEXT BLOCKS the tools
return: the kg_search live render, the kg_search CACHE-HIT replay
render (a logged chain replayed against the live graph), and the
kg_expand neighborhood render. Only presentation lives here — sizing,
truncation, tag policy; the traversal and its scores come from the
engine."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional, Sequence

from llm_agents.kg.graph.values import KgEdge, KgEntity, SearchResult
from llm_agents.kg.graph.vocabulary import _EDGE_RENDER_CAP, _HUB_DEGREE

if TYPE_CHECKING:  # pragma: no cover - annotation-only
    from llm_agents.kg.graph.engine import KnowledgeGraph

_DESC_MAX = 120
_TAG_RENDER_NODES = 6
NODE_MAX_DEFAULT = 10
NODE_MAX_CAP = 16


def render_search(graph: KnowledgeGraph, query: str,
                  result: SearchResult, *, hops: int,
                  scope_label: str = "ns0 DB semantics: tables/views "
                                     "and how they relate") -> str:
    """The kg_search result text: header, ranked nodes (score +
    provenance + one-line semantics), the join/derivation edges among
    them, and the drill-down hints. *scope_label* names the namespace
    searched (the kit owns the vocabulary)."""
    lines = [f"KG search '{query.strip()}' ({scope_label}: "
             f"{result.n_entities} nodes, {result.n_edges} edges) "
             f"— {len(result.nodes)} node(s), {len(result.edges)} edge(s), "
             f"hops ≤ {hops}"]
    if not result.nodes:
        lines.append("(no match — try the vocabulary the nodes carry: "
                     "trader terms like 均线/动量/估值/融资融券/期权/情绪 "
                     "or English momentum/valuation/margin/options; or "
                     "kg_expand a known name)")
        return "\n".join(lines)
    for position, node in enumerate(result.nodes, start=1):
        entity = node.entity
        kind = entity.entity_type
        desc = entity.description or ""
        if len(desc) > _DESC_MAX:
            desc = desc[:_DESC_MAX - 1] + "…"
        lines.append(f"[{position}] {entity.name} ({kind}) "
                     f"score {node.score:.2f} — {node.why}")
        if desc:
            lines.append(f"    {desc}")
        # Tags render for the head of the ranking only — the tail's
        # vocabulary is one kg_expand away, and the block stays lean.
        if entity.tags and position <= _TAG_RENDER_NODES:
            tags = ", ".join(entity.tags[:8])
            lines.append(f"    tags: {tags}")
    if result.edges:
        lines.append("edges (how the nodes relate / join; later lines "
                     "may name partners outside the ranked list):")
        rendered = 0
        for edge in result.edges:
            if rendered >= _EDGE_RENDER_CAP:
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
                  scope_label: str = "ns0 DB semantics: tables/views "
                                     "and how they relate") -> str:
    """The kg_search CACHE-HIT text: a chain logged in the cache
    (``llm.kg_queries``; replays come from its promoted copy in
    ``llm.kg_hotpaths``) replayed against the LIVE graph — the same
    edge list in logged order (the packing tiers' order, preserved by
    each row's depth ordinal) and the member nodes' one-line faces.
    The header says the traversal was not re-run (a hot-path replay
    also carries its hit_count); there are no scores/why lines (those
    belong to the live render — only the chain itself is logged)."""
    logged = created_at.astimezone(timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC")
    hits = f", hits {hit_count}" if hit_count is not None else ""
    lines = [f"KG search '{query.strip()}' ({scope_label}: "
             f"{graph.n_entities} nodes, {graph.n_edges} edges) — "
             f"cached chain {chain_id[:8]} (logged {logged}{hits}): "
             f"{len(edges)} edge(s)"]
    lines.append("edges (logged order — how the nodes relate / join):")
    for edge in edges:
        lines.append("  " + _render_edge(graph, edge))
    lines.append("nodes of the chain:")
    position = 0
    seen: set = set()
    for edge in edges:
        for member_id in edge.member_ids:
            if member_id in seen:
                continue
            seen.add(member_id)
            entity = graph.entity(member_id)
            if entity is None:
                continue
            position += 1
            desc = entity.description or ""
            if len(desc) > _DESC_MAX:
                desc = desc[:_DESC_MAX - 1] + "…"
            lines.append(f"[{position}] {entity.name} "
                         f"({entity.entity_type})"
                         + (f" — {desc}" if desc else ""))
            # Same tag policy as the live render: head of the list only.
            if entity.tags and position <= _TAG_RENDER_NODES:
                lines.append(f"    tags: {', '.join(entity.tags[:8])}")
    return "\n".join(lines)


def render_expand(graph: KnowledgeGraph, resolved: List[KgEntity],
                  edges: List[KgEdge], neighbors: List[KgEntity],
                  unresolved: List[str], *, hops: int) -> str:
    """The kg_expand result text: per named entity its degree and edges
    (arrowed by direction), then the one-line face of every neighbor."""
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
