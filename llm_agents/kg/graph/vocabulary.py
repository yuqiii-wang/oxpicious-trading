"""llm_agents.kg.graph.vocabulary — the MATCH VOCABULARY and the
tuning constants of the traversal. Query side and index side share one
folding + tokenization (``normalize`` / ``extract_tokens``); every
magic number of the pipeline lives here — the face weights (WHERE a
term matches ranks HOW strongly), the spreading-activation factors,
the relevance floors, and the packing/render budgets."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

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
# top); propagated nodes are judged on the GLOBAL scale — a blended
# walk score is already ≤ ~1, so a fraction of the OVERALL top (seeds
# keep raw lexical scores) is what separates real enrichment from
# decay noise. Each class keeps a small minimum so a result never
# collapses.
_SEED_FLOOR = 0.10
_WALK_FLOOR = 0.02
_SEED_MIN = 3
_WALK_MIN = 2

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
