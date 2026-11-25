"""llm_agents.kg.graph.vocabulary — the MATCH VOCABULARY and the
tuning constants of the traversal. Query side and index side share one
folding + tokenization (``normalize`` / ``extract_tokens``); every
magic number of the pipeline lives here — the face weights (WHERE a
term matches ranks HOW strongly), the spreading-activation factors,
the relevance floors, and the packing/render budgets."""
from __future__ import annotations

import re
from typing import Dict, FrozenSet, List, Tuple

from llm_agents.kg.graph.values import KgEdge

# ---------------------------------------------------------------------------
# Tokenization: normalization + the token classes (query side and index
# side share it)
# ---------------------------------------------------------------------------

# CJK ideographs (the tsquery lexeme classes of the MCP readonly server).
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_LATIN_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase with separator runs (-/_//.) folded to single spaces —
    the same vocabulary folding as ``planning.prompt_loading`` (a tag
    'ma-spread' and a query 'MA spread' share one face)."""
    return re.sub(r"[\s/\-_.]+", " ", str(text or "").lower()).strip()


def extract_tokens(text: str) -> List[str]:
    """The match vocabulary of one text: latin/digit words (≥2 chars)
    plus every CJK bigram of each CJK run (a 1-char run yields the
    unigram). Bigrams give segmenter-free Chinese recall: any two
    adjacent shared characters is a hit, exact word boundaries are not
    required."""
    tokens: List[str] = []
    folded = normalize(text)
    for word in _LATIN_WORD_RE.findall(folded):
        if len(word) >= 2:
            tokens.append(word)
    for run in _CJK_RUN_RE.findall(folded):
        if len(run) == 1:
            tokens.append(run)
            continue
        tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def is_fuzzy_token(token: str) -> bool:
    """Whether *token* takes part in the fuzzy lane: a latin/digit word
    of at least :data:`FUZZY_MIN_LEN` characters that carries a letter
    (an index token or a query token — both arrive :func:`normalize`d).
    Pure digits are identifiers and never take part."""
    return (len(token) >= FUZZY_MIN_LEN
            and _FUZZY_TOKEN_RE.fullmatch(token) is not None)


def char_trigrams(token: str) -> FrozenSet[str]:
    """A word's character trigrams, pg_trgm-padded (two leading spaces,
    one trailing): ``'momentum'`` → ``{'  m', ' mo', 'mom', …}``."""
    padded = f"  {token} "
    return frozenset(padded[index:index + 3]
                     for index in range(len(padded) - 2))


def trigram_similarity(left: FrozenSet[str], right: FrozenSet[str]) -> float:
    """Jaccard over two trigram sets — pg_trgm's ``similarity()``."""
    shared = len(left & right)
    if not shared:
        return 0.0
    return shared / (len(left) + len(right) - shared)


# ---------------------------------------------------------------------------
# Matching weights: face weights + the CJK unigram dampener
# ---------------------------------------------------------------------------

# Face weights: WHERE a term matches ranks how strongly the entity IS
# about the query. Tags are the trader's own vocabulary (and the KG's
# GIN-indexed lookup face) — above description, below the name itself.
FACE_NAME = 3.0
FACE_TAG = 2.2
FACE_DESC = 0.8
FACE_TYPE = 0.5
# A lone CJK character is ambiguous — it matches at half weight.
_UNIGRAM_FACTOR = 0.5

# -- the fuzzy lane (character trigrams, 2026-11-23) ----------------------
# A query token with NO exact index bucket may still be a plural or a
# misspelling of an authored one (labels → label, momemtum → momentum):
# exact matching made that a total miss. The seed rung then resolves it
# through the FUZZY lane — first the English plural strip (``s`` / ``es``
# → an exact index token, similarity 1.0: ``calls`` → ``call``), else the
# closest index token by CHARACTER-TRIGRAM similarity, the pg_trgm shape
# (the word padded with two leading and one trailing space, similarity =
# shared trigrams / their union). Only latin/digit words of at least
# FUZZY_MIN_LEN characters take part on either side (shorter words carry
# too few trigrams to mean anything; CJK bigrams are left alone), a
# trigram match must clear FUZZY_SIMILARITY_MIN, and every fuzzy hit
# scores strictly BELOW the exact hit of the same face — the face weight
# × FUZZY_DAMP × the similarity.
#
# FUZZY_SIMILARITY_MIN = 0.58 is the measured cut (temp_scripts/
# kg_lexical_golden.py, variant group vs negative group): the true
# misspellings cluster at 0.58–0.77, while the FALSE attachments sit at
# or below 0.571 (pasta → past on a true-negative ask, contrast →
# contract inside a real query) — and nothing string-based separates a
# one-letter typo from a one-letter collision, so the cut is placed
# between them. Two real misspellings score 0.500 and stay unrecovered
# (momemtum, sanbox); lowering the threshold to 0.50 recovers them AND
# lets the two false attachments through.
FUZZY_MIN_LEN = 4
FUZZY_SIMILARITY_MIN = 0.58
FUZZY_DAMP = 0.5
# A latin/digit word carrying at least one LETTER — pure digits are
# identifiers (index codes, years) and never fuzzy-matched: 000001 and
# 000300 share most of their trigrams and are different indices.
_FUZZY_TOKEN_RE = re.compile(r"(?=[a-z0-9]*[a-z])[a-z0-9]+")

# Seed damping for the read-side sql_views templates: a template
# matching the query is weaker "IS about the query" evidence than a
# table — the corpus is locator-lane enrichment (the related-sql-views
# block and the walk carry it), so the damp keeps tables primary in
# the ranking instead of letting 400+ template name tokens crowd them
# out. The corpus's own name shape (slash loader path — the static
# rule's convention) distinguishes it from the hand-seeded schema-
# qualified DB views (stats.v_*), which stay FULL-weight derived
# objects; entity_type 'view' alone is shared by both kinds.
_READ_SIDE_NAME_MARK = "/"


def seed_type_damp(entity_type: str, name: str) -> float:
    """The seed-score multiplier for one entity (1.0 default): the
    read-side sql_views templates — 'view' entities whose name is a
    slash path — match at half strength."""
    if entity_type == "view" and _READ_SIDE_NAME_MARK in name:
        return 0.5
    return 1.0


# ---------------------------------------------------------------------------
# Spreading-activation constants
# ---------------------------------------------------------------------------

# Per-hop energy decay, the per-hop frontier bound, the sub-relation
# agreement boost, and the final blend between connectivity
# (activation) and lexical evidence.
HOP_DECAY = 0.55
WALK_BEAM = 32
SUBRELATION_BOOST = 1.6
ACT_BLEND = 0.55
# A propagated node with NO lexical tie to the query is dampened when
# it is also a hub (degree above this) — pure-connectivity hubs sink.
_HUB_DEGREE = 8
_HUB_FACTOR = 0.5
# The RELEVANCE FLOOR: results scale with the question's specificity.
# The seed tail a top seed dwarfs drops out (_SEED_FLOOR of the seed
# top). Propagated nodes are judged on the GLOBAL scale: their blended
# score is activation on the WALK's OWN scale (the strongest propagated
# row scores ACT_BLEND — a propagated node carries no lexical part),
# and _WALK_FLOOR of the OVERALL top (the raw seed top, i.e. how
# specifically the ask NAMES its tables, or the top walk score) is what
# separates real enrichment from decay noise. An ask whose top seed
# scores above ACT_BLEND / _WALK_FLOOR therefore keeps only the
# _WALK_MIN strongest propagated nodes (focused); a vaguely named one
# surfaces more of its neighborhood. Each class keeps a small minimum
# so a result never collapses. (2026-11-23: the walk scale used to be
# the max activation over seeds TOO — a seed's activation is its raw
# lexical score, tens — which flattened every walk score to 0.02–0.27
# and held every measured query at the minimum; see engine.search.)
_SEED_FLOOR = 0.10
_WALK_FLOOR = 0.02
_SEED_MIN = 3
_WALK_MIN = 2

# The ns0 read-side corpus relation: a sql_views template → each base
# relation its text reads (head-first [view, table]; NULL sub_relation).
# The edges are DERIVED, not seeded — llm_agents/kg/static_rules/
# parses the templates (references resolved against pg_class); the
# constant is shared by the engine (factors + related_views) and the
# rule that writes the edges.
READS_FROM = "reads_from"

# Per-(relation, is-subject) propagation factors. is_subject: the
# walking node is entity_ids[0] (the head). Both directions walk —
# "what feeds this table" and "what does this table feed" are both
# live questions — but with different strength.
_EDGE_FACTORS: Dict[Tuple[str, bool], float] = {
    # The family hint: two tables indicating the same concept.
    ("similar_to", True): 1.0,
    ("similar_to", False): 1.0,
    # The practical SQL composition path — a join key two tables share
    # is a strong "these combine" signal, either direction.
    ("joins_on", True): 0.9,
    ("joins_on", False): 0.9,
    # The one-of-a-kind hint: rows the picker offers as ONE mutually
    # exclusive choice (the study-scope family) — as strong a family
    # tie as similar_to.
    ("alternates_with", True): 1.0,
    ("alternates_with", False): 1.0,
    # Derived table → its source: the provenance read.
    ("computed_from", True): 0.75,
    # Source → its derivations: the "what do people compute from
    # this" read — the more common agent question.
    ("computed_from", False): 0.85,
    # The sql_views corpus hop (READS_FROM): template → the base
    # relation it reads is the provenance read (computed_from's
    # strength); relation → its reading templates answers "which
    # read-side queries serve this table" — the common agent question,
    # walking outward as strong as a source→derivations read.
    ("reads_from", True): 0.75,
    ("reads_from", False): 0.85,
    # The label-index hop (ns2): a table's typed SMALLINT column ↔ its
    # static.label_dim vocabulary — both directions are the lane's own
    # questions ("which tables carry this vocabulary" / "which
    # vocabulary types this table"), so it walks strong both ways.
    ("labeled_by", True): 0.85,
    ("labeled_by", False): 0.85,
    # Fact → its identity anchor: a grain hop into a hub.
    ("anchored_by", True): 0.45,
    # Identity → the fact tables on its grain: "what data exists".
    ("anchored_by", False): 0.85,
    ("documented_by", True): 0.7,
    ("documented_by", False): 0.7,
}
_DEFAULT_EDGE_FACTOR = 0.55


def edge_factor(edge: KgEdge, is_subject: bool) -> float:
    """The propagation weight of walking *edge* from the member that
    is (or is not) its subject — the single source ``walk`` propagates
    by and the edge-packing tiers order by (the strongest relation
    leads the join hints)."""
    return _EDGE_FACTORS.get((edge.relation, is_subject),
                             _DEFAULT_EDGE_FACTOR)


# ---------------------------------------------------------------------------
# Packing / render budgets
# ---------------------------------------------------------------------------

_EDGE_RENDER_CAP = 24
# The OUTWARD tier of the edge packing (edges from chosen nodes to
# partners the query did not name) has its own, smaller cap — the join
# hints without letting one broad result grow the whole block.
_EDGE_OUTWARD_CAP = 8
