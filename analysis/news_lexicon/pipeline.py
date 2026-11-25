"""analysis.news_lexicon.pipeline — the entry pipeline class.

``NewsLexiconAnalysis`` (a :class:`DataAnalysis` subclass): incremental
by the missing-article anti-join, --force truncates + re-scores the
whole corpus. Runs AFTER builds.text has digested the day's articles
and BEFORE analysis.news_text_daily reads the scores.
"""
from __future__ import annotations

from _common.data_analysis import DataAnalysis
from _common.db_commons import analyze_tables_async
from analysis.news_lexicon.config import DESCRIPTION, LEXICON_TABLE
from analysis.news_lexicon.steps import LexiconScoreStep


class NewsLexiconAnalysis(DataAnalysis):
    """``python -m analysis.news_lexicon`` — CFSD lexicon scoring."""

    title = "NEWS LEXICON (CFSD scoring)"
    description = DESCRIPTION
    component = "news_lexicon"

    def add_arguments(self, ap) -> None:
        self.add_force_arg(ap)

    def header_fields(self) -> dict:
        return {
            "mode": (
                "FORCE (scores truncated, whole corpus re-scored)"
                if self.args.force
                else "incremental (digested articles missing a score)"
            ),
        }

    async def run(self) -> None:
        conn = self.conn
        if self.args.force:
            await self.truncate_tables([LEXICON_TABLE])

        written = await LexiconScoreStep().run(conn)
        if written:
            await analyze_tables_async(conn, [LEXICON_TABLE])
