"""llm_agents.kg.graph.engine — :class:`KnowledgeGraph`, the
in-memory namespace-scoped graph with its search index. The three
rungs of the traversal are its methods: seed recognition
(``seed_scores``), spreading activation (``walk``), rank + pack
(``search``), plus the drill-down face (``neighborhood``) and the
name-resolution helpers the expand face needs."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from llm_agents.kg.graph.values import (
    KgEdge, KgEntity, ScoredNode, SeedHit, SearchResult, WalkHit,
)
from llm_agents.kg.graph.vocabulary import (
    _CJK_RE, _EDGE_OUTWARD_CAP, _EDGE_RENDER_CAP, _HUB_DEGREE,
    _HUB_FACTOR, _SEED_FLOOR, _SEED_MIN, _UNIGRAM_FACTOR, _WALK_FLOOR,
    _WALK_MIN, ACT_BLEND, FACE_DESC, FACE_NAME, FACE_TAG, FACE_TYPE,
    HOP_DECAY, SUBRELATION_BOOST, WALK_BEAM, edge_factor, extract_tokens,
    normalize,
)

from llm_agents.kg.graph.data_definitions import (
    _Face,
)




class KnowledgeGraph:
    """The in-memory namespace-scoped graph with its search index.

    Construction builds two access structures: the ADJACENCY list (the
    only access path a KG walker needs — every (edge, other-member,
    is-subject) triple touching each entity) and the TOKEN INDEX (every
    face token of every entity → the entities it matches, with the
    face's weight — the online lookup the seed rung queries)."""

    def __init__(self, entities: Iterable[KgEntity],
                 edges: Iterable[KgEdge]) -> None:
        self._entities: Dict[int, KgEntity] = {
            e.entity_id: e for e in entities}
        self._edges: Dict[int, KgEdge] = {e.edge_id: e for e in edges}
        # entity_id -> ((edge, other_id, is_subject), …)
        self._adjacency: Dict[int, Tuple[Tuple[KgEdge, int, bool], ...]] = {}
        for edge in self._edges.values():
            for member_id in edge.member_ids:
                is_subject = member_id == edge.member_ids[0]
                for other in edge.member_ids:
                    if other != member_id:
                        self._adjacency.setdefault(member_id, []).append(
                            (edge, other, is_subject))
        self._adjacency = {k: tuple(v) for k, v in self._adjacency.items()}
        # sub-relation agreement face per edge (for the ×1.6 boost).
        self._edge_tokens: Dict[int, FrozenSet[str]] = {
            edge.edge_id: frozenset(
                extract_tokens(f"{edge.relation} {edge.sub_relation or ''}"))
            for edge in self._edges.values()}
        # The token index: token -> entity_id -> best face weight.
        self._index: Dict[str, Dict[int, float]] = {}
        for entity in self._entities.values():
            for face in self._faces(entity):
                for token in extract_tokens(face.text):
                    bucket = self._index.setdefault(token, {})
                    if face.weight > bucket.get(entity.entity_id, 0.0):
                        bucket[entity.entity_id] = face.weight

    # -- construction helpers ------------------------------------------

    @staticmethod
    def _faces(entity: KgEntity) -> List[_Face]:
        """The entity's matchable faces. The schema-qualified name and
        the bare table name are separate faces (queries say 'rsi', not
        'analysis.mov_ave_rsi'); each tag is its own face."""
        faces = [_Face("name", normalize(entity.name), FACE_NAME),
                 _Face("name", normalize(entity.bare_name), FACE_NAME),
                 _Face("description", normalize(entity.description),
                       FACE_DESC),
                 _Face("type", normalize(entity.entity_type), FACE_TYPE)]
        faces.extend(_Face("tag", normalize(tag), FACE_TAG)
                     for tag in entity.tags)
        return faces

    # -- lookups --------------------------------------------------------

    def entity(self, entity_id: int) -> Optional[KgEntity]:
        return self._entities.get(entity_id)

    def edge(self, edge_id: int) -> Optional[KgEdge]:
        """One edge by id — the resolver chain replays (llm.kg_queries /
        llm.kg_hotpaths edge_id) resolve their logged ids through."""
        return self._edges.get(edge_id)

    def degree(self, entity_id: int) -> int:
        """How many (edge, other-member) ends touch the entity — the
        hub signal the ranking damper and the expand renderer read."""
        return len(self._adjacency.get(entity_id, ()))

    @property
    def n_entities(self) -> int:
        return len(self._entities)

    @property
    def n_edges(self) -> int:
        return len(self._edges)

    @property
    def entity_digest(self) -> str:
        """md5 hex over the entities' (entity_id, description) pairs
        sorted by id — the entity-description digest that becomes
        ``llm.kg_queries.entity_hash`` / ``llm.kg_hotpaths.entity_hash``
        and keys the chain-cache validity: an edit or add/remove
        changes it, renames and tag/edge edits deliberately do not
        (chain replays read the live graph, so renames surface in them
        anyway)."""
        payload = "\x1e".join(
            f"{entity_id}\x1f{self._entities[entity_id].description}"
            for entity_id in sorted(self._entities))
        return hashlib.md5(payload.encode("utf-8")).hexdigest()

    def resolve_name(self, name: str) -> Optional[KgEntity]:
        """Resolve ONE entity by name: exact schema-qualified, unique
        bare name, or unique bare-name suffix (a caller may say
        'mov_ave_rsi' or 'rsi' when unambiguous). None when absent or
        ambiguous."""
        wanted = normalize(name)
        if not wanted:
            return None
        for entity in self._entities.values():
            if normalize(entity.name) == wanted:
                return entity
        bare = [e for e in self._entities.values()
                if normalize(e.bare_name) == wanted]
        if len(bare) == 1:
            return bare[0]
        suffix = [e for e in self._entities.values()
                  if normalize(e.bare_name).endswith(f" {wanted}")
                  or normalize(e.bare_name) == wanted]
        if len(suffix) == 1:
            return suffix[0]
        return None

    def name_candidates(self, prefix: str, limit: int = 8) -> List[str]:
        """Names starting with *prefix* (normalized) — the suggestion
        list an unresolvable expand target renders."""
        wanted = normalize(prefix)
        if not wanted:
            return []
        hits = [e.name for e in self._entities.values()
                if normalize(e.name).startswith(wanted)
                or normalize(e.bare_name).startswith(wanted)]
        return hits[:limit]

    # -- rung 1: seed recognition ---------------------------------------

    def seed_scores(self, tokens: Sequence[str]) -> Dict[int, SeedHit]:
        """The lexical seed rung: per query token, the entities whose
        faces carry it, weighted by the face (name > tags >
        description > type) and by the token's IDF (rarity over the
        entity population — 指数 cannot outvote rsi). Returns entity →
        accumulated score + the (token, best-face) evidence pairs."""
        n = max(1, len(self._entities))
        scores: Dict[int, float] = {}
        evidence: Dict[int, Dict[str, str]] = {}
        for token in tokens:
            bucket = self._index.get(token)
            if not bucket:
                continue
            # Unigram CJK tokens are noisier than bigrams/words: they
            # match at half strength (a shared single character is
            # weak evidence of aboutness).
            weight_scale = (_UNIGRAM_FACTOR
                            if _CJK_RE.fullmatch(token) else 1.0)
            idf = 1.0 + (n - len(bucket)) / n
            for entity_id, face_weight in bucket.items():
                contribution = face_weight * weight_scale * idf
                scores[entity_id] = scores.get(entity_id, 0.0) + contribution
                evidence.setdefault(entity_id, {})[token] = (
                    "name" if face_weight >= FACE_NAME else
                    "tag" if face_weight >= FACE_TAG else
                    "description" if face_weight >= FACE_DESC else "type")
        hits: Dict[int, SeedHit] = {}
        for entity_id, score in scores.items():
            terms = tuple(sorted(evidence[entity_id].items()))
            hits[entity_id] = SeedHit(score=score, terms=terms)
        return hits

    # -- rung 2: spreading activation -----------------------------------

    def walk(self, seeds: Dict[int, SeedHit], query_tokens: Sequence[str],
             *, max_hops: int = 2, beam: int = WALK_BEAM,
             ) -> Dict[int, WalkHit]:
        """The propagation rung. Each frontier node's activation is
        decayed by :data:`HOP_DECAY` and SPLIT across its incident
        edges proportionally to their (relation, direction) factors —
        fan-out normalized, energy preserving: a hub's many edges
        cannot multiply its influence, only divide it. Edges whose
        relation/sub_relation tokens agree with the query carry
        :data:`SUBRELATION_BOOST`. Activation accumulates across paths
        and hops; per node the BEST (highest-contribution) path is
        kept as provenance."""
        token_set = frozenset(query_tokens)
        activation: Dict[int, float] = {
            entity_id: hit.score for entity_id, hit in seeds.items()}
        frontier: List[int] = list(seeds)
        best: Dict[int, WalkHit] = {}
        best_share: Dict[int, float] = {}
        for hop in range(1, max(1, max_hops) + 1):
            if not frontier:
                break
            contributions: Dict[int, float] = {}
            paths: Dict[int, Tuple[float, int, Tuple[Tuple[int, int], ...]]] = {}
            for source in frontier:
                incident = self._adjacency.get(source)
                if not incident:
                    continue
                factors: List[Tuple[float, KgEdge, int]] = []
                for edge, other, is_subject in incident:
                    factor = edge_factor(edge, is_subject)
                    if token_set and not token_set.isdisjoint(
                            self._edge_tokens[edge.edge_id]):
                        factor *= SUBRELATION_BOOST
                    factors.append((factor, edge, other))
                total = sum(factor for factor, _, _ in factors)
                if total <= 0.0:
                    continue
                for factor, edge, other in factors:
                    share = (activation.get(source, 0.0) * HOP_DECAY
                             * factor / total)
                    contributions[other] = (contributions.get(other, 0.0)
                                            + share)
                    if other not in paths or share > paths[other][0]:
                        origin = source
                        chain: Tuple[Tuple[int, int], ...] = (
                            (edge.edge_id, source),)
                        prior = best.get(source)
                        if prior is not None and source not in seeds:
                            origin = prior.origin_id
                            chain = prior.path + chain
                        paths[other] = (share, origin, chain)
            if not contributions:
                break
            for entity_id, share in contributions.items():
                activation[entity_id] = (activation.get(entity_id, 0.0)
                                         + share)
            for entity_id, (share, origin, chain) in paths.items():
                # Provenance keeps the STRONGEST single contribution
                # across hops — a weaker later path never rewrites it.
                if share > best_share.get(entity_id, 0.0):
                    best_share[entity_id] = share
                    best[entity_id] = WalkHit(
                        activation=activation[entity_id],
                        origin_id=origin, hops=hop, path=chain)
            frontier = sorted(
                (entity_id for entity_id in contributions
                 if entity_id not in seeds),
                key=lambda entity_id: -activation[entity_id])[:beam]
        return best

    # -- rung 3: rank + pack --------------------------------------------

    def search(self, query: str, *, hops: int = 2,
               max_nodes: int = 10) -> SearchResult:
        """The full pipeline over one free-text query: tokens → seeds →
        walk → blended rank → the packed subgraph (top nodes + the
        edges among them)."""
        tokens = extract_tokens(query)
        seeds = self.seed_scores(tokens)
        walked = self.walk(seeds, tokens, max_hops=hops)
        max_seed = max((hit.score for hit in seeds.values()), default=0.0)
        max_act = max((hit.activation for hit in walked.values()),
                      default=0.0)
        ranked: List[Tuple[float, KgEntity, str]] = []
        seed_rows: List[Tuple[float, KgEntity, str]] = []
        for entity_id, hit in seeds.items():
            entity = self._entities.get(entity_id)
            if entity is None:
                continue
            why = "seed: " + ", ".join(
                f"{token}[{kind}]" for token, kind in hit.terms[:4])
            seed_rows.append((hit.score, entity, why))
        # The lexical relevance floor: the seed tail a top hit dwarfs
        # drops out (min _SEED_MIN kept) — specific questions return
        # focused sets instead of always filling the budget.
        seed_rows.sort(key=lambda item: (-item[0], item[1].name))
        if seed_rows:
            floor = seed_rows[0][0] * _SEED_FLOOR
            kept = [row for row in seed_rows if row[0] >= floor]
            seed_rows = kept if len(kept) >= _SEED_MIN \
                else seed_rows[:_SEED_MIN]
        ranked.extend(seed_rows)
        walk_rows: List[Tuple[float, KgEntity, str]] = []
        for entity_id, hit in walked.items():
            if entity_id in seeds:
                continue
            entity = self._entities.get(entity_id)
            if entity is None:
                continue
            act_part = (hit.activation / max_act if max_act > 0.0 else 0.0)
            seed_hit = seeds.get(entity_id)
            lex_part = (seed_hit.score / max_seed
                        if seed_hit is not None and max_seed > 0.0 else 0.0)
            score = ACT_BLEND * act_part + (1.0 - ACT_BLEND) * lex_part
            if seed_hit is None and self.degree(entity_id) > _HUB_DEGREE:
                score *= _HUB_FACTOR
            walk_rows.append((score, entity, self._walk_why(hit)))
        # The activation relevance floor, judged on the GLOBAL scale
        # (seeds keep raw lexical scores; walk scores blend to ~0..1 —
        # a fraction of the overall top separates real enrichment from
        # decay noise). Min _WALK_MIN kept when any walked rows exist.
        walk_rows.sort(key=lambda item: (-item[0], item[1].name))
        if walk_rows:
            global_top = max(seed_rows[0][0] if seed_rows else 0.0,
                             walk_rows[0][0])
            floor = global_top * _WALK_FLOOR
            kept = [row for row in walk_rows if row[0] >= floor]
            walk_rows = kept if len(kept) >= _WALK_MIN \
                else walk_rows[:_WALK_MIN]
        ranked.extend(walk_rows)
        ranked.sort(key=lambda item: (-item[0], item[1].name))
        chosen = ranked[:max(1, max_nodes)]
        chosen_ids = {entity.entity_id for _, entity, _ in chosen}
        # Edge packing, in two tiers: edges FULLY INSIDE the chosen set
        # first (the subgraph's own join/derivation paths), then —
        # filling the render budget — the chosen nodes' edges pointing
        # OUTWARD: a joins_on edge to a table the query did not match
        # is exactly the SQL hint the agent needs (the far endpoint
        # rides named in the edge line).
        discovered: List[KgEdge] = []
        seen_edge_ids: set = set()
        for _, entity, _ in chosen:
            for edge, _, _ in self._adjacency.get(entity.entity_id, ()):
                if edge.edge_id not in seen_edge_ids:
                    seen_edge_ids.add(edge.edge_id)
                    discovered.append(edge)
        internal = [edge for edge in discovered
                    if all(member in chosen_ids
                           for member in edge.member_ids)]
        # The outward tier is the JOIN-HINT tier: ordered by the edge's
        # walking factor from the chosen endpoint (joins_on leads,
        # then the derivation/anchor reads), so the freshest hint is
        # not crowded out by whichever edge happens to sit first in
        # adjacency order. Discovery order breaks ties.
        outward = [edge for edge in discovered if edge not in internal]
        outward.sort(key=lambda edge: -max(
            edge_factor(edge, member == edge.member_ids[0])
            for member in edge.member_ids if member in chosen_ids))
        edges = internal + outward[:min(
            _EDGE_OUTWARD_CAP,
            max(0, _EDGE_RENDER_CAP - len(internal)))]
        return SearchResult(
            nodes=tuple(ScoredNode(entity, score, why)
                        for score, entity, why in chosen),
            edges=tuple(edges), n_entities=len(self._entities),
            n_edges=len(self._edges))

    def _walk_why(self, hit: WalkHit) -> str:
        """The provenance line of one propagated node: hop count, the
        edge vocabulary of its best path, and the seed it grew from."""
        origin = self._entities.get(hit.origin_id)
        origin_name = origin.name if origin else "?"
        rels: List[str] = []
        for edge_id, _ in hit.path:
            edge = self._edges.get(edge_id)
            if edge is None:
                continue
            label = edge.relation
            if edge.sub_relation:
                label = f"{label}/{edge.sub_relation}"
            if label not in rels:
                rels.append(label)
        via = "→".join(rels) if rels else "?"
        return (f"{hit.hops} hop via {via} from {origin_name}")

    # -- the drill-down face ---------------------------------------------

    def neighborhood(self, names: Sequence[str], *, hops: int = 1,
                     max_nodes: int = 24,
                     ) -> Tuple[List[KgEntity], List[KgEdge],
                                List[KgEntity], List[str]]:
        """The expand face: resolve *names* (exact / unique bare /
        unique suffix), BFS their ≤hops neighborhoods, and return
        ``(resolved, edges, neighbors, unresolved)`` — the edges are
        every edge with ALL members inside the collected set."""
        resolved: List[KgEntity] = []
        unresolved: List[str] = []
        for name in names:
            entity = self.resolve_name(name)
            if entity is None:
                unresolved.append(name)
            elif entity not in resolved:
                resolved.append(entity)
        if not resolved:
            return [], [], [], unresolved
        seen: set = {entity.entity_id for entity in resolved}
        frontier = list(seen)
        for _ in range(max(1, hops)):
            nxt: List[int] = []
            for entity_id in frontier:
                for _, other, _ in self._adjacency.get(entity_id, ()):
                    if other not in seen:
                        seen.add(other)
                        nxt.append(other)
            frontier = nxt
        collected = set(seen)
        if len(collected) > max(1, max_nodes):
            # Keep the named cores + the nearest by BFS layer; trim the
            # last layer's excess arbitrarily-but-stably by name.
            keep = {entity.entity_id for entity in resolved}
            for entity_id in sorted(
                    collected - keep,
                    key=lambda i: self._entities[i].name):
                if len(keep) >= max(1, max_nodes):
                    break
                keep.add(entity_id)
            collected = keep
        neighbors = [self._entities[entity_id]
                     for entity_id in sorted(collected)
                     if entity_id in self._entities]
        edges = [edge for edge in self._edges.values()
                 if all(member in collected for member in edge.member_ids)]
        return resolved, edges, neighbors, unresolved
