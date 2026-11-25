"""builds.macro.pipeline — Build the macro daily family (gold/silver) and
upsert to the database.

Reads the full-history per-instrument CSVs written by
``downloads.macro.gold_silver`` (temps/gold_silver/{sge_au99.99,
sge_ag99.99,comex_gc,comex_si}.csv) and upserts the two macro tables:

  1. stats.macro_identity    — one identity row per (code, date)
  2. stats.macro_basic_stats — daily OHLC + change + volume (FK to identity)

change / change_pct are computed HERE over the full history per code
(close vs the previous trading-day close, via the shared grouped_shift
helper) — the sources carry no change fields and the UI stays a pure
data surface (no derived indicators client-side).

Missing-data detection flow (DB-first, per (code, date) pair):
  1. Read the 4 CSVs (full history — the downloader rewrites them daily)
  2. Compute change / change_pct per code over the full history
  3. Query stats.macro_identity for existing (code, date) pairs
  4. missing_pairs = available_pairs - existing_pairs
  5. If none: exit early (DB is up to date)
  6. Emit rows (ONE host unwrap at the boundary) filtered to the missing
     pairs; identity first, then basic_stats

With --force: truncate both tables first (basic_stats before identity —
FK), so all source rows are treated as missing.

With --date YYYY-MM-DD: build ONLY that single date and bypass the DB
missing-pair skip — the date is (re)built even if already present
(existing rows refresh through the normal upsert path). Mutually
exclusive with --force.

Usage:
  python -m builds.macro
  python -m builds.macro --date 2026-11-20
  python -m builds.macro --force

Prerequisite:
  Run `python -m downloads.macro.gold_silver` first to (re)generate the
  temps/gold_silver/*.csv source files.
"""
from __future__ import annotations

import argparse
import logging
import os
import time

import pandas as pd

from builds._commons.row_emission import dates_as_date_list, records_from_frame


def _host(df):
    """Unwrap a cudf-backed frame to host pandas ONCE before row emission
    (records_from_frame / dates_as_date_list). Frames stay cudf-dispatched
    through all processing — host conversion happens only at this final
    boundary."""
    return df.to_pandas() if hasattr(df, "to_pandas") else df

from _common.build_commons import (
    add_common_build_args,
    enforce_date_force_exclusion,
    forced_date_scope,
    copy_or_upsert_split_async,
    get_db_or_exit,
    parse_date_arg,
    print_build_header,
    print_wall_time,
    truncate_table_async,
)
from _common.df_utils import grouped_shift

from builds.macro.instruments import INSTRUMENT_BY_CODE, INSTRUMENTS
from builds.macro.paths import GOLD_SILVER_DIR, INSTRUMENT_CSVS

import logging
logger = logging.getLogger(__name__)

BASIC_COLS = ["date", "code", "open", "high", "low", "close",
              "change", "change_pct", "volume"]
IDENTITY_COLS = ["date", "code", "name", "metal_type", "unit", "source"]


class MacroPipeline:
    """Argv-scoped macro daily build (the BondPipeline shape: ``__init__``
    parses sys.argv — the MacroBuild delegate passes no namespace — and
    ``run()`` holds the flow)."""

    def __init__(self, args: argparse.Namespace | None = None) -> None:
        if args is None:
            ap = argparse.ArgumentParser()
            add_common_build_args(ap)
            args = ap.parse_args()
        self.args: argparse.Namespace = args

    async def insert_rows(self, conn, table: str, rows, pk_cols: list) -> None:
        """COPY/upsert rows into table and print the outcome line."""
        n_copied, n_upserted = await copy_or_upsert_split_async(conn, table, rows, pk_cols)
        total = n_copied + n_upserted
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
              f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
              "upsert"
        logger.info(f"    [DB] Inserted {total:,} rows into {table} via {via}")

    # ------------------------------------------------------------------
    # Frame building (cudf-dispatched)
    # ------------------------------------------------------------------
    def load_instrument_frame(self, code: str) -> pd.DataFrame | None:
        """Read one instrument's full-history CSV into a frame.

        The CSVs are our own downloader's output (clean ISO dates, header
        ``date,open,high,low,close,volume``); read via the sanctioned
        small-CSV route and typed explicitly (dates datetime64 via
        format-parse, numerics float64, empty volume → NaN).
        """
        csv_path = INSTRUMENT_CSVS[code]
        if not os.path.exists(csv_path):
            logger.error(f"    [FATAL] missing source CSV for {code}: {csv_path}")
            return None
        df = pd.read_csv(csv_path, dtype=str, encoding="utf-8-sig")
        if df is None or len(df) == 0:
            logger.warning(f"    [{code}] empty source CSV {csv_path}")
            return None
        df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["code"] = code
        return df

    def build_frames(self) -> pd.DataFrame:
        """All instruments' rows in ONE frame + per-code change columns
        (computed over the FULL history — the shift needs the previous
        trading day even when only the newest rows are missing)."""
        frames = []
        for inst in INSTRUMENTS:
            df = self.load_instrument_frame(inst.code)
            if df is not None:
                frames.append(df)
        if not frames:
            raise SystemExit(1)
        df = pd.concat(frames, ignore_index=True)
        df = df.sort_values(["code", "date"]).reset_index(drop=True)
        grouped_shift(df, "code", "close", "prev_close", periods=1)
        df["change"] = df["close"] - df["prev_close"]
        df["change_pct"] = (df["close"] / df["prev_close"] - 1.0) * 100.0
        return df

    # ------------------------------------------------------------------
    # Main flow
    # ------------------------------------------------------------------
    async def run(self) -> None:
        # --date / --force are mutually exclusive; parse the forced date early.
        enforce_date_force_exclusion(self.args)
        forced = parse_date_arg(self.args.date)

        t0 = time.time()
        print_build_header(
            "BUILD MACRO DAILY (GOLD/SILVER)  ·  missing-pairs-only → DATABASE",
            **{
                "Source dir": GOLD_SILVER_DIR,
                "Instruments": ", ".join(inst.code for inst in INSTRUMENTS),
            },
        )

        # ------------------------------------------------------------------
        # (1) Read the source CSVs + compute change / change_pct
        # ------------------------------------------------------------------
        logger.info("\n[1/4] Reading source CSVs (full history per instrument) …")
        df = self.build_frames()
        n_by_code = df.groupby("code").size().to_dict() if len(df) else {}
        logger.info(f"    {len(df):,} source rows "
                    + " ".join(f"[{c}]={n:,}" for c, n in sorted(n_by_code.items())))

        # ------------------------------------------------------------------
        # (2) Missing-(code, date)-pair discovery
        # ------------------------------------------------------------------
        logger.info("\n[2/4] Discovering missing (code, date) pairs …")
        host_df = _host(df)
        # ONE host emission of the full history (~10k rows) — the pair set
        # and the row filter run on the emitted dicts (pure python, no frame
        # string extraction).
        basic_rows_all = records_from_frame(host_df, BASIC_COLS)
        available_pairs = {(r["code"], r["date"]) for r in basic_rows_all}
        available_dates = set(dates_as_date_list(host_df["date"]))

        conn = await get_db_or_exit()
        if forced is not None:
            # --date mode: bypass the DB missing-pair skip — the forced date
            # is ALWAYS processed (existing rows refresh via the upsert path).
            scoped_dates = forced_date_scope(available_dates, forced, "temps/gold_silver CSVs")
            existing_pairs: set = set()
            missing_pairs = {p for p in available_pairs if p[1] in scoped_dates}
        elif self.args.force:
            # --force: purge the scope first (child before parent — FK), so
            # all source rows are treated as missing.
            logger.info("    [FORCE] truncating stats.macro_basic_stats + stats.macro_identity")
            await truncate_table_async(conn, "stats.macro_basic_stats")
            await truncate_table_async(conn, "stats.macro_identity")
            existing_pairs = set()
            missing_pairs = available_pairs
        else:
            existing_rows = await conn.fetch(
                "SELECT code, date FROM stats.macro_identity")
            existing_pairs = {(r["code"], r["date"]) for r in existing_rows}
            missing_pairs = available_pairs - existing_pairs
        logger.info(f"    [DB] {len(missing_pairs):,} pairs missing from "
                    f"stats.macro_identity ({len(existing_pairs):,} present)")

        if not missing_pairs:
            logger.info("    [INFO] Database is up to date — no new macro rows to insert")
            print_wall_time(t0)
            return

        # ------------------------------------------------------------------
        # (3) Emit rows filtered to the missing pairs
        # ------------------------------------------------------------------
        logger.info("\n[3/4] Emitting rows (missing pairs only) …")
        missing_basic_rows = [r for r in basic_rows_all
                              if (r["code"], r["date"]) in missing_pairs]
        identity_rows = []
        for r in missing_basic_rows:
            inst = INSTRUMENT_BY_CODE[r["code"]]
            identity_rows.append({
                "date": r["date"], "code": r["code"],
                "name": inst.name, "metal_type": inst.metal_type,
                "unit": inst.unit, "source": inst.source,
            })
        logger.info(f"    {len(identity_rows):,} identity rows, "
                    f"{len(missing_basic_rows):,} basic_stats rows")

        # ------------------------------------------------------------------
        # (4) Insert (identity first — FK)
        # ------------------------------------------------------------------
        logger.info("\n[4/4] Inserting to database (identity → basic_stats) …")
        await self.insert_rows(conn, "stats.macro_identity", identity_rows,
                               ["code", "date"])
        await self.insert_rows(conn, "stats.macro_basic_stats", missing_basic_rows,
                               ["code", "date"])

        print_wall_time(t0)
