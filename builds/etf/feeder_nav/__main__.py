"""builds.etf.feeder_nav — Load huatai-pb.com close-only NAV series into
stats.etf_identity + stats.etf_basic_stats, two universes:

  1. OTC ETF联接 feeder funds (codes the listed-ETF prefix families do
     NOT cover) — full series from the archives + accumulated daily trend
     snapshots; the ONLY quote source these funds have. Missing pairs ∪
     newest REFRESH_DAYS days upserted.
  2. LISTED checkin ETFs (51x/56x/58x SSE + 159 SZSE prefixes — the
     watchlist's listed members, discovered from the archive CSVs' file
     names) — NAV-history BACKFILL for dates with NO row yet (the
     exchange OHLCV starts 2026-07-31; the fund-site archives reach back
     to each fund's inception). Strictly missing-only: real exchange
     OHLC rows are NEVER overwritten, so no refresh window and no --force
     purge applies.

Codes are BARE 6-digit for BOTH universes (2026-09-30 convention — no
exchange suffix anywhere). Listed-vs-OTC is discriminated by the code
prefix on the CSV side and by etf_identity.exchange on the DB side
(SS/SZ = listed, NULL = OTC feeder).

The fund site publishes NO OHLC — only 单位净值 (close) per trade date —
so OHLC is SYNTHESIZED per the etf_basic_stats.is_ohl_estimated
convention (same as the index build's close-only CSIndex history):
open = previous close of the same code (NULL for the first row),
high = low = close, is_ohl_estimated = TRUE.
Feeder rows carry exchange NULL; listed backfill rows carry their
listing venue in etf_identity.exchange.

Reads the huatai-pb NAV CSVs (downloads.etf.huatai_pb.*):
  • full history:  temps/huatai_pb_nav/huatai_pb_nav_archive_<code>.csv
    (bare-code files; LEGACY files written before 2026-09-30 carry a
    suffixed code column — stripped on read)
  • daily increments: temps/huatai_pb_nav/huatai_pb_nav_trend_<date>.csv
    (all-funds snapshot, one row per fund per run date — the accumulated
    files carry each fund's freshest NAV days)

Feeder universe: codes whose 基金名称 contains 联接 in the LATEST trend
snapshot (every feeder share class A/C/... the site carries — a superset
of the one-row-per-family stats.sec_etf_links registry). Writes ONLY
etf_identity + etf_basic_stats (no tech/adjustment/liquidity rows — no
margin, and MAs stay NULL until a consumer needs them); rows surface
through stats.v_etf_margin like any listed ETF.

Missing-data detection flow (DB-first):
  query the relevant (date, code) pairs from stats.etf_identity → read
  CSVs → keep pairs the DB lacks (feeders: ∪ newest REFRESH_DAYS days —
  NAV corrections self-heal) → bulk upsert (identity first —
  etf_basic_stats has the FK).

With --force: DELETE the feeder slice (exchange IS NULL) from both
tables first, then reload the feeder universe (listed-ETF backfill stays
missing-only — --force never deletes exchange rows). NOTE:
``builds.etf --force`` truncates etf_identity CASCADE, wiping feeder +
backfill rows too — re-run this module afterwards (same ordering
contract as builds.index.composition vs builds.index.daily; the daily
driver runs it right after builds.etf).

Usage:
  python -m builds.etf.feeder_nav
  python -m builds.etf.feeder_nav --code 012761     (single fund, bare)
  python -m builds.etf.feeder_nav --force
  python -m builds.etf.feeder_nav --date 2026-09-29 (force one NAV date)

The :class:`FeederNavBuild` entry class (a :class:`DataBuild` subclass)
owns the runtime lifecycle + common argparse. Bootstrap-first layout:
the runtime setup runs before this module's imports.
"""

from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from typing import Dict, List, Optional, Set, Tuple
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from _common.build_commons import bulk_upsert_async, TODAY_STR
from _common.db_commons import analyze_tables_async, chunked_purge_async
from builds._commons.paths import HUATAI_NAV_DIR
from builds._commons.row_emission import dates_as_date_list, records_from_frame
from _common.log_setup import setup_logging

from _common.data_build import DataBuild
from downloads.etf.huatai_pb._endpoints import (
    SSE_ETF_PREFIXES,
    is_listed_etf_code,
)

logger = setup_logging("feeder_nav")

# paths.py carries os.path.join strings — glob needs a Path
NAV_DIR = Path(HUATAI_NAV_DIR)

# Always re-upsert the newest NAV days even when present — the fund site
# can correct recent values (same intent as builds.etf's recent re-scan).
REFRESH_DAYS = 5

NAV_SRC_COLS = ["date", "code", "nav", "day_pct"]


def _exchange_of(code: str) -> Optional[str]:
    """Listing venue for a bare ETF code ('SS'/'SZ'); None for OTC funds."""
    if is_listed_etf_code(code):
        return "SS" if str(code).startswith(SSE_ETF_PREFIXES) else "SZ"
    return None


def trend_names() -> Dict[str, str]:
    """Raw code → 基金名称 from the LATEST trend snapshot (bare codes —
    legacy suffixed rows are stripped). {} when no snapshot exists."""
    files = sorted(NAV_DIR.glob("huatai_pb_nav_trend_*.csv"))
    if not files:
        return {}
    snap = pd.read_csv(files[-1], dtype={"code": str})
    snap = snap.dropna(subset=["name"])
    codes = snap["code"].astype(str).str.split(".").str[0]
    return dict(zip(codes.tolist(), snap["name"].astype(str).tolist()))


def resolve_universe(
    code_filter: Optional[str], raw_names: Dict[str, str]
) -> Tuple[Set[str], Dict[str, str]]:
    """Feeder universe + display names (BARE codes) from raw trend names.

    Universe = OTC entries (codes OUTSIDE the listed-ETF prefix families)
    whose 基金名称 contains 联接 — the site's feeder share classes.
    ``--code`` bypasses the name filter but stays OTC-only: a listed
    ETF's --code routes to the backfill path, never to phantom feeder
    rows.
    """
    if code_filter:
        pairs = [(c, n) for c, n in raw_names.items()
                 if not is_listed_etf_code(c) and c == code_filter]
    else:
        pairs = [(c, n) for c, n in raw_names.items()
                 if not is_listed_etf_code(c) and "联接" in n]
    names = dict(pairs)
    return set(names), names


def load_nav_frames(universe: Set[str]) -> pd.DataFrame:
    """All feeder NAV rows: full-history archives + accumulated trend
    snapshots, deduped per (code, date). Columns: date (ISO str), code
    (bare), nav, day_pct."""
    frames: List[pd.DataFrame] = []
    for path in sorted(NAV_DIR.glob("huatai_pb_nav_archive_*.csv")):
        code = path.stem.replace("huatai_pb_nav_archive_", "").split(".")[0]
        if code not in universe:
            continue
        df = pd.read_csv(path, dtype={"code": str})
        if df.empty:
            continue
        frames.append(pd.DataFrame({
            "date": df["date"].astype(str),
            "code": code,
            "nav": pd.to_numeric(df["nav"], errors="coerce"),
            "day_pct": pd.to_numeric(df["day_pct"], errors="coerce"),
        }))
    for path in sorted(NAV_DIR.glob("huatai_pb_nav_trend_*.csv")):
        df = pd.read_csv(path, dtype={"code": str})
        if df.empty:
            continue
        df = pd.DataFrame({
            "date": df["date"].astype(str),
            # split guard: legacy trend snapshots suffixed listed rows
            "code": df["code"].astype(str).str.split(".").str[0],
            "nav": pd.to_numeric(df["nav"], errors="coerce"),
            "day_pct": pd.to_numeric(df["day_pct"], errors="coerce"),
        })
        frames.append(df[df["code"].isin(universe)])

    if not frames:
        return pd.DataFrame(columns=NAV_SRC_COLS)
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.dropna(subset=["nav"])
    combined = combined[combined["date"] != ""]
    # archive frames come first in `frames` → keep="last" lets the
    # freshest trend snapshot win on (code, date) conflicts
    combined = combined.drop_duplicates(subset=["code", "date"], keep="last")
    return combined.reset_index(drop=True)


def load_listed_archive_frames(code_filter: Optional[str]) -> pd.DataFrame:
    """Listed-ETF NAV archives: files whose FILE-NAME code is in the
    listed-ETF prefix families (51x/56x/58x SSE + 159 SZSE) — the checkin
    watchlist's listed members. Returns the full-history frame with BARE
    codes (legacy suffixed code columns are stripped on read); OTC
    (feeder) archives are the feeder path's.
    """
    frames: List[pd.DataFrame] = []
    for path in sorted(NAV_DIR.glob("huatai_pb_nav_archive_*.csv")):
        code = path.stem.replace("huatai_pb_nav_archive_", "").split(".")[0]
        if not is_listed_etf_code(code):
            continue
        if code_filter and code != code_filter:
            continue
        df = pd.read_csv(path, dtype={"code": str})
        if df.empty:
            continue
        frames.append(pd.DataFrame({
            "date": df["date"].astype(str),
            "code": code,
            "nav": pd.to_numeric(df["nav"], errors="coerce"),
            "day_pct": pd.to_numeric(df["day_pct"], errors="coerce"),
        }))
    if not frames:
        return pd.DataFrame(columns=NAV_SRC_COLS)
    out = pd.concat(frames, ignore_index=True)
    out = out.dropna(subset=["nav"])
    out = out[out["date"] != ""]
    return out.drop_duplicates(subset=["code", "date"]).reset_index(drop=True)


def synthesize_basic_rows(
    nav: pd.DataFrame, names: Dict[str, str]
) -> pd.DataFrame:
    """close-only NAV rows → etf_basic_stats-shaped frame.

    Vectorized per-code shift for prev_close/open; high = low = close;
    is_ohl_estimated = TRUE (documented synthesis — the fund site has no
    OHLC). Dates stay ISO strings; conversion happens at emission.
    """
    nav = nav.sort_values(["code", "date"], kind="stable").reset_index(drop=True)
    prev = nav.groupby("code", sort=False)["nav"].shift(1)
    return pd.DataFrame({
        "date": nav["date"],
        "code": nav["code"],
        "name": nav["code"].map(names),
        "prev_close": prev,
        "open": prev,
        "high": nav["nav"],
        "low": nav["nav"],
        "close": nav["nav"],
        "pct_change": nav["day_pct"],
        "pe": np.nan,
        "eps": np.nan,
        "is_ohl_estimated": True,
    })


class FeederNavBuild(DataBuild):
    """``python -m builds.etf.feeder_nav`` — huatai-pb NAV → etf tables
    (OTC feeder full series + listed checkin ETFs' missing-only history
    backfill)."""

    title = ("BUILD ETF NAV (huatai-pb.com: feeders + listed backfill)  ·  "
             "missing-data-only → stats.etf_identity + stats.etf_basic_stats")
    component = "feeder_nav"

    def add_arguments(self, parser) -> None:
        self.add_date_range_args(parser)
        self.add_code_arg(parser)

    def apply_args(self) -> None:
        self.apply_date_force_args()
        # OTC feeder codes are BARE; a listed ETF's --code routes to the
        # backfill path (resolve_universe stays OTC-only)
        self.args.resolved_code = self.resolve_code_filter(strip_suffix=True)

    def header_fields(self) -> dict:
        return {
            "NAV dir":     HUATAI_NAV_DIR,
            "Code filter": self.args.resolved_code or "(none — all funds)",
            "Forced date": str(self.forced_date) if self.forced_date else "(none)",
            "Today":       TODAY_STR,
        }

    async def run(self) -> None:
        code_filter = getattr(self.args, "resolved_code", None)
        forced = self.forced_date
        start = getattr(self.args, "start_date", None)
        end = getattr(self.args, "end_date", None)

        logger.info("[1/5] Resolving universes from latest trend snapshot …")
        raw_names = trend_names()
        universe, names = resolve_universe(code_filter, raw_names)
        logger.info("    [UNIVERSE] %d OTC feeder funds (%s …)", len(universe),
                    ", ".join(sorted(universe)[:5]) or "—")

        logger.info("\n[2/5] Reading NAV CSVs (archives + trend snapshots) …")
        nav = load_nav_frames(universe) if universe \
            else pd.DataFrame(columns=NAV_SRC_COLS)
        listed = load_listed_archive_frames(code_filter)
        for frame, label in ((nav, "feeder"), (listed, "listed")):
            if start:
                frame = frame[frame["date"] >= str(start)]
            if end:
                frame = frame[frame["date"] <= str(end)]
            if forced is not None:
                frame = frame[frame["date"] == forced.isoformat()]
            if label == "feeder":
                nav = frame
            else:
                listed = frame
        if not universe and listed.empty:
            logger.error("    [UNIVERSE] neither feeder funds nor listed "
                         "archives resolved (no trend snapshot, no listed-"
                         "code archive CSVs, or --code matches nothing)")
            sys.exit(1)
        if not nav.empty:
            logger.info("    [CSV] feeder: %d rows, %d codes, %s → %s",
                        len(nav), nav["code"].nunique(),
                        nav["date"].min(), nav["date"].max())
        if not listed.empty:
            logger.info("    [CSV] listed: %d rows, %d codes, %s → %s",
                        len(listed), listed["code"].nunique(),
                        listed["date"].min(), listed["date"].max())
        if forced is not None and nav.empty and listed.empty:
            logger.error("    [DATE MODE] no NAV rows on %s", forced)
            sys.exit(1)

        rows = synthesize_basic_rows(nav, names) if not nav.empty else \
            pd.DataFrame()

        logger.info("\n[3/5] Connecting to database and detecting missing pairs …")
        conn = await self.connect_db()
        try:
            if self.args.force:
                logger.info("    [DB] Force mode: deleting feeder "
                            "(exchange IS NULL) rows — listed exchange "
                            "rows are never purged")
                for tbl in ("stats.etf_basic_stats", "stats.etf_identity"):
                    await chunked_purge_async(
                        conn, tbl, where_sql="exchange IS NULL")
                existing: Set[Tuple[str, str]] = set()
            else:
                db_rows = await conn.fetch(
                    "SELECT date::text AS date, code "
                    "FROM stats.etf_identity WHERE exchange IS NULL")
                existing = {(r["date"], r["code"]) for r in db_rows}
            logger.info("    [DB] %s existing feeder (date, code) pairs "
                        "in stats.etf_identity", f"{len(existing):,}")

            # ---- feeder phase: missing ∪ newest REFRESH_DAYS days -------
            to_write = pd.DataFrame()
            if not rows.empty:
                pairs = pd.Series(list(zip(rows["date"], rows["code"])),
                                  index=rows.index)
                missing = ~pairs.isin(existing)
                # newest days re-upsert even when present (NAV corrections)
                refresh_from = (
                    pd.Timestamp(rows["date"].max())
                    - pd.Timedelta(days=REFRESH_DAYS)
                ).strftime("%Y-%m-%d")
                to_write = rows[missing | (rows["date"] >= refresh_from)]
                to_write = to_write.reset_index(drop=True)
            if len(to_write) == 0:
                logger.info("    [INFO] feeder NAV is up to date — no rows "
                            "to insert")
            else:
                logger.info("    [DB] %s feeder rows to upsert (existing "
                            "skipped, newest %d days refreshed)",
                            f"{len(to_write):,}", REFRESH_DAYS)
                n1, n2 = await self._upsert_nav_rows(conn, to_write)
                logger.info("    [DB] upserted %s identity rows, %s "
                            "basic_stats rows (all is_ohl_estimated=TRUE)",
                            f"{n1:,}", f"{n2:,}")

            # ---- listed phase: STRICTLY missing-only backfill -----------
            # The exchange OHLCV starts 2026-07-31; the fund-site archives
            # reach back to each fund's inception. Real exchange rows are
            # NEVER overwritten — no refresh window, no purge.
            if not listed.empty:
                lrows = synthesize_basic_rows(listed, raw_names)
                codes_l = sorted(lrows["code"].unique().tolist())
                db_rows = await conn.fetch(
                    "SELECT date::text AS date, code "
                    "FROM stats.etf_identity WHERE code = ANY($1::text[])",
                    codes_l)
                existing_l = {(r["date"], r["code"]) for r in db_rows}
                lpairs = pd.Series(list(zip(lrows["date"], lrows["code"])),
                                   index=lrows.index)
                lwrite = lrows[~lpairs.isin(existing_l)].reset_index(drop=True)
                if len(lwrite) == 0:
                    logger.info("    [LISTED] %d listed ETF(s) NAV history "
                                "fully covered — nothing to backfill",
                                len(codes_l))
                else:
                    logger.info("    [LISTED] backfilling %s NAV-history "
                                "rows for %d listed ETF(s) %s (existing "
                                "rows untouched)",
                                f"{len(lwrite):,}", len(codes_l),
                                ",".join(codes_l))
                    n3, n4 = await self._upsert_nav_rows(conn, lwrite)
                    logger.info("    [DB] backfilled %s identity rows, %s "
                                "basic_stats rows (all is_ohl_estimated="
                                "TRUE)", f"{n3:,}", f"{n4:,}")

            if self.args.force:
                durations = await analyze_tables_async(
                    conn, ("stats.etf_identity", "stats.etf_basic_stats"))
                if durations:
                    logger.info("    [ANALYZE] refreshed: " + ", ".join(
                        f"{t} ({d:.1f}s)" for t, d in durations.items()))
        finally:
            await conn.close()

    async def _upsert_nav_rows(self, conn, frame: pd.DataFrame) -> Tuple[int, int]:
        """Emit + upsert one NAV frame's identity + basic_stats rows.

        exchange: derived from the bare code's prefix family ('SS'/'SZ'
        for listed ETFs, NULL for OTC feeders).
        Returns (identity rows, basic_stats rows) upserted.
        """
        date_l = dates_as_date_list(frame["date"])
        recs = records_from_frame(frame, ["code", "name"])
        identity_rows = [
            {"date": d, "code": r["code"],
             "exchange": _exchange_of(r["code"]),
             "name": r["name"] or r["code"]}
            for d, r in zip(date_l, recs)
        ]
        n1 = await bulk_upsert_async(
            conn, "stats.etf_identity", identity_rows,
            key_columns=["code", "date"])
        basic_recs = records_from_frame(
            frame,
            ["code", "prev_close", "open", "high", "low", "close",
             "pct_change", "pe", "eps", "is_ohl_estimated"])
        basic_rows = [{"date": d, **r} for d, r in zip(date_l, basic_recs)]
        n2 = await bulk_upsert_async(
            conn, "stats.etf_basic_stats", basic_rows,
            key_columns=["code", "date"])
        return n1, n2


if __name__ == "__main__":
    FeederNavBuild().execute()
