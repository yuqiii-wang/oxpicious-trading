"""analysis.text_metrics.pipeline — the DataAnalysis driver.

Phases:

  [tm1/4] scope — identity registration + force/incremental decision
  [tm2/4] fetch — the full-history articles + keyword-tone panels
  [tm3/4] compute — DailyTextMetrics (config knobs, whole-frame passes)
  [tm4/4] write — force: TRUNCATE + chunked COPY; incremental: the
          missing-dates ∪ recent-grace window upsert
"""
from __future__ import annotations

from _common.data_analysis import DataAnalysis
from _common.db_commons import (
    analyze_tables_async,
    bulk_upsert_async,
    copy_frame_chunked_async,
)
from _common.df_utils import sanitize_for_db_insert, to_py_dates

from analysis.text_metrics.config import (
    ANALYSIS_DESCRIPTION,
    ANALYSIS_NAME,
    GRACE_RECOMPUTE_DAYS,
    KEY_COLUMNS,
    OUT_COLUMNS,
    OUT_FLOAT_COLUMNS,
    TABLE,
)
from analysis.text_metrics.compute.metrics import DailyTextMetrics
from analysis.text_metrics.fetch import (
    fetch_articles,
    fetch_keyword_tone,
)


class TextMetricsAnalysis(DataAnalysis):
    """Daily news-text metrics per industry + the broad market."""

    title = "ANALYZE TEXT METRICS"
    component = "text_metrics"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)

    def header_fields(self) -> dict:
        return {"mode": "FORCE (truncate + COPY rebuild)"
                if getattr(self.args, "force", False) else "incremental"}

    async def run(self) -> None:
        force = bool(getattr(self.args, "force", False))

        # ---- [tm1/4] identity + scope -----------------------------------
        self.logger.info("[tm1/4] scope: %s — full-history fetch, the "
                         "corpus is the working set (config.FETCH_*).",
                         "FORCE" if force else "incremental")
        await self.upsert_identity(ANALYSIS_NAME, ANALYSIS_NAME,
                                   ANALYSIS_DESCRIPTION)

        # ---- [tm2/4] fetch ------------------------------------------------
        articles = await fetch_articles(self.conn)
        tone = await fetch_keyword_tone(self.conn)
        self.logger.info("[tm2/4] %d article row(s), %d tone row(s).",
                         len(articles), len(tone))
        if articles.empty:
            self.logger.info("[tm2/4] no articles — nothing to compute.")
            return

        # ---- [tm3/4] compute ----------------------------------------------
        out = DailyTextMetrics().run(articles, tone)
        self.logger.info("[tm3/4] %d candidate row(s) past the "
                         "MIN_ARTICLES gate.", len(out))
        if out.empty:
            self.logger.info("[tm3/4] no rows past the gate — nothing "
                             "to write.")
            return

        # Write-boundary date materialization (B-A2): compute kept
        # datetime64[us]; the scope set-ops + writes use python dates.
        out = to_py_dates(out, ["date"])

        # ---- [tm4/4] write -------------------------------------------------
        if force:
            # truncate_tables iterates its argument — pass a LIST, never
            # the bare table-name string (a string iterates per char).
            await self.truncate_tables([TABLE])
            n = await copy_frame_chunked_async(
                self.conn, TABLE, out,
                columns=OUT_COLUMNS,
                numeric_cols=OUT_FLOAT_COLUMNS,
                date_cols=["date"],
                label="[tm4/4]",
            )
            self.logger.info("[tm4/4] FORCE — COPY %d row(s) into %s.",
                             n, TABLE)
        else:
            present = {
                r["date"] for r in await self.conn.fetch(
                    f"SELECT DISTINCT date FROM {TABLE}")
            }
            panel_dates = set(out["date"])
            missing = panel_dates - present
            newest = max(panel_dates)
            grace = {
                d for d in (panel_dates & present)
                if (newest - d).days <= GRACE_RECOMPUTE_DAYS
            }
            targets = missing | grace
            self.logger.info(
                "[tm4/4] %d panel date(s): %d missing, %d in the %d-day "
                "grace window.", len(panel_dates), len(missing),
                len(grace), GRACE_RECOMPUTE_DAYS)
            if not targets:
                self.logger.info("[tm4/4] nothing to write.")
                return
            rows_df = out.loc[out["date"].isin(targets)]
            rows = sanitize_for_db_insert(
                rows_df, numeric_cols=OUT_FLOAT_COLUMNS, date_cols=["date"])
            n = await bulk_upsert_async(
                self.conn, TABLE, rows, key_columns=KEY_COLUMNS)
            self.logger.info("[tm4/4] upsert %d row(s) into %s.",
                             n, TABLE)

        await analyze_tables_async(self.conn, [TABLE])
        await self._log_summary()

    async def _log_summary(self) -> None:
        """End-of-run sanity: coverage, market-row extremes, a sample."""
        stats = await self.conn.fetchrow(
            f"SELECT COUNT(*) AS rows, COUNT(DISTINCT industry_id) AS n_id, "
            f"MIN(date) AS min_d, MAX(date) AS max_d "
            f"FROM {TABLE}")
        self.logger.info(
            "[tm] table: %s row(s), %d industry ids (incl. the '' market "
            "row), %s .. %s", stats["rows"], stats["n_id"],
            stats["min_d"], stats["max_d"])
        sample = await self.conn.fetch(
            f"SELECT date, sentiment_z, flow_z, kw_tone FROM {TABLE} "
            f"WHERE industry_id = '' ORDER BY date DESC LIMIT 5")
        for r in sample:
            self.logger.info(
                "[tm] market %s sentiment_z=%s flow_z=%s kw_tone=%s",
                r["date"],
                None if r["sentiment_z"] is None
                else round(r["sentiment_z"], 2),
                None if r["flow_z"] is None
                else round(r["flow_z"], 2),
                None if r["kw_tone"] is None
                else round(r["kw_tone"], 2))
