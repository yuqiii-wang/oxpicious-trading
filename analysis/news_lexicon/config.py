"""Config for analysis.news_lexicon — the CFSD lexicon scoring pipeline.

One output table in the text schema (DDL: database/sql/text/
news_lexicon_scores.sql):
  text.news_lexicon_scores — per digested article: the CFSD dictionary
  score, its unit counters, the disagreement flag vs the LLM digestion
  score, and the dict/engine version pins.
"""
from __future__ import annotations

ANALYSIS_NAME = "news_lexicon"
DESCRIPTION = (
    "CFSD lexicon scoring: scores every digested article's text with the "
    "static.fin_sentiment_dict dictionary (unit-based, negation-aware, "
    "degree-adverb tiers — the CFSD paper's rule, 姜富伟等 2021) and "
    "flags the disagreement against the article's LLM digestion score "
    "(sign mismatch AND |llm - lex| >= 2). The disagreement halves the "
    "article's weight in the daily news-text blend "
    "(analysis.news_text_daily)."
)

# --- Tables ------------------------------------------------------------------

LEXICON_TABLE = "text.news_lexicon_scores"

# --- Version pins ------------------------------------------------------------

#: The dictionary seed state these scores were built against
#: (database/sql/static/fin_sentiment_dict/ git state). Bump + re-score
#: (--force) whenever the dictionary seeds change.
DICT_VERSION = "cfsd-2026-11-21"

#: The scorer code version (negator/degree vocabularies + unit rules
#: below). A recalibration bumps this and re-scores --force.
ENGINE_VERSION = "v1-2026-11-22"

# --- Scoring knobs -----------------------------------------------------------

#: Scoring text = title + "。" + content, capped — bounds segmenter latency
#: on the few very long bodies; the CFSD unit rule is a share-of-units
#: measure, so truncation biases long articles less than a raw count would.
TEXT_CAP_CHARS = 3000

#: The raw unit share (SUM (-1)^n * W * deg / n_units) lives in [-1, +1];
#: the digestion scale is [-5, +5]. Linear rescale, then clipped.
SCORE_SCALE = 5.0

#: Disagreement verdict: sign mismatch (strict — llm*lex < 0, zero never
#: counts) AND at least this many points apart on the [-5,+5] scale.
DISAGREE_DIFF = 2.0

#: Negation vocabulary (token-equality after StructBERT segmentation): a
#: negator inside a sentiment unit flips that unit's sign — the CFSD
#' (-1)^n rule (不排除 / 未达预期 / 难以持续 all flip or damp).
NEGATORS: frozenset[str] = frozenset({
    "不", "无", "没", "没有", "非", "莫", "未", "别",
    "无需", "不必", "难以", "尚未", "并非", "未必",
})

#: Degree-adverb tiers (token-equality): the nearest degree token BEFORE
#: the sentiment word within its unit scales that unit's contribution.
DEGREE_TIERS: dict[str, float] = {
    # strong — 极其/大幅-class
    "极其": 2.0, "异常": 2.0, "大幅": 2.0, "显著": 2.0, "极为": 2.0,
    "十分": 2.0, "非常": 2.0, "格外": 2.0, "特别": 2.0, "高度": 2.0,
    "严重": 2.0, "百分百": 2.0, "倍加": 2.0,
    # mid — 很/明显-class
    "很": 1.5, "较为": 1.5, "明显": 1.5, "更加": 1.5, "越发": 1.5,
    "相当": 1.5, "颇": 1.5, "持续": 1.5,
    # weak — 略微-class
    "略微": 0.5, "稍微": 0.5, "略有": 0.5, "略": 0.5, "稍": 0.5,
    "稍稍": 0.5, "轻微": 0.5,
}
