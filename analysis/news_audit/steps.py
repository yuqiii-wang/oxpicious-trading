"""Steps for analysis.news_audit — registry sync + the two 5-day audits.

Compute layout (cudf.pandas, whole-frame): the article/keyword frames are
fetched with dates as epoch float8 (the DB-read convention), bucketed onto
the 5-day grid in int64 day-number space (one floordiv + multiply), and
aggregated with ONE grouped pass per grain. The only host round-trips are
the sanctioned write-boundary helpers (epoch_col_to_dt64 /
sanitize_for_db_insert) — there is no gather/index algorithm here that
would justify a host core.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from pydantic import BaseModel

from _common.build_commons import rec_cols
from _common.db_commons import bulk_upsert_async
from _common.df_utils import epoch_col_to_dt64
from _common.sql_views import render_sql_view
from _common.sql_writes import load_sql_write
from analysis._common import sanitize_for_db_insert
from analysis.news_audit.config import (
    CONTENT_AUDIT_TABLE,
    PROVIDER_AUDIT_TABLE,
)
from analysis.news_audit.windows import AuditWindowGrid

_EPOCH = date(1970, 1, 1)

SECONDS_PER_DAY = 86_400.0


# ---------------------------------------------------------------------------
#  Fetch helpers
# ---------------------------------------------------------------------------

async def fetch_frame(conn, view_name: str, **args) -> pd.DataFrame:
    """Render a sql_views template and land its rows as a DataFrame."""
    view = render_sql_view(view_name, **args)
    rows = await conn.fetch(view.sql, *view.params)
    return pd.DataFrame(rec_cols(rows))


async def news_bearing_window_starts(conn) -> set[int]:
    """Grid window starts (day-numbers) that contain at least one news
    article. The complement (grid windows with NO news) is the
    empty-window exclusion set for incremental targeting — an empty
    window never materializes rows, so it must not read as missing
    forever."""
    view = render_sql_view("analysis/news_audit/steps/nonempty_windows")
    rows = await conn.fetch(view.sql, *view.params)
    return {(r["window_start"] - _EPOCH).days for r in rows}


def _day_numbers(date_epoch: pd.Series) -> pd.Series:
    """Epoch-seconds column -> int64 day numbers since the unix epoch."""
    return (date_epoch // SECONDS_PER_DAY).astype("int64")


def _window_starts(days: pd.Series, grid: AuditWindowGrid) -> pd.Series:
    """Day numbers of each row's 5-day grid window start (anchor + 5k)."""
    return grid.anchor_days + (
        (days - grid.anchor_days) // grid.stride * grid.stride
    )


def _attach_window_cols(
    agg: pd.DataFrame, grid: AuditWindowGrid
) -> pd.DataFrame:
    """Attach window_end / window_start / is_complete to a grouped frame
    keyed on the ``wstart`` day-number column, and drop the integer key."""
    end_days = agg["wstart"] + (grid.window_days - 1)
    agg["is_complete"] = end_days <= grid.latest_days
    # index= is REQUIRED: the frame was boolean-filtered to the target
    # windows (gappy index); a fresh RangeIndex series would misalign to
    # all-NaN dates (epoch_col_to_dt64's own docstring calls this out).
    agg["window_start"] = epoch_col_to_dt64(
        agg["wstart"] * 86400, unit="us", index=agg.index
    )
    agg["window_end"] = epoch_col_to_dt64(
        end_days * 86400, unit="us", index=agg.index
    )
    return agg.drop(columns=["wstart"])


def _fake_pct(num_fake: pd.Series, num_digested: pd.Series) -> pd.Series:
    """num_fake / num_digested as a 0..1 fraction; NaN when nothing was
    digested (verdicts exist only on digested rows)."""
    den = num_digested.astype("float64").where(num_digested > 0)
    return (num_fake.astype("float64") / den).round(4)


# ---------------------------------------------------------------------------
#  Step 1 — provider registry sync
# ---------------------------------------------------------------------------

class NewsProviderOriginRow(BaseModel):
    """One origin as synced into text.news_providers."""

    source: str
    author: str
    first_seen_date: date
    last_seen_date: date


class ProviderRegistryStep:
    """Sync text.news_providers from the distinct origins of text.news.

    Upserts every (source, author) origin with its full-history seen-date
    span. The conflict arm updates ONLY the span + updated_at — the manual
    is_blocked flag survives every sync (database/sql_writes action
    analysis/news_audit/steps/news_providers/upsert_origin.sql). Never
    deletes: origins that stop publishing keep their registry row.
    """

    title = "text.news_providers registry sync"

    async def run(self, conn) -> tuple[date | None, date | None]:
        view = render_sql_view("analysis/news_audit/steps/provider_origins")
        rows = await conn.fetch(view.sql, *view.params)
        origins = [NewsProviderOriginRow.model_validate(dict(r)) for r in rows]
        if not origins:
            print(f"[{self.title}] text.news is empty — nothing to sync")
            return None, None

        upsert = load_sql_write(
            "analysis/news_audit/steps/news_providers/upsert_origin"
        )
        await conn.executemany(
            upsert,
            [
                (o.source, o.author, o.first_seen_date, o.last_seen_date)
                for o in origins
            ],
        )
        first = min(o.first_seen_date for o in origins)
        latest = max(o.last_seen_date for o in origins)
        print(
            f"[{self.title}] {len(origins):,} origins upserted "
            f"({first} .. {latest})"
        )
        return first, latest


# ---------------------------------------------------------------------------
#  Step 2 — provider audit (per window x origin)
# ---------------------------------------------------------------------------

class ProviderAuditStep:
    """Build text.news_provider_audit rows for the target windows.

    Grain (window_start, source, author): num_items over ALL articles,
    num_digested / num_fake / fake_pct / avg_sentiment over the digested
    ones, avg_word_count over all, num_industries as the origin's spread.
    """

    title = "text.news_provider_audit"
    table = PROVIDER_AUDIT_TABLE
    key_columns = ["window_start", "source", "author"]

    def compute(
        self, articles: pd.DataFrame, grid: AuditWindowGrid, target_days: list[int]
    ) -> pd.DataFrame:
        art = articles.assign(
            wstart=_window_starts(_day_numbers(articles["date_epoch"]), grid)
        )
        agg = (
            art.groupby(["wstart", "source", "author"], sort=True)
            .agg(
                num_items=("news_id", "size"),
                num_digested=("digested", "sum"),
                num_fake=("is_fake", "sum"),
                avg_sentiment=("sentiment_level", "mean"),
                avg_word_count=("word_count", "mean"),
                num_industries=("industry_id", "nunique"),
            )
            .reset_index()
        )
        agg = agg[agg["wstart"].isin(target_days)]
        agg = _attach_window_cols(agg, grid)
        agg["fake_pct"] = _fake_pct(agg["num_fake"], agg["num_digested"])
        return agg[
            [
                "window_start", "window_end", "source", "author",
                "is_complete", "num_items", "num_digested", "num_fake",
                "fake_pct", "avg_sentiment", "avg_word_count",
                "num_industries",
            ]
        ]

    async def run(
        self, conn, articles: pd.DataFrame, keywords: pd.DataFrame | None,
        grid: AuditWindowGrid, target_days: list[int],
    ) -> None:
        # keywords is unused at this grain (both steps share one caller
        # shape so __main__ can drive them from one frame pair).
        agg = self.compute(articles, grid, target_days)
        rows = sanitize_for_db_insert(
            agg,
            numeric_cols=["fake_pct", "avg_sentiment", "avg_word_count"],
            date_cols=["window_start", "window_end"],
        )
        await _purge_windows(
            conn, "analysis/news_audit/steps/news_provider_audit/purge_window",
            target_days,
        )
        await bulk_upsert_async(conn, self.table, rows, key_columns=self.key_columns)
        n_windows = len({r["window_start"] for r in rows})
        print(
            f"[{self.title}] {len(rows):,} rows written "
            f"({n_windows} of {len(target_days)} target windows carry rows)"
        )


# ---------------------------------------------------------------------------
#  Step 3 — content audit (per window x industry, + keyword rollup)
# ---------------------------------------------------------------------------

class ContentAuditStep:
    """Build text.news_content_audit rows for the target windows.

    Grain (window_start, industry_id): the provider audit's item/digested/
    fake/sentiment metrics, plus the keyword rollup from the per-keyword
    frame — distinct keywords hit (counted once per window, not per
    article), total affirmative mentions, and how many articles hit at
    least one keyword (0-filled when the window has no keyword rows).
    """

    title = "text.news_content_audit"
    table = CONTENT_AUDIT_TABLE
    key_columns = ["window_start", "industry_id"]

    def compute(
        self,
        articles: pd.DataFrame,
        keywords: pd.DataFrame,
        grid: AuditWindowGrid,
        target_days: list[int],
    ) -> pd.DataFrame:
        art = articles.assign(
            wstart=_window_starts(_day_numbers(articles["date_epoch"]), grid)
        )
        items = (
            art.groupby(["wstart", "industry_id"], sort=True)
            .agg(
                num_items=("news_id", "size"),
                num_digested=("digested", "sum"),
                num_fake=("is_fake", "sum"),
                avg_sentiment=("sentiment_level", "mean"),
            )
            .reset_index()
        )
        kw = keywords.assign(
            wstart=_window_starts(_day_numbers(keywords["date_epoch"]), grid)
        )
        kw_agg = (
            kw.groupby(["wstart", "industry_id"], sort=True)
            .agg(
                distinct_keywords=("keyword", "nunique"),
                keyword_mentions=("kw_count", "sum"),
                articles_with_keywords=("news_id", "nunique"),
            )
            .reset_index()
        )
        out = items.merge(
            kw_agg, on=["wstart", "industry_id"], how="left"
        )
        # No-keyword windows/industries carry 0s (int-typed end-to-end —
        # NULL would force the columns through float at the write boundary).
        for c in ("distinct_keywords", "keyword_mentions", "articles_with_keywords"):
            out[c] = out[c].fillna(0).astype("int64")
        out = out[out["wstart"].isin(target_days)]
        out = _attach_window_cols(out, grid)
        out["fake_pct"] = _fake_pct(out["num_fake"], out["num_digested"])
        return out[
            [
                "window_start", "window_end", "industry_id", "is_complete",
                "num_items", "num_digested", "num_fake", "fake_pct",
                "avg_sentiment", "distinct_keywords", "keyword_mentions",
                "articles_with_keywords",
            ]
        ]

    async def run(
        self, conn, articles: pd.DataFrame, keywords: pd.DataFrame,
        grid: AuditWindowGrid, target_days: list[int],
    ) -> None:
        agg = self.compute(articles, keywords, grid, target_days)
        rows = sanitize_for_db_insert(
            agg,
            numeric_cols=["fake_pct", "avg_sentiment"],
            date_cols=["window_start", "window_end"],
        )
        await _purge_windows(
            conn, "analysis/news_audit/steps/news_content_audit/purge_window",
            target_days,
        )
        await bulk_upsert_async(conn, self.table, rows, key_columns=self.key_columns)
        n_windows = len({r["window_start"] for r in rows})
        print(
            f"[{self.title}] {len(rows):,} rows written "
            f"({n_windows} of {len(target_days)} target windows carry rows)"
        )


# ---------------------------------------------------------------------------
#  Shared write helper
# ---------------------------------------------------------------------------

async def _purge_windows(conn, action_name: str, target_days: list[int]) -> None:
    """DELETE the target windows' stale rows before the recomputed ones are
    upserted (grace/trailing windows recompute in place)."""
    purge_sql = load_sql_write(action_name)
    await conn.executemany(
        purge_sql,
        [
            (_EPOCH + timedelta(days=s),)
            for s in target_days
        ],
    )
