"""analysis.news_text_daily.pipeline — the entry pipeline class.

``NewsTextDailyAnalysis`` (a :class:`DataAnalysis` subclass) owns the
run lifecycle: the trading calendar, the incremental target set
(missing article-bearing trade days + the GRACE_DAYS tail — late
digestions / lexicon scores correct the just-closed days), the shared
fetches, and the two scope steps' writes. The metric definitions and
their validated readings live in the DDL header and the __main__.py
docstring.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from _common.data_analysis import DataAnalysis
from _common.db_commons import (
    analyze_tables_async,
    get_existing_keys_async,
)
from _common.df_utils import host_array
from analysis.news_text_daily.config import (
    DESCRIPTION,
    GRACE_DAYS,
    MENTION_MIN_KW_CHARS,
    TABLE,
    WARMUP_CAL_DAYS,
)
from analysis.news_text_daily.fetch import (
    fetch_calendar_days,
    fetch_coverage_articles,
    fetch_keyword_tone,
    fetch_mention_articles,
    fetch_scored_articles,
    day_number_to_date,
)
from analysis.news_text_daily.steps import (
    IndustryEventStep,
    MarketMetricStep,
    SectorEventStep,
    blend_scores,
)

_EPOCH = date(1970, 1, 1)


def _as_day(entry) -> int:
    """get_existing_keys_async entry (date or 1-tuple) -> day number."""
    value = entry[0] if isinstance(entry, tuple) else entry
    return (value - _EPOCH).days


class NewsTextDailyAnalysis(DataAnalysis):
    """``python -m analysis.news_text_daily`` — the daily news-text panel."""

    title = "NEWS TEXT DAILY (sentiment state + flow flags)"
    description = DESCRIPTION
    component = "news_text_daily"

    def add_arguments(self, ap) -> None:
        self.add_force_arg(ap)

    def header_fields(self) -> dict:
        return {
            "mode": (
                "FORCE (panel truncated, full rebuild)"
                if self.args.force
                else f"incremental (missing article days + {GRACE_DAYS}-day tail)"
            ),
        }

    async def run(self) -> None:
        conn = self.conn
        if self.args.force:
            await self.truncate_tables([TABLE])

        cal_days = await fetch_calendar_days(conn)
        if len(cal_days) == 0:
            print("[news_text_daily] empty trading calendar — nothing to do")
            return

        # Target set: article-bearing trade days missing from the table,
        # plus the trailing grace window (recomputed EVERY run — late
        # digestions / lexicon scores correct the just-closed days).
        # The coverage probe loads from the OLDEST existing row so a
        # pipeline-down gap longer than the grace window still heals.
        existing = await get_existing_keys_async(conn, TABLE, ["date"])
        existing_days = {_as_day(entry) for entry in existing}
        probe_from_day = (
            int(cal_days[0])
            if self.args.force or not existing_days
            else min(existing_days)
        )
        coverage_probe = await fetch_coverage_articles(conn, probe_from_day)
        market_cov = coverage_probe[
            (coverage_probe["industry_id"] == "000001")
            & (coverage_probe["n_articles"] > 0)
        ]
        if market_cov.empty:
            print("[news_text_daily] text.news_coverage_audit has no "
                  "market coverage rows in scope — run "
                  "analysis.news_audit (coverage) first")
            return
        article_days = set(
            host_array(market_cov["day"]).astype("int64").tolist()
        )

        missing = article_days - existing_days
        max_day = max(article_days)
        grace = {
            d for d in article_days if d > max_day - GRACE_DAYS
        }
        targets = missing | grace
        if not targets:
            print("[news_text_daily] all article days up to date — "
                  "nothing to compute")
            return

        from_day = min(targets) - WARMUP_CAL_DAYS
        scored = blend_scores(await fetch_scored_articles(conn, from_day))
        if scored.empty:
            # Coverage rows exist but nothing digested yet (digestions
            # mid-rebuild / upstream crash) — the sentiment panel has no
            # basis. Bail with a message instead of writing empty
            # metrics; the grace-tail target set heals the days once
            # the digestion source repopulates.
            print("[news_text_daily] no digested articles in scope "
                  "— nothing to compute")
            return
        mentions = blend_scores(await fetch_mention_articles(
            conn, from_day, MENTION_MIN_KW_CHARS))
        coverage = await fetch_coverage_articles(conn, from_day)
        tone = await fetch_keyword_tone(conn)
        print(
            f"[news_text_daily] {len(targets)} target day(s) "
            f"({len(missing)} missing, {len(grace)} grace tail); fetched "
            f"{len(scored):,} scored / {len(mentions):,} co-mention / "
            f"{len(coverage):,} coverage article rows from "
            f"{day_number_to_date(from_day)}"
        )

        await MarketMetricStep().run(
            conn, scored, coverage, cal_days, tone, targets,
        )
        await IndustryEventStep().run(
            conn, mentions, cal_days, targets,
        )
        await SectorEventStep().run(
            conn, mentions, cal_days, targets,
        )
        await analyze_tables_async(conn, [TABLE])
