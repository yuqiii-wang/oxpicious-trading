"""llm_agents.kg.graph.engine — :class:`KnowledgeGraph`, the
in-memory namespace-scoped graph with its search index. The three
rungs of the traversal are its methods: seed recognition
(``seed_scores`` — exact tokens, plus the FUZZY LANE for a token with
no exact bucket: ``plural_stem`` / ``fuzzy_match``), spreading
activation (``walk``), rank + pack (``search``, which also reports the
rung counters as a ``SearchTrace``), plus the drill-down face
(``neighborhood``) and the name-resolution helpers the expand face
needs."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Collection, Dict, FrozenSet, Iterable, List, Optional, \
    Sequence, Tuple

from llm_agents.kg.graph.values import (
    KgEdge, KgEntity, ScoredNode, SeedHit, SearchResult, WalkHit,
)
from llm_agents.kg.graph.vocabulary import (
    _CJK_RE, _EDGE_OUTWARD_CAP, _EDGE_RENDER_CAP, _HUB_DEGREE,
    _HUB_FACTOR, _SEED_FLOOR, _SEED_MIN, _UNIGRAM_FACTOR, _WALK_FLOOR,
    _WALK_MIN, ACT_BLEND, FACE_DESC, FACE_NAME, FACE_TAG, FACE_TYPE,
    FUZZY_DAMP, FUZZY_SIMILARITY_MIN, HOP_DECAY, READS_FROM,
    SUBRELATION_BOOST, WALK_BEAM, char_trigrams, edge_factor,
    extract_tokens, is_fuzzy_token, normalize, seed_type_damp,
)

from llm_agents.kg.graph.data_definitions import (
    _Face,
    ResolvedToken,
    SearchTrace,
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
        # The fuzzy lane's postings (vocabulary.char_trigrams): trigram →
        # the latin index tokens carrying it, plus each such token's own
        # trigram count (the similarity denominator). Built with the
        # index, so a scoped subgraph (``scoped``) carries its own.
        postings: Dict[str, set] = {}
        self._trigram_counts: Dict[str, int] = {}
        for token in self._index:
            if not is_fuzzy_token(token):
                continue
            trigrams = char_trigrams(token)
            self._trigram_counts[token] = len(trigrams)
            for trigram in trigrams:
                postings.setdefault(trigram, set()).add(token)
        self._trigram_postings: Dict[str, FrozenSet[str]] = {
            trigram: frozenset(tokens) for trigram, tokens in postings.items()}

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

    def related_views(self, entity_id: int) -> List[KgEntity]:
        """The sql_views template entities whose ``reads_from`` edge
        lands on this entity (it is the edge's OBJECT — the relation
        being read): deduped, name-sorted. The service's related-sql-
        views block reads this — a hub table's reading templates
        (100+ for the classification tables) drown in walk fan-out, so
        the block answers deterministically instead."""
        views: Dict[int, KgEntity] = {}
        for edge, other_id, is_subject in self._adjacency.get(entity_id, ()):
            if edge.relation != READS_FROM or is_subject:
                continue
            other = self._entities.get(other_id)
            if other is not None and other.entity_type == "view":
                views[other.entity_id] = other
        return sorted(views.values(), key=lambda entity: entity.name)

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

    # -- the serving-scope derivation -------------------------------------

    def scoped(self, allowed: Collection[str],
               allowed_schemas: Optional[Collection[str]] = None,
               ) -> "KnowledgeGraph":
        """The FORCED-TABLE-SCOPE subgraph: the entities *allowed* names
        (schema-qualified ``analysis.mov_ave_rsi`` or bare
        ``mov_ave_rsi``, case-insensitive) and — when *allowed_schemas*
        rides — every schema-qualified entity UNDER one of those
        schemas (``stats`` keeps ``stats.*`` tables and DB views;
        slash-path sql_views templates have no schema dot and ride only
        via the attachment pass below), plus — pass two — every
        NON-table entity riding an edge whose other members all kept
        (the ns0 ``view`` templates/DB views reading a kept table, the
        ns2 label domains typing one), with the edges pruned to the
        survivors (no ghost endpoints). Entity/edge ids are preserved,
        so a scoped graph's chain logs and replays resolve against the
        same rows; the ``entity_digest`` is the subset's own, so scoped
        chains never cross between scopes or into the unscoped graph's.
        A namespace with no table entities (ns1, the prompt catalog)
        yields an empty graph by construction — the service exempts ns1
        from the table scope instead of calling this there. Empty
        *allowed*/*allowed_schemas* returns the graph unchanged (no
        limitation)."""
        tokens = {str(name).strip().casefold() for name in allowed
                  if str(name).strip()}
        schema_tokens = {str(name).strip().casefold() for name in
                         (allowed_schemas or ()) if str(name).strip()}
        if not tokens and not schema_tokens:
            return self
        kept: Dict[int, KgEntity] = {}
        for entity in self._entities.values():
            folded = entity.name.casefold()
            if folded in tokens or entity.bare_name.casefold() in tokens:
                kept[entity.entity_id] = entity
                continue
            if schema_tokens:
                dot = folded.rfind(".")
                if dot > 0 and folded[:dot] in schema_tokens:
                    kept[entity.entity_id] = entity
        # Pass two: a non-table entity rides when ONE edge names it and
        # every other member of that edge is kept — a view template
        # reading a kept table, a label domain typing one. A still-
        # unknown TABLE member never rides (the scope's whole point).
        for edge in self._edges.values():
            unknown = [member for member in edge.member_ids
                       if member not in kept]
            if len(unknown) != 1:
                continue
            rider = self._entities.get(unknown[0])
            if rider is not None and rider.entity_type != "table":
                kept[rider.entity_id] = rider
        return KnowledgeGraph(
            entities=kept.values(),
            edges=(edge for edge in self._edges.values()
                   if all(member in kept for member in edge.member_ids)))

    # -- rung 1: seed recognition ---------------------------------------

    def fuzzy_match(self, token: str) -> Optional[Tuple[str, float]]:
        """The FUZZY lane of the seed rung: the index token *token* is
        closest to, with their character-trigram similarity
        (:func:`~llm_agents.kg.graph.vocabulary.trigram_similarity`).
        ``None`` when *token* is not fuzzy-eligible (not a latin word of
        :data:`~llm_agents.kg.graph.vocabulary.FUZZY_MIN_LEN`+ chars) or
        nothing clears
        :data:`~llm_agents.kg.graph.vocabulary.FUZZY_SIMILARITY_MIN`.
        Ties break toward the RARER token (the smaller bucket — the more
        specific vocabulary), then lexically: deterministic. Only ever
        asked for a token with NO exact bucket — an exact match always
        wins."""
        if not is_fuzzy_token(token):
            return None
        probe = char_trigrams(token)
        shared: Dict[str, int] = {}
        for trigram in probe:
            for candidate in self._trigram_postings.get(trigram, ()):
                shared[candidate] = shared.get(candidate, 0) + 1
        best: Optional[Tuple[float, int, str]] = None
        for candidate, count in shared.items():
            # shared / union <= shared / max(len) — a cheap bound that
            # skips the candidates no similarity could rescue.
            widest = max(len(probe), self._trigram_counts[candidate])
            if count < FUZZY_SIMILARITY_MIN * widest:
                continue
            similarity = count / (len(probe)
                                  + self._trigram_counts[candidate] - count)
            if similarity < FUZZY_SIMILARITY_MIN:
                continue
            key = (-similarity, len(self._index[candidate]), candidate)
            if best is None or key < best:
                best = key
        return None if best is None else (best[2], -best[0])

    def plural_stem(self, token: str) -> Optional[str]:
        """The exact index token an English PLURAL of one strips to
        (``labels`` → ``label``, ``calls`` → ``call``, ``indexes`` →
        ``index``): the ``s`` ending first, then ``es``. ``None`` when
        *token* is not fuzzy-eligible or neither strip is an index
        token — a word that merely ends in ``s`` (``analysis``,
        ``status``) strips to nothing and stays unmatched. Asked only for
        a token with NO exact bucket."""
        if not is_fuzzy_token(token) or not token.endswith("s"):
            return None
        for stem in (token[:-1], token[:-2]):
            if stem in self._index:
                return stem
        return None

    def _resolve_tokens(self, tokens: Sequence[str],
                        ) -> List[ResolvedToken]:
        """Each query token against the token index: its exact bucket at
        factor 1.0, or — with no exact bucket — the fuzzy lane: the
        index token its plural strips to (similarity 1.0), else the
        closest vocabulary token by trigram similarity, at factor
        ``FUZZY_DAMP × similarity``. Skipped when that vocabulary token
        is ALSO an exact query token (the ask already covers it).
        Tokens matching neither way drop out."""
        token_set = frozenset(tokens)
        resolved: List[ResolvedToken] = []
        for token in tokens:
            bucket = self._index.get(token)
            if bucket:
                resolved.append(ResolvedToken(token, bucket, 1.0, None))
                continue
            stem = self.plural_stem(token)
            fuzzy = ((stem, 1.0) if stem is not None
                     else self.fuzzy_match(token))
            if fuzzy is None or fuzzy[0] in token_set:
                continue
            matched, similarity = fuzzy
            resolved.append(ResolvedToken(
                token, self._index[matched], FUZZY_DAMP * similarity,
                matched))
        return resolved

    def _seed_from(self, resolved: Sequence[ResolvedToken],
                   ) -> Dict[int, SeedHit]:
        """The lexical seed rung over resolved tokens: per token, the
        entities its bucket holds, weighted by the face (name > tags >
        description > type), by the token's IDF (rarity over the entity
        population — 指数 cannot outvote rsi) and by the token's factor
        (1.0 exact; damped for a fuzzy match)."""
        n = max(1, len(self._entities))
        scores: Dict[int, float] = {}
        evidence: Dict[int, Dict[str, str]] = {}
        for item in resolved:
            bucket = item.bucket
            # Unigram CJK tokens are noisier than bigrams/words: they
            # match at half strength (a shared single character is
            # weak evidence of aboutness).
            weight_scale = (_UNIGRAM_FACTOR
                            if _CJK_RE.fullmatch(item.token) else 1.0)
            idf = 1.0 + (n - len(bucket)) / n
            for entity_id, face_weight in bucket.items():
                contribution = (face_weight * weight_scale * idf
                                * item.factor)
                scores[entity_id] = (scores.get(entity_id, 0.0)
                                     + contribution)
                kind = ("name" if face_weight >= FACE_NAME else
                        "tag" if face_weight >= FACE_TAG else
                        "description" if face_weight >= FACE_DESC
                        else "type")
                # A fuzzy hit says what it matched: `momemtum[name≈momentum]`.
                evidence.setdefault(entity_id, {})[item.token] = (
                    f"{kind}≈{item.matched}" if item.matched else kind)
        hits: Dict[int, SeedHit] = {}
        for entity_id, score in scores.items():
            entity = self._entities.get(entity_id)
            damp = (seed_type_damp(entity.entity_type, entity.name)
                    if entity else 1.0)
            terms = tuple(sorted(evidence[entity_id].items()))
            hits[entity_id] = SeedHit(score=score * damp, terms=terms)
        return hits

    def seed_scores(self, tokens: Sequence[str]) -> Dict[int, SeedHit]:
        """The lexical seed rung: per query token, the entities whose
        faces carry it, weighted by the face (name > tags >
        description > type) and by the token's IDF (rarity over the
        entity population — 指数 cannot outvote rsi). A token with no
        exact bucket seeds through the FUZZY lane when a vocabulary
        token is close enough (:meth:`fuzzy_match`), strictly below the
        exact hit. Returns entity → accumulated score + the (token,
        best-face) evidence pairs."""
        return self._seed_from(self._resolve_tokens(tokens))

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
        resolved = self._resolve_tokens(tokens)
        seeds = self._seed_from(resolved)
        walked = self.walk(seeds, tokens, max_hops=hops)
        max_seed = max((hit.score for hit in seeds.values()), default=0.0)
        # The walk's OWN scale: the strongest activation among the
        # PROPAGATED nodes — seeds excluded. A seed's activation IS its
        # lexical score (tens on the live graph), and ``walk`` records
        # seeds too (they receive energy from their neighbours), so
        # normalizing the propagated rows by a seed's activation
        # flattened every walk score toward zero — measured 2026-11-23:
        # 0.02–0.27 on 137 live queries against the ~0..1 this rank rung
        # documents — and the walk relevance floor (a fraction of the
        # raw seed top) then pruned EVERY propagated row to the
        # _WALK_MIN in 100% of them: the spreading-activation rung
        # contributed exactly two nodes, whatever the query. On the walk
        # scale the strongest propagated row scores ACT_BLEND and the
        # floor grades as designed (see the floor below).
        max_act = max((hit.activation for entity_id, hit
                       in walked.items() if entity_id not in seeds),
                      default=0.0)
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
        n_seeds = len(seed_rows)
        seed_floor = seed_rows[0][0] * _SEED_FLOOR if seed_rows else 0.0
        if seed_rows:
            kept = [row for row in seed_rows if row[0] >= seed_floor]
            seed_rows = kept if len(kept) >= _SEED_MIN \
                else seed_rows[:_SEED_MIN]
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
        # The activation relevance floor, judged on the GLOBAL scale:
        # a fraction (_WALK_FLOOR) of the OVERALL top — the raw seed top
        # (how specifically the ask NAMES its tables) or the top walk
        # score — separates real enrichment from decay noise. The two
        # scales meet as designed: walk scores top out at ACT_BLEND (the
        # strongest propagated row), so a strongly named ask (seed top
        # above ACT_BLEND / _WALK_FLOOR) keeps its result FOCUSED — its
        # propagated rows all fall under the floor and _WALK_MIN keeps
        # the two strongest — while a vaguely named one surfaces more of
        # its neighborhood. Min _WALK_MIN kept whenever walk rows exist.
        walk_rows.sort(key=lambda item: (-item[0], item[1].name))
        n_walk = len(walk_rows)
        walk_floor = 0.0
        if walk_rows:
            global_top = max(seed_rows[0][0] if seed_rows else 0.0,
                             walk_rows[0][0])
            walk_floor = global_top * _WALK_FLOOR
            kept = [row for row in walk_rows if row[0] >= walk_floor]
            walk_rows = kept if len(kept) >= _WALK_MIN \
                else walk_rows[:_WALK_MIN]
        # SEEDS FIRST, then the propagated rows — both classes already
        # in their own score order. Structural, not a score comparison:
        # a table that NAMES the ask is never outranked by connectivity
        # alone (a propagated row carries no lexical part, so its score
        # is pure activation).
        ranked = seed_rows + walk_rows
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
            n_edges=len(self._edges),
            trace=SearchTrace(
                n_tokens=len(set(tokens)), n_seeds=n_seeds,
                n_seed_kept=len(seed_rows),
                n_seed_pruned=n_seeds - len(seed_rows),
                n_walk=n_walk, n_walk_kept=len(walk_rows),
                n_walk_pruned=n_walk - len(walk_rows),
                n_fuzzy_tokens=sum(1 for item in resolved if item.is_fuzzy),
                seed_top=seed_rows[0][0] if seed_rows else 0.0,
                walk_top=walk_rows[0][0] if walk_rows else 0.0,
                seed_floor=seed_floor, walk_floor=walk_floor))

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
                     max_nodes: int = 96,
                     ) -> Tuple[List[KgEntity], List[KgEdge],
                                List[KgEntity], List[str]]:
        """The expand face: resolve *names* (exact / unique bare /
        unique suffix), BFS their ≤hops neighborhoods, and return
        ``(resolved, edges, neighbors, unresolved)`` — the edges are
        every edge with ALL members inside the collected set.

        The node budget exists to bound a hops-2 BFS, not to clip a
        hub's own edges: the trim keeps every partner of a RESOLVED
        core before anything else (the face's contract is "every edge
        the named node rides" — 2026-11-23, after the ns0 hubs grew
        past the old 24 cap and the alphabetical trim silently dropped
        their joins_on/computed_from partners), then fills the rest by
        name. The default covers the largest live hub
        (stats.sec_classification: 91 edge ends + the core = 92)."""
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
        budget = max(1, max_nodes)
        if len(collected) > budget:
            keep = {entity.entity_id for entity in resolved}
            core_partners: List[int] = []
            for entity in resolved:
                for _, other, _ in self._adjacency.get(
                        entity.entity_id, ()):
                    if other not in keep and other not in core_partners:
                        core_partners.append(other)
            for entity_id in core_partners:
                if len(keep) >= budget:
                    break
                keep.add(entity_id)
            for entity_id in sorted(
                    collected - keep,
                    key=lambda i: self._entities[i].name):
                if len(keep) >= budget:
                    break
                keep.add(entity_id)
            collected = keep
        neighbors = [self._entities[entity_id]
                     for entity_id in sorted(collected)
                     if entity_id in self._entities]
        edges = [edge for edge in self._edges.values()
                 if all(member in collected for member in edge.member_ids)]
        return resolved, edges, neighbors, unresolved
