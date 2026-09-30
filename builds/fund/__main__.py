"""builds.fund — Load OTC fund NAV history (ETF联接基金 feeders) into
stats.fund_nav.

Reads every ``temps/fund_nav/fund_nav_<code>.csv`` produced by
``downloads.fund.eastmoney.nav`` (eastmoney pingzhongdata — full NAV series
per feeder fund) and appends rows newer than each code's DB max date
(incremental), or truncates + reloads everything with ``--force``.

The table is the fund-side trend series for OTC feeder codes that have no
market OHLCV; it feeds the ``sec_type=fund`` baseline bundle served by
/api/sec-baseline and rendered by the shared CodeTrendChart (NAV close
line) on the ETF page's Linked Feeder ETFs row-click expansion.

Usage:
  python -m builds.fund            # incremental (per-code max-date append)
  python -m builds.fund --force    # truncate stats.fund_nav + full reload
"""
from __future__ import annotations

import glob
import os
from datetime import date

import numpy as np
import pandas as pd

from _common.data_build import DataBuild
from _common.log_setup import setup_logging

logger = setup_logging("fund")

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate  # noqa: E402
activate()

from _common.db_commons import copy_or_upsert_split_async  # noqa: E402
from builds._commons.paths import FUND_NAV_DIR  # noqa: E402

NAV_COLS = ["date", "code", "nav", "cum_nav", "day_pct"]


def load_nav_frames() -> pd.DataFrame:
    """Concat every per-fund NAV CSV in temps/fund_nav (dedup on code+date)."""
    files = sorted(glob.glob(os.path.join(FUND_NAV_DIR, "fund_nav_*.csv")))
    frames = []
    for path in files:
        if os.path.basename(path).startswith("fund_nav_universe_"):
            continue
        df = pd.read_csv(path, dtype=str, encoding="utf-8-sig", keep_default_na=False)
        if df.empty or "date" not in df.columns:
            continue
        frames.append(df[NAV_COLS])
    if not frames:
        return pd.DataFrame(columns=NAV_COLS)
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates(subset=["code", "date"], keep="first")


def _to_write_rows(write_df: pd.DataFrame) -> list[dict]:
    """Vectorized frame → row-dict list for copy_or_upsert (NaT/NaN → None)."""
    w = write_df.copy()
    w["date"] = pd.to_datetime(w["date"], errors="coerce")
    w = w.dropna(subset=["date"])
    w["nav"] = pd.to_numeric(w["nav"], errors="coerce")
    w = w.dropna(subset=["nav"])
    for c in ("cum_nav", "day_pct"):
        w[c] = pd.to_numeric(w[c], errors="coerce").astype(object).where(
            pd.to_numeric(w[c], errors="coerce").notna(), None)
    date_l = [d.date() for d in pd.to_datetime(w["date"])]
    code_l = np.asarray(w["code"], dtype=object).tolist()
    nav_l = np.asarray(w["nav"], dtype=float).tolist()
    cum_l = np.asarray(w["cum_nav"], dtype=object).tolist()
    pct_l = np.asarray(w["day_pct"], dtype=object).tolist()
    return [
        {"code": c, "date": d, "nav": n, "cum_nav": cu, "day_pct": p}
        for c, d, n, cu, p in zip(code_l, date_l, nav_l, cum_l, pct_l)
    ]


class FundBuild(DataBuild):
    """``python -m builds.fund`` — NAV loader for stats.fund_nav."""

    title = "BUILD FUND NAV  ·  feeder funds (ETF联接基金)  ·  → stats.fund_nav"
    component = "fund"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)

    async def run(self) -> None:
        all_df = load_nav_frames()
        if all_df.empty:
            logger.error("    [FATAL] No fund_nav_*.csv rows found — run "
                         "downloads.fund.eastmoney.nav first")
            raise SystemExit(1)
        logger.info(f"    [CSV] {all_df['code'].nunique()} funds, "
              f"{len(all_df):,} NAV rows on disk "
              f"({all_df['date'].min()} → {all_df['date'].max()})")

        conn = await self.connect_db()
        try:
            if self.args.force:
                await conn.execute("TRUNCATE TABLE stats.fund_nav")
                write_df = all_df
                logger.info("    [DB] truncated stats.fund_nav (force)")
            else:
                max_rows = await conn.fetch(
                    "SELECT code, max(date) AS max_date FROM stats.fund_nav GROUP BY code")
                if max_rows:
                    cutoff = pd.to_datetime(all_df["code"].map(
                        {r["code"]: r["max_date"] for r in max_rows}))
                    dates = pd.to_datetime(all_df["date"], errors="coerce")
                    write_df = all_df[cutoff.isna() | (dates > cutoff)]
                    logger.info(f"    [DB] incremental: {len(max_rows)} codes stored, "
                          f"{len(write_df):,} rows newer than per-code max dates")
                else:
                    write_df = all_df
                    logger.info("    [DB] empty table — full load")

            if write_df.empty:
                logger.info("    [DB] up to date — nothing to write")
                return

            rows = _to_write_rows(write_df)
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, "stats.fund_nav", rows, ["code", "date"],
                date_column="date",
            )
            via = "COPY" if n_copied and not n_upserted else "COPY+upsert"
            logger.info(f"    [DB] wrote {n_copied + n_upserted:,} rows into "
                  f"stats.fund_nav via {via}")
        finally:
            await conn.close()


if __name__ == "__main__":
    FundBuild().execute()
