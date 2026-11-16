"""analysis.market_crowdedness.pipeline — the entry pipeline class.

``MarketCrowdednessAnalysis`` (a :class:`DataAnalysis` subclass) owns
the run lifecycle: incremental scope, membership/names fetch, the
year-pinned coverage check, the chunked compute, the force/incremental
writes for the four output tables, and the identity + sanity summary.
The metric definition and layout live in the ``__main__.py`` docstring.
"""
from __future__ import annotations

import datetime
import gc
import logging
import time

import numpy as np
import pandas as pd

from _common.build_commons import rec_cols
from _common.data_analysis import DataAnalysis
from _common.db_commons import (
    analyze_tables_async,
    bulk_upsert_async,
    copy_frame_chunked_async,
)
from _common.df_utils import epoch_col_to_dt64, host_array, host_unique
from _common.labels import SEC_TYPE_CODE
from _common.sql_views import render_sql_view
from _common.sql_writes import render_sql_write
from analysis._common import sanitize_for_db_insert

from analysis.market_crowdedness.compute import _compute_crowdedness
from analysis.market_crowdedness.config import (
    ANALYSIS_DESCRIPTION,
    ANALYSIS_NAME,
    HOT_TECH_FLOAT_COLUMNS,
    HOT_TECH_OUT_COLUMNS,
    HOT_TECH_TABLE,
    INC_LOOKBACK_DAYS,
    OUT_COLUMNS,
    OUT_FLOAT_COLUMNS,
    PINNED_OUT_COLUMNS,
    SETS_OUT_COLUMNS,
    SETS_TABLE,
    TABLE,
    YEAR_TABLE,
)
from analysis.market_crowdedness.fetch import (
    _fetch_amounts,
    _fetch_membership,
    _fetch_returns,
    _fetch_stock_names,
    _fetch_tech,
)

logger = logging.getLogger(__name__)


class MarketCrowdednessAnalysis(DataAnalysis):
    """``python -m analysis.market_crowdedness`` — market crowdedness."""

    title = ("ANALYZE MARKET CROWDEDNESS — per-index/industry "
             "crowdedness curve")
    component = "market_crowdedness"
    # The page toggle exposes index + industry only; the pipeline covers
    # the same two sides (full active classified universe on both).
    sec_types = ("index", "industry")

    def add_arguments(self, parser) -> None:
        self.add_sec_type_arg(parser)
        self.add_force_arg(parser)

    def apply_args(self) -> None:
        pass

    def header_fields(self) -> dict:
        return {
            "tables": ("analysis.market_crowdedness "
                       "(+_year, _hot_sets, _hot_tech)"),
            "sec_types": ", ".join(self.resolve_sec_types()),
            "mode": "FORCE (delete scoped sec_types + full recompute)"
                    if self.args.force
                    else "incremental (missing dates only)",
        }

    async def run(self) -> None:
        t0 = time.time()
        scoped = self.resolve_sec_types()
        sec_type_codes = [SEC_TYPE_CODE[st] for st in scoped]
        force = bool(self.args.force)

        logger.info("\n" + "=" * 78)
        logger.info("  MARKET CROWDEDNESS v2  (%s mode, sec_types: %s)",
                    "FORCE" if force else "incremental", ", ".join(scoped))
        logger.info("=" * 78)

        # ---- [mc1/5] incremental scope: which dates are missing -------
        lo = datetime.date(1990, 1, 1)
        hi = datetime.date.today()
        target_dates: set | None = None
        amt_range = await self.conn.fetchrow(
            "SELECT MIN(date) AS lo, MAX(date) AS hi "
            "FROM stats.stock_liquidity_margin WHERE trading_amount > 0"
        )
        if not force:
            target_dates = set()
            for st in scoped:
                st_code = SEC_TYPE_CODE[st]
                target_dates |= await self.find_missing_dates(
                    TABLE, ["stats.stock_identity"], sec_type=st_code)
                # The year variants share the rolling gates — their
                # missing dates piggyback on the same calendar diff.
                target_dates |= await self.find_missing_dates(
                    YEAR_TABLE, ["stats.stock_identity"],
                    sec_type=st_code)
                # The hot-tech aggregates share the same pool calendar —
                # their missing dates piggyback on the same diff (the
                # gates differ but the window clamp below bounds them
                # identically).
                target_dates |= await self.find_missing_dates(
                    HOT_TECH_TABLE, ["stats.stock_identity"],
                    sec_type=st_code)
            if not target_dates:
                logger.info("[mc1/5] no missing dates — nothing to do.")
                await self.upsert_identity(
                    ANALYSIS_NAME, ANALYSIS_NAME, ANALYSIS_DESCRIPTION)
                return
            # Clamp to the amounts table's actual range: stock_identity
            # dates back to 2003 but stock_liquidity_margin starts ~2020,
            # and dates with no amount data can NEVER gain a row — without
            # the clamp every incremental run would recompute the full
            # window chasing unfillable pre-history dates. The FIRST
            # amounts date is excluded too: it has no previous traded day,
            # so its gross Δamt is zero by construction and no row can be
            # defined on it.
            if amt_range and amt_range["lo"] is not None:
                target_dates = {
                    d for d in target_dates
                    if amt_range["lo"] < d <= amt_range["hi"]
                }
                # Percentile warm-up: a row needs >= MIN_PCT_OBS (60)
                # defined pillar days of trailing history, so dates
                # before the 60th trading day after the first amounts
                # date can NEVER gain a row (same rationale as the
                # range clamp — without this edge every incremental run
                # chases the unfillable warm-up window). 120 calendar
                # days safely bounds 60 trading days incl. holiday
                # clustering; fillable dates later than a pool's own
                # inception are all past this edge.
                warmup_edge = (amt_range["lo"]
                               + datetime.timedelta(days=120))
                target_dates = {
                    d for d in target_dates if d >= warmup_edge
                }
            if not target_dates:
                logger.info(
                    "[mc1/5] missing dates all outside the amounts range "
                    "— nothing to do.")
                await self.upsert_identity(
                    ANALYSIS_NAME, ANALYSIS_NAME, ANALYSIS_DESCRIPTION)
                return
            lo = min(target_dates) - datetime.timedelta(
                days=INC_LOOKBACK_DAYS)
            hi = max(target_dates)
            logger.info("[mc1/5] %d missing date(s): %s .. %s "
                        "(+%d-day lookback)",
                        len(target_dates), min(target_dates), hi,
                        INC_LOOKBACK_DAYS)
        else:
            logger.info("[mc1/5] FORCE mode — full history recompute.")

        # ---- [mc2/5] membership + names ------------------------------
        members = await _fetch_membership(self.conn, scoped)
        if members.empty:
            logger.info("[mc2/5] no membership rows — nothing to compute.")
            return
        stock_codes = host_unique(members["stock_code"])
        names = await _fetch_stock_names(self.conn, stock_codes)
        n_pools = len(host_unique(members["pool_code"]))
        logger.info("[mc2/5] %d pool(s), %d member stock(s).",
                    n_pools, len(stock_codes))

        # ---- [mc1b/5] year-pinned coverage ---------------------------
        # A (pool, year) set can only be DERIVED from full history — an
        # incremental window never covers a past year's data. When a
        # pool is missing a frozen roster year (first run, a new pool,
        # the year roll, a pipeline-down gap) the pinned pass widens to
        # a FULL-window backfill; the rolling write is still guarded by
        # its present-date safety filter.
        frozen_sets = pd.DataFrame(
            columns=["sec_type", "pool_code", "hot_year", "hot_rank",
                     "stock_code"])
        if not force and amt_range and amt_range["lo"] is not None:
            frozen_sets = await self._load_frozen_sets(sec_type_codes)
            if self._pinned_backfill_needed(
                frozen_sets, members, amt_range["lo"], amt_range["hi"],
            ):
                lo = min(lo, amt_range["lo"])
                logger.info(
                    "[mc1b/5] frozen-roster coverage gap — pinned "
                    "FULL-window backfill this run.")
            else:
                logger.info("[mc1b/5] frozen rosters OK "
                            "(%s roster row(s)).", f"{len(frozen_sets):,}")

        # ---- [mc3/5] amounts + returns + tech + compute --------------
        amt = await _fetch_amounts(self.conn, stock_codes, lo, hi)
        ret = await _fetch_returns(self.conn, stock_codes, lo, hi)
        tech = await _fetch_tech(self.conn, stock_codes, lo, hi)
        logger.info("[mc3/5] %s amount row(s), %s return row(s), "
                    "%s tech row(s) in window — computing.",
                    f"{len(amt):,}", f"{len(ret):,}", f"{len(tech):,}")
        out, pinned_out, sets_out, tech_out = _compute_crowdedness(
            members, amt, ret, tech, names, frozen_sets)
        del members, amt, ret, tech
        gc.collect()
        logger.info("        -> %s crowdedness row(s), %s pinned "
                    "row(s), %s roster row(s), %s hot-tech row(s) "
                    "(%.1fs)",
                    f"{len(out):,}", f"{len(pinned_out):,}",
                    f"{len(sets_out):,}", f"{len(tech_out):,}",
                    time.time() - t0)
        if (out.empty and pinned_out.empty and sets_out.empty
                and tech_out.empty):
            logger.info("        no defined rows — nothing to write.")
            return

        # ---- [mc4/5] write --------------------------------------------
        if force:
            await self.delete_sec_type_rows(
                [TABLE, YEAR_TABLE, SETS_TABLE, HOT_TECH_TABLE],
                sec_type_codes)
            # copy_frame_chunked_async defaults keep the bulk-write
            # discipline engaged: partition-key-aligned chunks, one
            # COMMIT per chunk, the 0.5 s inter-chunk cool-down (10 s
            # once the write reaches 20+ chunks) and the replica-lag
            # wait (chunk_pause_s=None would disable them — never pass
            # it here).
            n = await copy_frame_chunked_async(
                self.conn, TABLE, out,
                columns=OUT_COLUMNS,
                numeric_cols=OUT_FLOAT_COLUMNS,
                round_to=None,  # pre-rounded in _compute_crowdedness
                date_cols=["date"],
                label="[mc4/5]",
            )
            n_q = 0
            if not pinned_out.empty:
                n_q = await copy_frame_chunked_async(
                    self.conn, YEAR_TABLE, pinned_out,
                    columns=PINNED_OUT_COLUMNS,
                    numeric_cols=OUT_FLOAT_COLUMNS,
                    round_to=None,  # pre-rounded in _finalize_pillars
                    date_cols=["date"],
                    label="[mc4/5]",
                )
            n_s = 0
            if not sets_out.empty:
                n_s = await copy_frame_chunked_async(
                    self.conn, SETS_TABLE, sets_out,
                    columns=SETS_OUT_COLUMNS,
                    numeric_cols=[],
                    round_to=None,
                    date_cols=[],
                    label="[mc4/5]",
                )
            n_t = 0
            if not tech_out.empty:
                n_t = await copy_frame_chunked_async(
                    self.conn, HOT_TECH_TABLE, tech_out,
                    columns=HOT_TECH_OUT_COLUMNS,
                    numeric_cols=HOT_TECH_FLOAT_COLUMNS,
                    round_to=None,  # pre-rounded in _compute_pool_batch
                    date_cols=["date"],
                    label="[mc4/5]",
                )
            await analyze_tables_async(
                self.conn,
                [TABLE, YEAR_TABLE, SETS_TABLE, HOT_TECH_TABLE])
            logger.info("[mc4/5] wrote %s row(s), %s pinned row(s), "
                        "%s roster row(s), %s hot-tech row(s).",
                        f"{n:,}", f"{n_q:,}", f"{n_s:,}", f"{n_t:,}")
        else:
            to_write = await self._filter_missing_dates(
                out, TABLE, sec_type_codes)
            rows = sanitize_for_db_insert(
                to_write, numeric_cols=OUT_FLOAT_COLUMNS,
                round_to=None, date_cols=["date"],
            )
            n = 0
            if rows:
                n = await bulk_upsert_async(
                    self.conn, TABLE, rows,
                    key_columns=["code", "sec_type", "date"],
                )
            open_year: int | None = None
            if amt_range and amt_range["hi"] is not None:
                open_year = int(amt_range["hi"].year)
            n_q = await self._write_pinned_incremental(
                pinned_out, sec_type_codes, open_year)
            n_s = await self._write_sets_incremental(sets_out)
            to_write_t = await self._filter_missing_dates(
                tech_out, HOT_TECH_TABLE, sec_type_codes)
            rows_t = sanitize_for_db_insert(
                to_write_t, numeric_cols=HOT_TECH_FLOAT_COLUMNS,
                round_to=None, date_cols=["date"],
            )
            n_t = 0
            if rows_t:
                n_t = await bulk_upsert_async(
                    self.conn, HOT_TECH_TABLE, rows_t,
                    key_columns=["code", "sec_type", "date"],
                )
            logger.info("[mc4/5] wrote %s row(s), %s pinned row(s), "
                        "%s roster row(s), %s hot-tech row(s).",
                        f"{n:,}", f"{n_q:,}", f"{n_s:,}", f"{n_t:,}")

        # ---- [mc5/5] identity + sanity summary ------------------------
        await self.upsert_identity(
            ANALYSIS_NAME, ANALYSIS_NAME, ANALYSIS_DESCRIPTION)
        summary = await self.conn.fetch("""
            SELECT sec_type, COUNT(*) AS n_rows,
                   COUNT(DISTINCT code) AS n_codes,
                   MIN(date) AS first_date, MAX(date) AS last_date,
                   ROUND(AVG(crowdedness_score), 4) AS avg_score,
                   ROUND(MAX(crowdedness_score), 4) AS max_score,
                   COUNT(comovement) AS n_comovement,
                   COUNT(valuation) AS n_valuation,
                   ROUND(AVG(comovement), 4) AS avg_comovement,
                   ROUND(AVG(valuation), 4) AS avg_valuation,
                   ROUND(AVG(top1_share), 4) AS avg_top1,
                   ROUND(AVG(top10_share), 4) AS avg_top10,
                   ROUND(AVG(effective_n), 2) AS avg_effective_n
            FROM analysis.market_crowdedness
            GROUP BY sec_type ORDER BY sec_type
        """)
        logger.info("\n      Summary by sec_type:")
        for r in summary:
            logger.info("        sec_type=%s: %s rows . %s codes . "
                        "%s -> %s . avg=%s max=%s . comovement(%s rows) "
                        "avg=%s . valuation(%s rows) avg=%s . "
                        "top1=%s top10=%s effN=%s",
                        r["sec_type"], f"{r['n_rows']:,}", r["n_codes"],
                        r["first_date"], r["last_date"],
                        r["avg_score"], r["max_score"],
                        f"{r['n_comovement']:,}", r["avg_comovement"],
                        f"{r['n_valuation']:,}", r["avg_valuation"],
                        r["avg_top1"], r["avg_top10"], r["avg_effective_n"])
        sample = await self.conn.fetch("""
            SELECT code, sec_type, date, crowdedness_score,
                   concentration, flow_change, comovement, valuation,
                   top1_share, top5_share, top10_share, effective_n,
                   concentration_pct, flow_pct, comovement_pct,
                   valuation_pct, top_stocks
            FROM analysis.market_crowdedness
            WHERE date = (SELECT MAX(date) FROM analysis.market_crowdedness)
            ORDER BY crowdedness_score DESC
            LIMIT 5
        """)
        logger.info("      Latest-date top-5 crowdedness sample:")
        for r in sample:
            logger.info("        %s (sec_type=%s) %s: score=%s "
                        "conc=%s/%s flow=%s/%s cmv=%s/%s val=%s/%s "
                        "top1/5/10=%s/%s/%s effN=%s | %s",
                        r["code"], r["sec_type"], r["date"],
                        r["crowdedness_score"],
                        r["concentration"], r["concentration_pct"],
                        r["flow_change"], r["flow_pct"],
                        r["comovement"], r["comovement_pct"],
                        r["valuation"], r["valuation_pct"],
                        r["top1_share"], r["top5_share"],
                        r["top10_share"], r["effective_n"],
                        (r["top_stocks"][:50] + "…")
                        if r["top_stocks"] and len(r["top_stocks"]) > 50
                        else r["top_stocks"])

        ysummary = await self.conn.fetch("""
            SELECT sec_type, COUNT(*) AS n_rows,
                   COUNT(DISTINCT code) AS n_codes,
                   COUNT(DISTINCT hot_year) AS n_years,
                   MIN(date) AS first_date, MAX(date) AS last_date,
                   ROUND(AVG(crowdedness_score), 4) AS avg_score,
                   ROUND(MAX(crowdedness_score), 4) AS max_score
            FROM analysis.market_crowdedness_year
            GROUP BY sec_type ORDER BY sec_type
        """)
        n_rosters = await self.conn.fetchval(
            "SELECT COUNT(*) FROM analysis.market_crowdedness_hot_sets")
        logger.info("\n      Year-pinned summary by sec_type "
                    "(%s roster row(s)):", f"{n_rosters:,}")
        for r in ysummary:
            logger.info("        sec_type=%s: %s rows . %s codes . %s "
                        "year(s) . %s -> %s . avg=%s max=%s",
                        r["sec_type"], f"{r['n_rows']:,}", r["n_codes"],
                        r["n_years"], r["first_date"], r["last_date"],
                        r["avg_score"], r["max_score"])

        tsummary = await self.conn.fetch("""
            SELECT sec_type, COUNT(*) AS n_rows,
                   COUNT(DISTINCT code) AS n_codes,
                   MIN(date) AS first_date, MAX(date) AS last_date,
                   ROUND(AVG(hot_n), 1) AS avg_hot_n,
                   COUNT(mean_px_vs_ma20) AS n_px20,
                   COUNT(mean_px_vs_ma20_pct) AS n_px20_pct,
                   COUNT(mean_rsi14) AS n_rsi,
                   COUNT(mean_amt_vs_ma20) AS n_amt20,
                   ROUND(AVG(mean_ret) * 100, 3) AS avg_ret_pct
            FROM analysis.market_crowdedness_hot_tech
            GROUP BY sec_type ORDER BY sec_type
        """)
        logger.info("\n      Hot-tech summary by sec_type:")
        for r in tsummary:
            logger.info("        sec_type=%s: %s rows . %s codes . "
                        "%s -> %s . avg hot_n=%s . px_vs_ma20 %s rows "
                        "(%s with pct) . rsi14 %s rows . amt_vs_ma20 "
                        "%s rows . avg ret=%s%%",
                        r["sec_type"], f"{r['n_rows']:,}", r["n_codes"],
                        r["first_date"], r["last_date"], r["avg_hot_n"],
                        f"{r['n_px20']:,}", f"{r['n_px20_pct']:,}",
                        f"{r['n_rsi']:,}", f"{r['n_amt20']:,}",
                        r["avg_ret_pct"])
        tsample = await self.conn.fetch("""
            SELECT code, sec_type, date, hot_n,
                   mean_px_vs_ma20, mean_px_vs_ma20_pct,
                   mean_rsi14, mean_roc20, mean_amt_vs_ma20, mean_ret
            FROM analysis.market_crowdedness_hot_tech
            WHERE date = (SELECT MAX(date)
                          FROM analysis.market_crowdedness_hot_tech)
            ORDER BY mean_px_vs_ma20_pct DESC NULLS LAST
            LIMIT 5
        """)
        logger.info("      Latest-date top-5 hot-tech sample "
                    "(by mean_px_vs_ma20_pct):")
        for r in tsample:
            logger.info("        %s (sec_type=%s) %s: hot_n=%s "
                        "px20=%s/%s rsi=%s roc=%s amt20=%s ret=%s",
                        r["code"], r["sec_type"], r["date"], r["hot_n"],
                        r["mean_px_vs_ma20"], r["mean_px_vs_ma20_pct"],
                        r["mean_rsi14"], r["mean_roc20"],
                        r["mean_amt_vs_ma20"], r["mean_ret"])

        logger.info("\n  market_crowdedness wall time: %.1fs",
                    time.time() - t0)

    # ------------------------------------------------------------------
    #  Incremental write helper
    # ------------------------------------------------------------------

    async def _filter_missing_dates(
        self, frame: pd.DataFrame, table: str, sec_type_codes: list,
    ) -> pd.DataFrame:
        """Post-compute incremental safety filter: keep only rows whose
        date is still missing for this sec_type in ``table`` (the
        rolling path's present-date guard, shared with the hot-tech
        write)."""
        kept: list[pd.DataFrame] = []
        for code in sec_type_codes:
            part = frame[frame["sec_type"] == code]
            if part.empty:
                continue
            rows_seen = await self.conn.fetch(
                f"SELECT DISTINCT date FROM {table} WHERE sec_type = $1",
                code,
            )
            present = {r["date"] for r in rows_seen if r["date"]}
            if present:
                miss_arr = np.array(
                    sorted(present), dtype="datetime64[D]"
                ).astype("datetime64[us]")
                part = part[~part["date"].isin(miss_arr)]
            if not part.empty:
                kept.append(part)
        return (pd.concat(kept, ignore_index=True)
                if kept else frame.iloc[0:0])

    # ------------------------------------------------------------------
    #  Year-pinned helpers (frozen-roster IO + incremental writes)
    # ------------------------------------------------------------------

    async def _load_frozen_sets(self, sec_type_codes: list) -> pd.DataFrame:
        """The closed-year rosters for the scoped sec_types —
        [sec_type, pool_code, hot_year(int64), hot_rank, stock_code];
        empty when nothing is frozen yet."""
        cols = ["sec_type", "pool_code", "hot_year", "hot_rank",
                "stock_code"]
        # analysis/market_crowdedness/pipeline/frozen_hot_sets
        view = render_sql_view(
            "analysis/market_crowdedness/pipeline/frozen_hot_sets",
            sec_types=sec_type_codes,
        )
        rows = await self.conn.fetch(view.sql, *view.params)
        if not rows:
            return pd.DataFrame(columns=cols)
        df = pd.DataFrame(rec_cols(rows), columns=cols)
        df["sec_type"] = df["sec_type"].astype("int16")
        df["hot_year"] = df["hot_year"].astype("int64")
        df["hot_rank"] = df["hot_rank"].astype("int64")
        return df

    def _pinned_backfill_needed(
        self,
        frozen_sets: pd.DataFrame,
        members: pd.DataFrame,
        amt_lo: datetime.date,
        amt_hi: datetime.date,
    ) -> bool:
        """True when a pool lacks a frozen roster for some year it
        should cover (all data years since its first frozen one) —
        those sets derive only from FULL history, so the run widens the
        window. Host walk over the small roster/membership frames."""
        if frozen_sets.empty or members.empty:
            return True
        data_years = list(range(amt_lo.year, amt_hi.year + 1))
        sec_h = host_array(frozen_sets["sec_type"].to_numpy()).astype(
            "int64")
        pool_h = host_array(frozen_sets["pool_code"].to_numpy())
        year_h = host_array(frozen_sets["hot_year"].to_numpy()).astype(
            "int64")
        frozen_map: dict[tuple[int, str], set[int]] = {}
        for s, p, y in zip(sec_h, pool_h, year_h):
            frozen_map.setdefault((int(s), str(p)), set()).add(int(y))
        m_sec = host_array(members["sec_type"].to_numpy()).astype("int64")
        m_pool = host_array(members["pool_code"].to_numpy())
        for s, p in set(zip(m_sec.tolist(), m_pool.tolist())):
            ks = frozen_map.get((int(s), str(p)))
            if not ks:
                return True  # a pool with no frozen roster at all
            first = min(ks)
            if any(y >= first and y not in ks for y in data_years):
                return True
        return False

    async def _write_pinned_incremental(
        self,
        pinned_out: pd.DataFrame,
        sec_type_codes: list,
        open_year: int | None,
    ) -> int:
        """Year variants, split by write semantics: MISSING (hot_year,
        date) pairs are conflict-free by construction -> the throttled
        COPY path (the first backfill is millions of rows — an upsert
        of that size took the server down 2026-11-14); the OPEN year's
        PRESENT rows are true overwrites (its set re-derives under it)
        -> the small upsert. Present rows of FROZEN years are not
        rewritten (their stored history stays as-is)."""
        if pinned_out.empty:
            return 0
        n_copy = 0
        n_up = 0
        for code in sec_type_codes:
            part = pinned_out[pinned_out["sec_type"] == code]
            if part.empty:
                continue
            # analysis/market_crowdedness/pipeline/year_present_pairs
            view = render_sql_view(
                "analysis/market_crowdedness/pipeline/year_present_pairs",
                sec_type=code,
            )
            rows = await self.conn.fetch(view.sql, *view.params)
            if rows:
                c = rec_cols(rows)
                c["date"] = epoch_col_to_dt64(c["date"])
                present = pd.DataFrame(c, columns=["hot_year", "date"])
                present = present.assign(_present=np.int8(1))
                part = part.merge(
                    present, on=["hot_year", "date"], how="left")
                missing = part[part["_present"].isna()].drop(
                    columns=["_present"])
                overwrite = part[part["_present"].notna()].drop(
                    columns=["_present"])
                if open_year is not None:
                    overwrite = overwrite[
                        overwrite["hot_year"] == open_year]
            else:
                missing = part
                overwrite = part.iloc[0:0]
            if not missing.empty:
                # COPY with no purge: the missing-pair filter guarantees
                # the PKs are absent (the db-sql-write conflict-free rule
                # satisfied by the filter, not a DELETE).
                n_copy += await copy_frame_chunked_async(
                    self.conn, YEAR_TABLE, missing,
                    columns=PINNED_OUT_COLUMNS,
                    numeric_cols=OUT_FLOAT_COLUMNS,
                    round_to=None,  # pre-rounded in _finalize_pillars
                    date_cols=["date"],
                    label="[mc4/5]",
                )
            if not overwrite.empty:
                row_dicts = sanitize_for_db_insert(
                    overwrite, numeric_cols=OUT_FLOAT_COLUMNS,
                    round_to=None, date_cols=["date"],
                )
                if row_dicts:
                    n_up += await bulk_upsert_async(
                        self.conn, YEAR_TABLE, row_dicts,
                        key_columns=["code", "sec_type", "hot_year",
                                     "date"],
                    )
        return n_copy + n_up

    async def _write_sets_incremental(self, sets_out: pd.DataFrame) -> int:
        """Replace the DERIVED rosters: per (sec_type, year) delete
        exactly the re-derived pools' rows, then upsert the fresh sets —
        a plain upsert would leave stale members of a shrunken roster."""
        if sets_out.empty:
            return 0
        # Group the derived (sec_type, year) blocks — host walk over
        # the small roster frame.
        y_h = host_array(sets_out["hot_year"].to_numpy()).astype("int64")
        st_h = host_array(sets_out["sec_type"].to_numpy()).astype("int64")
        code_h = host_array(sets_out["code"].to_numpy())
        groups: dict[tuple[int, int], set[str]] = {}
        for s, y, c in zip(st_h, y_h, code_h):
            groups.setdefault((int(s), int(y)), set()).add(str(c))
        for (s, y), codes in groups.items():
            # market_crowdedness/pipeline/market_crowdedness_hot_sets/
            # delete_derived
            write = render_sql_write(
                "analysis/market_crowdedness/pipeline/"
                "market_crowdedness_hot_sets/delete_derived",
                s, y, sorted(codes),
            )
            await self.conn.execute(write.sql, *write.params)
        row_dicts = sanitize_for_db_insert(
            sets_out, numeric_cols=[],
            round_to=None, date_cols=[],
        )
        if not row_dicts:
            return 0
        return await bulk_upsert_async(
            self.conn, SETS_TABLE, row_dicts,
            key_columns=["code", "sec_type", "hot_year", "stock_code"],
        )
