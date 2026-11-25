"""Input reads for analysis.news_text_daily (sql_views tree
analysis/news_text_daily/).

Dates land as epoch float8 (the cudf DB-read convention) and stay in
int64 DAY-NUMBER space through the compute — the conversion to real
dates happens once at the write boundary (epoch_col_to_dt64). The
calendar-roll helpers live in analysis._common.calendar_roll (shared
with the coverage audit step).

The coverage basis is the text.news_coverage_audit table (built by
analysis.news_audit — the 2026-11-22 coverage migration): the panel no
longer scans text.news itself, it reads the audit's per-(trading-day,
industry) counts ('000001' = the whole-market rollup row). The
industry/sector scopes' article basis is the CO-MENTION frame
(mention_articles view — per (article, industry) substantial keyword
co-mentions joined through static.keyword_maps, the 2026-11-22
attribution rework that lets niche industries populate).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.build_commons import rec_cols
from _common.df_utils import host_array
from _common.sql_views import render_sql_view
from analysis._common.calendar_roll import day_number_to_date, day_numbers

# Each fetch's column contract (SELECT order): the typed scaffold an
# EMPTY result keeps, so downstream column access sees a typed empty
# frame instead of KeyError-ing on a columnless one (rec_cols returns
# {} for no rows — the 2026-11-21 'date_epoch' crash while the
# digestion source was mid-rebuild).
_SCORED_COLUMNS: dict[str, str] = {
    "news_id": "int64", "date_epoch": "float64", "industry_id": "object",
    "sector_id": "object", "source": "object", "sentiment_level": "float64",
    "lex_score": "float64", "disagrees": "int64",
}
_COVERAGE_COLUMNS: dict[str, str] = {
    "date_epoch": "float64", "industry_id": "object", "n_articles": "int64",
}
_MENTION_COLUMNS: dict[str, str] = {
    "news_id": "int64", "date_epoch": "float64", "industry_id": "object",
    "source": "object", "sentiment_level": "float64",
    "lex_score": "float64", "disagrees": "int64",
}
_CALENDAR_COLUMNS: dict[str, str] = {"date_epoch": "float64"}
_TONE_COLUMNS: dict[str, str] = {
    "date_epoch": "float64", "pos_mentions": "float64",
    "neg_mentions": "float64",
}


def _rows_frame(rows, dtypes: dict[str, str]) -> pd.DataFrame:
    """DB rows -> DataFrame carrying the fetch's column contract — a
    non-empty result materializes as-is (the cudf DB-read boundary);
    an EMPTY one keeps the typed scaffold instead of a columnless
    frame."""
    cols = rec_cols(rows)
    if cols:
        return pd.DataFrame(cols)
    return pd.DataFrame({c: pd.Series(dtype=d) for c, d in dtypes.items()})


async def fetch_scored_articles(conn, from_day: int) -> pd.DataFrame:
    """Digested non-fake articles with LLM + lexicon scores (blended
    downstream). Day-number int64 column ``day``; lex/disagrees nullable."""
    view = render_sql_view(
        "analysis/news_text_daily/daily_articles",
        from_date=day_number_to_date(from_day),
    )
    rows = await conn.fetch(view.sql, *view.params)
    df = _rows_frame(rows, _SCORED_COLUMNS)
    df["day"] = day_numbers(df["date_epoch"])
    return df.drop(columns=["date_epoch"])


async def fetch_mention_articles(conn, from_day: int, min_kw_chars: int) -> pd.DataFrame:
    """The industry/sector CO-MENTION frame: per (article, industry)
    substantial-mention rows (mention_articles view — the matched
    industry keywords joined through the keyword map, >= min_kw_chars
    total matched chars), with the score columns for the blend. One
    row per (news_id, industry_id); an article mentioning several
    industries appears once per industry. Day-number int64 column
    ``day``; lex/disagrees nullable."""
    view = render_sql_view(
        "analysis/news_text_daily/mention_articles",
        from_date=day_number_to_date(from_day),
        min_kw_chars=min_kw_chars,
    )
    rows = await conn.fetch(view.sql, *view.params)
    df = _rows_frame(rows, _MENTION_COLUMNS)
    df["day"] = day_numbers(df["date_epoch"])
    return df.drop(columns=["date_epoch"])


async def fetch_coverage_articles(conn, from_day: int) -> pd.DataFrame:
    """The coverage basis from text.news_coverage_audit (built by
    analysis.news_audit): per (trading day, industry_id) article counts,
    '000001' = the whole-market rollup row. Day-number int64 column
    ``day`` — already calendar-rolled by the audit (weekend articles sit
    on the next trading day), so NO roll_to_calendar pass here."""
    view = render_sql_view(
        "analysis/news_text_daily/coverage_audit",
        from_date=day_number_to_date(from_day),
    )
    rows = await conn.fetch(view.sql, *view.params)
    df = _rows_frame(rows, _COVERAGE_COLUMNS)
    df["day"] = day_numbers(df["date_epoch"])
    return df.drop(columns=["date_epoch"])


async def fetch_calendar_days(conn) -> np.ndarray:
    """The SSE Composite trading calendar as a sorted HOST int64 day-number
    array (the alignment core's left operand — unwrapped once)."""
    view = render_sql_view(
        "analysis/news_text_daily/index_calendar",
        index_code="000001",
    )
    rows = await conn.fetch(view.sql, *view.params)
    df = _rows_frame(rows, _CALENDAR_COLUMNS)
    days = day_numbers(df["date_epoch"])
    return np.sort(host_array(days).astype("int64"))


async def fetch_keyword_tone(conn) -> pd.DataFrame:
    """Daily up-family minus down-family kw_market mentions (date-joined
    to the calendar downstream). Columns day / tone_mentions; days with
    no matview row reindex to 0 downstream."""
    from _common.labels import KW_MARKET_CODE

    pos_codes = [
        KW_MARKET_CODE[k] for k in ("px_up", "sharp_up", "rebound")
    ]
    neg_codes = [
        KW_MARKET_CODE[k] for k in ("px_dn", "sharp_dn", "pullback", "break_dn")
    ]
    view = render_sql_view(
        "analysis/news_text_daily/keyword_tone",
        pos_codes=pos_codes,
        neg_codes=neg_codes,
    )
    rows = await conn.fetch(view.sql, *view.params)
    df = _rows_frame(rows, _TONE_COLUMNS)
    df["day"] = day_numbers(df["date_epoch"])
    tone = (
        df["pos_mentions"].fillna(0.0) - df["neg_mentions"].fillna(0.0)
    )
    out = pd.DataFrame({"day": df["day"], "tone": tone})
    return out.groupby("day", as_index=False).sum()
