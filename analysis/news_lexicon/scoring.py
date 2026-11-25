"""The CFSD unit scorer (analysis.news_lexicon.scoring).

Implements the CFSD paper's dictionary rule (姜富伟/孟令超/唐国豪,
媒体文本情绪与股票回报预测, 经济学(季刊) 2021) over the seeded
static.fin_sentiment_dict vocabulary:

  a SENTIMENT UNIT = the tokens from just after the previous sentiment
  word up to and including the current one; each unit contributes

      (-1) ** n_negators_in_unit * W_polarity * degree_multiplier

  where W is +1 (positive) / -1 (negative) / +0.5 (the 21 dual-sign
  'both' words — half weight either sign), n counts NEGATOR tokens
  inside the unit before the word (不/未/难以/尚未/并非/未必 …), and
  the degree multiplier is the NEAREST degree-adverb token before the
  word within the unit (极其/大幅 x2, 很/明显 x1.5, 略微/稍微 x0.5,
  default 1.0).

  raw = SUM(units) / n_units  in [-1, +1];  lex_score = clip(raw * 5, -5, +5)

An article with no dictionary word scores 0.0 with n_units = 0 (no
evidence — never disagrees). This is pure host python over the
segmenter's token lists — the corpus is ~25k articles, no frame rides
through here.
"""
from __future__ import annotations

from dataclasses import dataclass

from analysis.news_lexicon.config import (
    DEGREE_TIERS,
    NEGATORS,
    SCORE_SCALE,
)

_POLARITY_WEIGHTS: dict[int, float] = {0: -1.0, 1: 1.0, 2: 0.5}


@dataclass(frozen=True)
class UnitScore:
    """One article's raw lexicon score + its unit counters."""

    raw: float          # SUM(units)/n_units in [-1,+1]; 0.0 when no units
    n_units: int        # sentiment words found
    neg_flips: int      # total negator occurrences inside units
    degree_hits: int    # units whose contribution carried a degree multiplier


class LexiconScorer:
    """Token-list scorer over the CFSD dictionary (see module docstring)."""

    def __init__(self, polarity_by_word: dict[str, int]) -> None:
        self._weight: dict[str, float] = {
            w: _POLARITY_WEIGHTS[p] for w, p in polarity_by_word.items()
        }

    def score_tokens(self, tokens: list[str]) -> UnitScore:
        """One segmented article -> its UnitScore."""
        units = 0
        flips = 0
        degree_hits = 0
        total = 0.0
        unit_start = 0  # index just after the previous sentiment word
        for i, tok in enumerate(tokens):
            weight = self._weight.get(tok)
            if weight is None:
                continue
            units += 1
            negs = 0
            degree = 1.0
            for j in range(unit_start, i):
                prev = tokens[j]
                if prev in NEGATORS:
                    negs += 1
                elif prev in DEGREE_TIERS:
                    degree = DEGREE_TIERS[prev]
            if negs:
                flips += negs
            if degree != 1.0:
                degree_hits += 1
            contribution = ((-1.0) ** negs) * weight * degree
            total += contribution
            unit_start = i + 1
        raw = total / units if units else 0.0
        return UnitScore(
            raw=max(-1.0, min(1.0, raw)),
            n_units=units,
            neg_flips=flips,
            degree_hits=degree_hits,
        )

    def scaled(self, score: UnitScore) -> float:
        """raw -> the digestion scale [-5,+5], clipped."""
        return round(max(-SCORE_SCALE, min(SCORE_SCALE, score.raw * SCORE_SCALE)), 3)
