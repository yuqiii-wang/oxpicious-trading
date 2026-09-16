"""Entry point for analyze.margins.

Run via ``python -m analyze.margins``.

Pipeline (default: incremental — skip dates already in the DB;
``--force``: truncate-then-recompute on every run).

  1. Determine ``ref_date`` = MAX(date) across the selected source
     tables. The universe filter (securities with non-zero rz_balance
     in the last 30 calendar days) is evaluated against this date.

  2. For each sec_type in the selected set (etf / stock / both):
       a. fetch_active_rongzi_codes  — universe filter.
       b. fetch_margin_history       — full per-(code, date) rz_balance
          + rz_buy for the filtered codes.
       c. fetch_industry_mapping     — code -> industry_id.
       d. compute_tech_stats         — regime-detection cols (slope_ma5 +
          zscore_20d) per code, computed on FULL history.
       e. Write rows to margin_tech_stats.

  2b. build_margin_index_series — per-(index_code, date) weighted-average
      RONGZI margin series TABLE (Python vectorization — the former
      in-SQL VIEW aggregation), then index-level tech_stats from it.

  3. compute_industry_stats — per-(date, industry_id) SUM aggregation
     of rz_balance / rz_buy across stocks AND ETFs.

  4. run_margin_changes — detect sustained UP/DOWN TREND episodes on
     the RONGZI margin-balance curve and populate margin_changes
     (new_buy + rz_buy_vs_trading_amt_ratio).

  5. Register the analyses in analysis.analysis_identity.

Incremental mode rationale
  The universe filter (active rongzi in last 30d) shifts daily, but
  existing rows for past dates remain valid — rz_balance is a STOCK
  (cumulative balance) that doesn't change retroactively. New dates
  simply get appended via upsert; stale codes drop off naturally for
  new dates (their rows for past dates are retained as historical
  record). The slope computation still uses FULL history (fetched
  unconditionally) so windows are correct even for the first
  newly-added date.

Testing
  ``python -m analyze.margins --sec-type etf`` runs the pipeline with
  ETF data only (smaller dataset, ~1K rows vs ~600K for stocks).
  ``python -m analyze.margins --sec-type index`` runs only the
  margin_index_series TABLE build + index-level tech stats + trend
  detection (skips stock/etf/industry steps).
"""
from __future__ import annotations

# Runtime bootstrap — pre-check → silence warnings → cudf.pandas hook →
# UTF-8 stdout. MUST come before the imports below (analyze._common,
# _common.build_commons, pandas, ...) which transitively import pandas.
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.build_commons import (
    truncate_table_async,
)
from analyze.margins.config import (
    TABLE_TECH_STATS,
    TABLE_INDUSTRY_STATS,
    TABLE_INDEX_SERIES,
    SEC_TYPES,
    TECH_STATS_DESCRIPTION,
    INDEX_SERIES_DESCRIPTION,
    INDUSTRY_STATS_DESCRIPTION,
)

# Now safe to import modules that use pandas
import pandas as pd

from _common.log_setup import setup_logging
from _common.data_analysis import DataAnalysis

from analyze.margins.pipeline import (  # noqa: E402
    fetch_latest_source_date,
    detect_missing_dates,
    run_sec_type,
    build_margin_index_series,
    run_index_tech_stats,
    insert_industry_stats,
)
from analyze.margins.compute import compute_industry_stats
from analyze.margins.changes import run_margin_changes

logger = setup_logging("margins")


# ---------------------------------------------------------------------------
#  Main orchestration
# ---------------------------------------------------------------------------

class MarginsAnalysis(DataAnalysis):
    """``python -m analyze.margins`` — rongzi tech stats + industry SUM.

    sec_type mapping quirk: ``--sec-type`` accepts a pseudo-type 'both'
    (default) meaning SEC_TYPES, and 'index' meaning the index-series
    build only — so add_arguments declares the choices inline instead of
    using add_sec_type_arg(), and apply_args() maps the value onto
    (sec_types, run_index, is_index_only).
    """

    title = "ANALYZE MARGINS (rongzi-only tech stats + industry SUM)"
    component = "margins"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--sec-type",
            choices=["etf", "stock", "index", "both"],
            default="both",
            help="Which sec_type to process. 'both' (default) runs the full "
                 "pipeline. 'etf' or 'stock' runs only that sec_type (useful "
                 "for testing with a smaller dataset). 'index' runs only the "
                 "margin_index_series TABLE build (Python vectorization) + "
                 "index-level tech stats + trend detection (skips "
                 "stock/etf/industry steps; useful for testing the "
                 "index-series build in isolation).",
        )
        self.add_force_arg(parser)

    def apply_args(self) -> None:
        self.sec_types = tuple(
            SEC_TYPES if self.args.sec_type == "both" else (
                () if self.args.sec_type == "index" else (self.args.sec_type,)
            )
        )
        self.run_index = self.args.sec_type in ("both", "index")
        self.is_index_only = self.args.sec_type == "index"

    def header_fields(self) -> dict:
        return {
            "index_table": TABLE_TECH_STATS,
            "sec_type": self.args.sec_type,
            "mode": "FORCE (full recompute)" if self.args.force
                    else "incremental (missing dates only)",
        }

    async def run(self) -> None:
        conn = self.conn
        args = self.args
        force = self.args.force
        sec_types = list(self.sec_types)
        run_index = self.run_index
        is_index_only = self.is_index_only

        # ---- Step 0: determine ref_date + missing dates ---------------
        logger.info("\n[0/5] Determining ref_date (MAX date across source tables)...")
        ref_date = await fetch_latest_source_date(conn, sec_types)
        logger.info(f"    -> ref_date = {ref_date}")

        if not force:
            logger.info("\n    Detecting missing dates per table (incremental mode)...")
        (
            target_dates_tech, target_dates_index_series,
            target_dates_index_tech, target_dates_industry,
        ) = await detect_missing_dates(
            conn, sec_types, run_index, is_index_only, force,
        )

        # Early exit if everything is up to date (incremental mode only).
        if not force:
            total_missing = (
                sum(len(s) for s in target_dates_tech.values())
                + len(target_dates_index_series)
                + len(target_dates_index_tech)
                + len(target_dates_industry)
            )
            if total_missing == 0:
                logger.info("    -> DB is up to date; nothing to do.")
                return

        # ---- Step 1: per-sec-type tech stats ----------------------------
        logger.info(f"\n[1/5] Per-sec-type tech stats (sec_types={sec_types})...")
        # Force mode: truncate the whole tech_stats table up front when
        # processing both sec_types (faster than 2 separate DELETEs).
        if force and args.sec_type == "both":
            logger.info("    Truncating margin_tech_stats (all sec_types)...")
            await truncate_table_async(conn, TABLE_TECH_STATS)

        histories: dict[str, pd.DataFrame] = {}
        maps: dict[str, pd.DataFrame] = {}
        tech_stats_by_sec_type: dict[str, pd.DataFrame] = {}
        for st in sec_types:
            td = target_dates_tech.get(st)
            if td is not None and len(td) == 0 and not force:
                logger.info(f"\n  [{st}] up to date; skipping.")
                continue
            hist, imap, tech = await run_sec_type(
                conn, st, ref_date, force=force, target_dates=td,
            )
            histories[st] = hist
            maps[st] = imap
            tech_stats_by_sec_type[st] = tech

        # ---- Step 1b: margin_index_series TABLE + index tech stats ----
        if run_index:
            # 1b-1: build the margin_index_series TABLE via Python
            # vectorization (the former in-SQL VIEW aggregation).
            logger.info(f"\n[1b/5] Building {TABLE_INDEX_SERIES} "
                  "(vectorized)...")
            await build_margin_index_series(
                conn, force=force,
                target_dates=target_dates_index_series,
            )

            # 1b-2: index-level tech stats computed from the TABLE.
            td_idx = target_dates_index_tech
            if td_idx is not None and len(td_idx) == 0 and not force:
                logger.info("\n  [index] up to date; skipping.")
            else:
                idx_hist, idx_tech = await run_index_tech_stats(
                    conn, force=force, target_dates=td_idx,
                )
                histories["index"] = idx_hist
                tech_stats_by_sec_type["index"] = idx_tech

        # ---- Step 2: industry SUM aggregation ---------------------------
        if is_index_only:
            logger.info("\n[2/5] Per-(date, industry_id) SUM aggregation "
                  "-- SKIPPED (index-only test run)")
        else:
            logger.info("\n[2/5] Per-(date, industry_id) SUM aggregation "
                  "(stock + etf)...")

        etf_hist = histories.get("etf", pd.DataFrame(
            columns=["code", "date", "rz_balance", "rz_buy"]
        ))
        stock_hist = histories.get("stock", pd.DataFrame(
            columns=["code", "date", "rz_balance", "rz_buy"]
        ))
        etf_map = maps.get("etf", pd.DataFrame(
            columns=["code", "industry_id", "industry_label",
                     "parent_index_weight"]
        ))
        stock_map = maps.get("stock", pd.DataFrame(
            columns=["code", "industry_id", "industry_label",
                     "parent_index_weight"]
        ))

        if is_index_only:
            # Index-only test run: skip industry aggregation + insert
            # steps (no stock/etf histories to aggregate).
            industry_stats = pd.DataFrame()
        else:
            # ---- Step 2: compute industry SUM aggregation -------------
            logger.info("\n[2/5] Per-(date, industry_id) SUM aggregation "
                  "(stock + etf)...")

            # Pre-compute industry stats (will be inserted in step 3)
            industry_stats = compute_industry_stats(
                etf_tech=etf_hist,
                stock_tech=stock_hist,
                etf_industry_map=etf_map,
                stock_industry_map=stock_map,
            )
            n_industries = (
                industry_stats["industry_id"].nunique()
                if not industry_stats.empty else 0
            )
            logger.info(f"    -> {len(industry_stats):,} rows across "
                  f"{n_industries} industries")

            # ---- Step 3: insert industry_stats ------------------------
            logger.info(f"\n[3/5] Inserting into {TABLE_INDUSTRY_STATS}...")
            await insert_industry_stats(
                conn, industry_stats,
                force=force, target_dates=target_dates_industry,
            )

        # ---- Step 4: margin changes detection ---------------------------
        # run_margin_changes upserts its OWN analysis_identity row
        # (margin_changes) internally, reusing the in-memory tech_stats +
        # raw histories collected in step 1 (no DB round-trip for source
        # data). Always truncates + recomputes when called — new dates
        # can change trend boundaries.
        logger.info("\n[4/5] Margin changes detection (internal step)...")
        await run_margin_changes(
            conn,
            histories=histories,
            tech_stats_by_sec_type=tech_stats_by_sec_type,
            force=True,
        )

        # ---- Step 5: register in analysis_identity ----------------------
        # (the changes identity row is upserted by its own internal step
        # above)
        logger.info("\n[5/5] Registering in analysis.analysis_identity...")
        await self.upsert_identity(
            "margin_tech_stats", "margin_tech_stats", TECH_STATS_DESCRIPTION,
        )
        n_identity = 1
        if run_index:
            await self.upsert_identity(
                "margin_index_series", "margin_index_series",
                INDEX_SERIES_DESCRIPTION,
            )
            n_identity += 1
        if not is_index_only:
            # industry_stats identity row only upserted when the industry
            # aggregation step ran (skipped for index-only test runs).
            await self.upsert_identity(
                "margin_industry_stats", "margin_industry_stats",
                INDUSTRY_STATS_DESCRIPTION,
            )
            n_identity += 1
        logger.info(f"    -> upserted {n_identity} identity rows "
              f"(+1 from changes step)")


if __name__ == "__main__":
    MarginsAnalysis().execute()