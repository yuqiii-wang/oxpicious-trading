"""analysis.text_metrics.fetch — raw-input SQL reads.

Fetch-only DB reads for the text-metrics compute (every computation
lives in the compute package); the SELECTs render from
database/sql_views/analysis/text_metrics/fetch/ at call time — the view
tree mirrors this module (sql_views README convention). Dates ride as
epoch float8 (the cudf DB-read convention — python-date columns poison
frames) and convert at the frame boundary via ``epoch_col_to_dt64``.

Both reads are FULL-HISTORY (no from_date window — see config:
the corpus is the working set, and article-day windows make a bounded
lookback unsound for sparse industries).
"""
from __future__ import annotations

import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import epoch_col_to_dt64
from _common.sql_views import render_sql_view

from analysis.text_metrics.config import (
    KW_BEAR_CODES,
    KW_BULL_CODES,
    KW_CATEGORY_MARKET,
)


async def fetch_articles(conn) -> pd.DataFrame:
    """Per-article sentiment panel: every text.news row LEFT JOINed with
    its digestion's sentiment_level. Columns
    [industry_id, date(datetime64[us]), digestible, sentiment_level].
    """
    view = render_sql_view("analysis/text_metrics/fetch/articles")
    rows = await conn.fetch(view.sql)
    if not rows:
        return pd.DataFrame(
            {"industry_id": [], "date": [], "digestible": [],
             "sentiment_level": []})
    df = pd.DataFrame(rec_cols(rows), columns=[
        "industry_id", "date_epoch", "digestible", "sentiment_level"])
    df["date"] = epoch_col_to_dt64(df.pop("date_epoch"), index=df.index)
    df["digestible"] = df["digestible"].astype("int64")
    df["sentiment_level"] = df["sentiment_level"].astype("float64")
    return df


async def fetch_keyword_tone(conn) -> pd.DataFrame:
    """Per-(industry_id, date) bullish/bearish kw_market mention sums:
    text.news_keywords ⋈ static.keyword_maps (category_type = market),
    negation already handled upstream at extraction time. Columns
    [industry_id, date(datetime64[us]), bull_mentions, bear_mentions].
    """
    view = render_sql_view(
        "analysis/text_metrics/fetch/keyword_tone",
        bull_codes=list(KW_BULL_CODES),
        bear_codes=list(KW_BEAR_CODES),
        kw_category_market=int(KW_CATEGORY_MARKET),
    )
    rows = await conn.fetch(view.sql, *view.params)
    if not rows:
        return pd.DataFrame(
            {"industry_id": [], "date": [], "bull_mentions": [],
             "bear_mentions": []})
    df = pd.DataFrame(rec_cols(rows), columns=[
        "industry_id", "date_epoch", "bull_mentions", "bear_mentions"])
    df["date"] = epoch_col_to_dt64(df.pop("date_epoch"), index=df.index)
    df["bull_mentions"] = df["bull_mentions"].astype("int64")
    df["bear_mentions"] = df["bear_mentions"].astype("int64")
    return df
