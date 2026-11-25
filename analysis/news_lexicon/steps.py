"""Steps for analysis.news_lexicon — the scoring write step.

Compute layout: the article texts are segmented in BATCHES through the
process-wide ChineseWordSegmenter (host-first: the resident local-model
host serves the batches when reachable, the in-process torch path is
the fallback), then scored in plain host python (the scorer is a pure
token walk — no frame rides through it). Only the row dicts at the
write boundary materialize (sanitize stays inside bulk_upsert_async).
"""
from __future__ import annotations

import logging

import pandas as pd
from pydantic import BaseModel

from _common.build_commons import rec_cols
from _common.db_commons import bulk_upsert_async
from _common.sql_views import render_sql_view
from _common.tokenization.chinese_word_segmenter import ChineseWordSegmenter
from analysis.news_lexicon.config import (
    DISAGREE_DIFF,
    DICT_VERSION,
    ENGINE_VERSION,
    LEXICON_TABLE,
    TEXT_CAP_CHARS,
)
from analysis.news_lexicon.scoring import LexiconScorer

logger = logging.getLogger(__name__)

#: segmentation batch size for the backfill-scale runs (the segmenter's
#: own host/in-process paths batch internally; this paces the logging)
SEGMENT_CHUNK = 512


class NewsLexiconScoreRow(BaseModel):
    """One row as upserted into text.news_lexicon_scores."""

    news_id: int
    lex_score: float
    n_units: int
    neg_flips: int
    degree_adj: int
    disagrees: bool
    llm_sentiment: float
    dict_version: str
    engine_version: str


class LexiconScoreStep:
    """Score every digested article missing a lexicon row.

    The incremental target set is the anti-join in the read template
    (digested articles with no text.news_lexicon_scores row) — after a
    --force truncate it returns the whole corpus, so one template serves
    both modes. Writes via bulk_upsert_async on news_id.
    """

    title = "text.news_lexicon_scores CFSD scoring"

    async def run(self, conn) -> int:
        articles = await self._fetch_articles(conn)
        if articles.empty:
            print(f"[{self.title}] every digested article already scored "
                  f"— nothing to do")
            return 0

        scorer = await self._build_scorer(conn)
        segmenter = ChineseWordSegmenter.get()

        texts = [
            (row.title + "。" + row.content)[:TEXT_CAP_CHARS]
            for row in articles.itertuples(index=False)
        ]
        token_lists: list[list[str]] = []
        for start in range(0, len(texts), SEGMENT_CHUNK):
            chunk = texts[start:start + SEGMENT_CHUNK]
            token_lists.extend(segmenter.segment_batch(chunk))
            print(f"[{self.title}] segmented "
                  f"{min(start + SEGMENT_CHUNK, len(texts)):,}/{len(texts):,}")

        rows: list[dict] = []
        for (idx, article), tokens in zip(articles.iterrows(), token_lists):
            score = scorer.score_tokens(tokens)
            lex = scorer.scaled(score)
            llm = float(article["sentiment_level"])
            # strict sign mismatch (zero never disagrees) + >= 2 points apart
            disagrees = (
                score.n_units > 0
                and llm * lex < 0
                and abs(llm - lex) >= DISAGREE_DIFF
            )
            rows.append(
                NewsLexiconScoreRow(
                    news_id=int(article["news_id"]),
                    lex_score=lex,
                    n_units=score.n_units,
                    neg_flips=score.neg_flips,
                    degree_adj=score.degree_hits,
                    disagrees=disagrees,
                    llm_sentiment=llm,
                    dict_version=DICT_VERSION,
                    engine_version=ENGINE_VERSION,
                ).model_dump()
            )

        written = await bulk_upsert_async(
            conn, LEXICON_TABLE, rows, key_columns=["news_id"],
        )
        disagrees_n = sum(1 for r in rows if r["disagrees"])
        print(f"[{self.title}] upserted {written:,} rows "
              f"({disagrees_n:,} disagreeing, "
              f"{sum(1 for r in rows if r['n_units'] == 0):,} with no "
              f"dictionary word)")
        return written

    async def _fetch_articles(self, conn) -> pd.DataFrame:
        view = render_sql_view("analysis/news_lexicon/digested_articles")
        rows = await conn.fetch(view.sql, *view.params)
        return pd.DataFrame(rec_cols(rows))

    async def _build_scorer(self, conn) -> LexiconScorer:
        view = render_sql_view("analysis/news_lexicon/dictionary")
        rows = await conn.fetch(view.sql, *view.params)
        polarity = {r["word"]: int(r["polarity"]) for r in rows}
        print(f"[{self.title}] dictionary loaded: {len(polarity):,} words")
        return LexiconScorer(polarity)
