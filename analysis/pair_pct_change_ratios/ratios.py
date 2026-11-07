"""Industry-vs-industry HEDGE RATIOS, two modes — compute + write for
analysis.pair_pct_change_ratios.

THREE CODES per row (2026-10-17 rework): a subject industry A (the
hedging instrument), a benchmark industry B (the position to hedge),
and a broad-market benchmark index M (the offset market). The stored
value is the MINIMUM-VARIANCE HEDGE RATIO over the trailing window:

  h* = -Cov(A, B) / Var(A)

i.e. how many units of A to hold per unit long of B so the combined
position's variance is minimal. h* > 0 (negative covariance — the
"opposite moves" regime) = BUY that many units of A; h* < 0 = short it.
Cov/Var via rolling-mean identities (mAB - mA*mB etc.; the k/(k-1)
sample correction cancels in the ratio) — cudf-native, no division by
single-day values (the earlier daily-ratio forms blew up to ±7,000 on
near-flat benchmark prints, e.g. 000015 on 2024-10-23 at −0.0003%).

TWO MODES (2026-10-17 study, 科技 vs 红利 full history):
  hedge_raw_ma{N}d     RAW: A and B are the industries' aggregate daily
                       pct changes. Raw corr(科技,红利) = +0.42 — the
                       common market factor dominates, so raw hedge
                       ratios are typically NEGATIVE (short A).
  hedge_offset_ma{N}d  MARKET-OFFSET: A' = A − M, B' = B − M, the
                       excess-over-market daily pct changes (M = the
                       benchmark_market INDEX's own-calendar pct). The
                       market factor cancels — offset corr(科技,红利)
                       = −0.49 vs 000300, −0.70 vs 000001 — and the
                       hedge ratio answers "how much A to BUY per unit
                       B" in the opposite-moves regime (h₆₀ ≈ +0.8).

  ind_pct(i, d)  = null-skipped MEAN of the member indexes' OWN-calendar
                   daily pct changes at date d (active type='index'
                   members with closes; missing member days SKIPPED,
                   never zero-filled).
  m(d)           = benchmark_market index's OWN-calendar daily pct.

NULL RULES (both modes, corr-parity POOLED CALENDAR — the offsets
convention): each mode's windows are the trailing N COMMON trading
days of that mode (raw: dates where both industries have aggregates;
offset: dates where both plus the market print exist). A date only one
side trades is NOT a day of the pair's calendar — not an in-window
null — so the LIVE frontier sample is always a full window. Null data
shrinks the common-day history: a pair with fewer than ceil(0.9·N)
complete days (young/sparse pairs — the >10% null rule) emits NULL for
that window. Var(A) at/below the variance floor (a near-motionless
hedging instrument) emits NULL; |h*| > 9999.999999 dropped (the
NUMERIC(10,6) bound backstop).

Grid: values materialized on stride-grid dates of the GLOBAL index
calendar (grid position % interval = interval - 1, default 20;
"interval" is a PK member so strides coexist), recomputed + upserted —
never a daily emit — PLUS ONE mutable off-grid LIVE sample at the
calendar frontier whenever the frontier is past the last grid date
(the offsets table's convention: the freshest windows plot at the
frontier, so the chart's right edge is never up to a stride-interval
stale — stride 20 ≈ a month). Each incremental run refreshes the
frontier sample and purges the previous frontier's leftover points.

Rows: code = subject industry_id (hedge instrument), benchmark_code =
exposure industry_id, benchmark_market = offset market index code
(default '000300' — the only market materialized in
analysis_composites.industry_corr_benchmark_offsets), grain = 1
('industry', static.label_dim pair_grain) on every row; self-industry
pairs excluded (h ≡ 1 by construction). ~115 industries → ~13K pairs ×
~88 grid dates × |market set| rows.

COMPUTE LAYOUT (cudf GPU-first; no host-numpy boundary, no per-group
loops): typed fetch (epoch::float8 -> datetime64[ns], float64 closes,
str codes + industry_id as group/merge keys only) -> ONE whole-frame
grouped mean for ind_pct + the market index pct -> pair frame over the
PER-PAIR UNION of the two industries' dates (two crossed legs
concatenated, groupby-max coalescing), LEFT-joined with each market's
pct -> pairwise-complete A/B columns per mode -> ONE whole-frame
grouped rolling pass (means of A, B, AB, AA) keyed by (code,
benchmark_code, benchmark_market) -> grid-date isin filter -> sanitize
-> ONE write.
"""
from __future__ import annotations

import datetime
import time
from typing import Dict, List, Optional, Sequence, Set

import numpy as np
import pandas as pd

from _common._holidays_and_weekdays import recent_trading_day_cutoff
from _common.build_commons import rec_col
from _common.db_commons import (
    analyze_tables_async,
    batched_copy_by_key_async,
    copy_or_upsert_split_async,
    truncate_table_async,
)
from _common.df_utils import (
    epoch_ns_array,
    grouped_rolling_agg,
    grouped_shift,
    sanitize_for_db_insert,
)
from _common.labels import PAIR_GRAIN_CODE
from _common.sql_views import render_sql_view

from analysis._common import upsert_analysis_identity
from analysis.pair_pct_change_ratios.config import (
    ANALYSIS_DESCRIPTION,
    ANALYSIS_NAME,
    INTERVAL_DAYS,
    MARKET_BENCHMARKS,
    RATIO_WINDOWS,
    SOURCE_TABLE,
    TABLE,
)

import logging
logger = logging.getLogger(__name__)

# hedge-ratio column names per window, per mode.
HEDGE_RAW_COLS: List[str] = [f"hedge_raw_ma{N}d" for N in RATIO_WINDOWS]
HEDGE_OFFSET_COLS: List[str] = [
    f"hedge_offset_ma{N}d" for N in RATIO_WINDOWS]
VALUE_COLS: List[str] = HEDGE_RAW_COLS + HEDGE_OFFSET_COLS

# Null handling (2026-10-17): every window statistic counts
# PAIRWISE-COMPLETE days only (null data skipped, never zero-filled); a
# window whose null share EXCEEDS 10% emits NULL (skip the period) —
# min_periods = ceil(0.9·N) per window, applied to each mode's own
# complete-day count.
_MIN_PERIODS: Dict[int, int] = {N: -(-9 * N // 10) for N in RATIO_WINDOWS}

# The rolling needs max(RATIO_WINDOWS) trailing common days before the
# earliest sampled grid date.
LOOKBACK_TRADING_DAYS: int = max(RATIO_WINDOWS)  # 255

# NUMERIC(10,6) bound backstop — huge ratios drop to NULL instead of
# erroring the bulk write.
_VALUE_BOUND: float = 9999.999999

# Subject-variance floor (daily pct std below ~1bp-5 -> Var 1e-16): a
# near-motionless hedging instrument makes the hedge ratio explode;
# such windows stay NULL.
_VAR_FLOOR: float = 1e-16

# The LATEST grid date's windows stay mutable while closes can still
# land for it (cross-border indexes publish a day+ behind). Corr parity
# (the offsets table's RECENT_STRIDE_GRACE_DAYS): re-target the latest
# stride date while it is within this many TRADING days of the calendar
# frontier; further back it is frozen.
RECENT_STRIDE_GRACE_TRADING_DAYS: int = 10

_GRAIN_INDUSTRY = PAIR_GRAIN_CODE["industry"]


# ---------------------------------------------------------------------------
#  Fetch primitives
# ---------------------------------------------------------------------------
async def fetch_grid_dates(conn, stride: int) -> List[datetime.date]:
    """Grid dates (every ``stride`` trading days) on the GLOBAL index
    calendar (stats.index_basic_stats distinct dates) — the corr grid,
    parameterized.

    Returned as python dates (set math + lookback anchoring); callers cast
    to datetime64[ns] for the frame isin.
    """
    # grid_dates view: stride-grid dates on the global index calendar.
    view = render_sql_view(
        "analysis/pair_pct_change_ratios/ratios/grid_dates", stride=stride,
    )
    rows = await conn.fetch(view.sql, *view.params)
    return [r["date"] for r in rows]


async def fetch_calendar_frontier(conn) -> Optional[datetime.date]:
    """Latest date on the global index calendar (closes may still land
    for grid dates near it)."""
    # calendar_frontier view.
    return await conn.fetchval(
        render_sql_view(
            "analysis/pair_pct_change_ratios/ratios/calendar_frontier",
        ).sql
    )


async def _fetch_closes_pct(
    conn, start_date: Optional[datetime.date],
    extra_codes: Sequence[str] = (),
) -> pd.DataFrame:
    """Typed daily closes + OWN-calendar pct change + industry_id for the
    WHOLE active index universe, PLUS the raw series of ``extra_codes``
    (the offset-market benchmark indexes — their own pct, no industry
    aggregation). One fetch feeds every industry aggregate and the
    market factors.

    GPU-safe frame construction (the cross_stats fetch convention): epoch
    ::float8 -> datetime64[ns], closes as float64, codes as python str
    lists. Sorted by (code, date) so the grouped shift sees each series'
    own calendar order. industry_id rides the frame as a str group key
    only (never computed on); extra codes carry industry_id "".
    """
    params: list = []
    where = ""
    if start_date is not None:
        params.append(start_date)
        where += f" AND b.date >= ${len(params)}"

    rows = await conn.fetch(f"""
        SELECT b.code,
               sc.industry_id,
               extract(epoch from b.date)::float8 AS date,
               b.close::float8 AS close
        FROM {SOURCE_TABLE} b
        LEFT JOIN stats.sec_classification sc
            ON sc.code = b.code AND sc.type = 'index'
        WHERE (sc.is_active = TRUE
                 AND sc.industry_id IS NOT NULL AND sc.industry_id <> ''
               OR b.code = ANY(${len(params) + 1}))
          AND b.close IS NOT NULL{where}
        ORDER BY b.code, b.date
    """, *params, sorted(set(extra_codes)))
    if not rows:
        return pd.DataFrame(
            columns=["code", "industry_id", "date", "pct"])

    df = pd.DataFrame({
        "code": [r["code"] for r in rows],
        "industry_id": [r["industry_id"] or "" for r in rows],
        "date": epoch_ns_array(rec_col(rows, "date")),
        "close": np.asarray(
            [float(r["close"]) if r["close"] is not None else np.nan
             for r in rows], dtype="float64"),
    })
    # Daily pct change on each series' OWN calendar (diff before any date
    # join — a date missing on one side never corrupts the other's lag).
    df = grouped_shift(df, "code", ["close"], ["prev_close"])
    df["pct"] = df["close"] / df["prev_close"] - 1.0
    return df.drop(columns=["close", "prev_close"])


async def _fetch_industry_universe(conn) -> Set[str]:
    """Industry_ids with at least one active type='index' member carrying
    a close — BOTH the subject and the benchmark universe (any industry
    can be either side; self-industry pairs excluded)."""
    # industry_universe view: industries with an active member close.
    view = render_sql_view(
        "analysis/pair_pct_change_ratios/ratios/industry_universe",
    )
    rows = await conn.fetch(view.sql)
    return {r["industry_id"] for r in rows}


# ---------------------------------------------------------------------------
#  Incremental targeting
# ---------------------------------------------------------------------------
async def _plan_target_grid_dates(
    conn, stride: int, grid_dates: List[datetime.date],
    *, force: bool, filtered: bool, universe: Set[str],
    subjects: Sequence[str],
) -> List[datetime.date]:
    """Which dates this run must (re)materialize.

    force / filtered (--code): every grid date (truncate-and-rebuild /
    recompute+upsert the chosen subject industries).
    incremental: grid dates with NO row yet for the stride, plus the
    LATEST grid date while it is within
    RECENT_STRIDE_GRACE_TRADING_DAYS trading days of the calendar
    frontier (corr parity — late-landing closes make its windows
    mutable), plus the LIVE FRONTIER DATE whenever it is past the last
    grid date (the mutable off-grid trailing sample — the offsets
    table's convention: the freshest windows plot at the frontier so
    the chart is never up to a stride-interval stale; stride 20 ≈ a
    month); when the universe grew since the last run, the whole grid
    is re-targeted so the new industries get full history.

    Grid dates with no subject-industry member close (A-share holidays
    carried by the cross-border-index global calendar) are dropped —
    they can never yield a row, and re-attempting them every run would
    never resolve (the cross_stats runner's unwritable-dates skip).
    """
    if force or filtered or not grid_dates:
        return list(grid_dates)

    rows = await conn.fetch(
        f'SELECT DISTINCT date FROM {TABLE} WHERE "interval" = $1 '
        f"AND grain = {_GRAIN_INDUSTRY}", stride)
    existing = {r["date"] for r in rows}
    target = [d for d in grid_dates if d not in existing]

    frontier = await fetch_calendar_frontier(conn)
    if frontier is not None and frontier > grid_dates[-1]:
        gap = await conn.fetchval(
            f"SELECT COUNT(DISTINCT date) FROM {SOURCE_TABLE} "
            f"WHERE date > $1 AND date <= $2", grid_dates[-1], frontier)
        if gap is not None and gap <= RECENT_STRIDE_GRACE_TRADING_DAYS:
            target.append(grid_dates[-1])  # mutable latest stride date
        target.append(frontier)  # mutable live off-grid frontier sample
    target = sorted(set(target))

    rows = await conn.fetch(
        f'SELECT DISTINCT benchmark_code FROM {TABLE} '
        f'WHERE "interval" = $1 AND grain = {_GRAIN_INDUSTRY}', stride)
    present = {r["benchmark_code"] for r in rows}
    missing = universe - present
    if missing:
        logger.info(f"    -> {len(missing)} benchmark industries absent "
              f"from {TABLE} (universe grew) — healing FULL history")
        target = list(grid_dates)

    rows = await conn.fetch(f"""
        SELECT DISTINCT b.date FROM {SOURCE_TABLE} b
        JOIN stats.sec_classification sc
            ON sc.code = b.code AND sc.type = 'index'
        WHERE b.close IS NOT NULL
          AND sc.industry_id = ANY($1)
          AND b.date = ANY($2)
    """, sorted(subjects), target)
    writable = {r["date"] for r in rows}
    dropped = [d for d in target if d not in writable]
    if dropped:
        logger.info(f"    -> {len(dropped)} grid dates have no subject "
              f"closes (holiday placeholders) — unwritable, skipped")
    return [d for d in target if d in writable]


# ---------------------------------------------------------------------------
#  Runner
# ---------------------------------------------------------------------------
async def run_pair_pct_change_ratios(
    conn, *, force: bool = False, stride: int = INTERVAL_DAYS,
    codes: Optional[Sequence[str]] = None,
) -> None:
    """Materialize analysis.pair_pct_change_ratios on the stride grid.

    Args:
      conn: asyncpg connection (caller owns lifecycle).
      force: truncate + full recompute when True.
      stride: grid stride in trading days (PK member "interval").
      codes: filtered mode — recompute ALL grid dates for these subject
        INDUSTRY ids only (UI refresh). None = the full industry
        universe.
    """
    t0 = time.time()
    logger.info(f"\n[0/3] Resolving industry universes + stride grid "
          f"(stride={stride})...")
    grid_dates = await fetch_grid_dates(conn, stride)
    if not grid_dates:
        logger.info("    -> empty global calendar; nothing to do.")
        return

    universe = await _fetch_industry_universe(conn)
    subjects = sorted(universe)
    if codes is not None:
        unknown = [c for c in codes if c not in universe]
        if unknown:
            logger.warning(f"    -> WARNING: industry ids with no active "
                  f"member closes (ignored): {', '.join(unknown)}")
        subjects = [c for c in subjects if c in set(codes)]
        if not subjects:
            logger.info("    -> no subjects requested; nothing to do.")
            return

    filtered = codes is not None
    if force:
        logger.info(f"\n[0/3] Force mode: truncating {TABLE}...")
        await truncate_table_async(conn, TABLE)

    target = await _plan_target_grid_dates(
        conn, stride, grid_dates, force=force, filtered=filtered,
        universe=universe, subjects=subjects,
    )
    logger.info(f"    -> {len(subjects)} subject industries (= benchmark "
          f"universe) of {len(universe)}; markets "
          f"{', '.join(MARKET_BENCHMARKS)}; {len(target)} of "
          f"{len(grid_dates)} grid dates to materialize "
          f"({'all' if len(target) == len(grid_dates) else 'incremental'})")
    if not target:
        logger.info(f"    -> {TABLE} up to date; nothing to do.")
        await upsert_analysis_identity(
            conn, name=ANALYSIS_NAME, detail_name=ANALYSIS_NAME,
            description=ANALYSIS_DESCRIPTION)
        return

    # ---- Fetch member closes + market benchmark closes with a
    # 255-trading-day lookback before the earliest sampled grid date.
    start_date: Optional[datetime.date] = recent_trading_day_cutoff(
        LOOKBACK_TRADING_DAYS, ref=min(target))
    logger.info(f"\n[1/3] Fetching closes from {start_date.isoformat()} "
          f"(lookback {LOOKBACK_TRADING_DAYS} trading days)...")
    closes_pct = await _fetch_closes_pct(conn, start_date, MARKET_BENCHMARKS)
    if closes_pct.empty:
        logger.info("    -> no closes in the lookback window; nothing "
          "to do.")
        return
    grid_ns = np.asarray(grid_dates, dtype="datetime64[D]").astype(
        "datetime64[ns]")
    # Sample set = grid dates + the LIVE FRONTIER DATE when it is past
    # the last grid date (the mutable off-grid trailing sample — keeps
    # the chart current between grid dates; a stride of 20 trading days
    # is otherwise up to a month stale at the right edge).
    frontier = await fetch_calendar_frontier(conn)
    sample_ns = grid_ns
    if frontier is not None and frontier > grid_dates[-1]:
        frontier_ns = np.asarray(
            [frontier], dtype="datetime64[D]").astype("datetime64[ns]")
        sample_ns = np.concatenate([grid_ns, frontier_ns])

    # ---- Industry aggregates (null-skipped member-mean pct) + market
    # index pct series (own calendars; market codes carry industry_id "").
    logger.info(f"\n[2/3] Computing industry x industry hedge ratios "
          f"(raw + offset modes) + writing {TABLE}...")
    defined = closes_pct[closes_pct["pct"].notna()]
    ind = (defined[defined["industry_id"] != ""]
           .groupby(["industry_id", "date"])["pct"]
           .mean().reset_index())
    ind.columns = ["industry_id", "date", "pct"]
    ind = ind.sort_values(["industry_id", "date"]).reset_index(drop=True)
    market = (defined[defined["code"].isin(set(MARKET_BENCHMARKS))]
              [["code", "date", "pct"]]
              .rename(columns={"code": "benchmark_market"}))

    # Pair frame over the PER-PAIR UNION of the two sides' own dates
    # (NOT the global union of all industries — a date only a third
    # industry trades would wrongly count as a null day for every
    # pair). Two crossed legs concatenated; a (pair, date) group's max
    # coalesces the legs (each side's value is non-null on exactly one
    # leg — groupby max skips NaN), leaving NaN where that side has no
    # industry-day. Self-pairs drop after the groupby.
    sub_ind = ind[ind["industry_id"].isin(set(subjects))]
    bench_codes = ind[["industry_id"]].drop_duplicates().rename(
        columns={"industry_id": "benchmark_code"})
    sub_codes = sub_ind[["industry_id"]].drop_duplicates().rename(
        columns={"industry_id": "code"})
    sub_codes["_k"] = np.int64(1)
    bench_codes["_k"] = np.int64(1)
    leg_s = sub_ind.rename(
        columns={"industry_id": "code", "pct": "pct_code"})
    leg_b = ind.rename(
        columns={"industry_id": "benchmark_code", "pct": "pct_bench"})
    leg_s["_k"] = np.int64(1)
    leg_b["_k"] = np.int64(1)
    leg_s = leg_s.merge(bench_codes, on="_k").drop(columns=["_k"])
    leg_b = leg_b.merge(sub_codes, on="_k").drop(columns=["_k"])
    frame = pd.concat([leg_s, leg_b], ignore_index=True)
    frame = (frame.groupby(["code", "benchmark_code", "date"])
             .agg({"pct_code": "max", "pct_bench": "max"})
             .reset_index())
    frame = frame[frame["code"] != frame["benchmark_code"]]

    # Cross with the market set (each market its own row block), then
    # LEFT-join the market pct on date (NaN on days the market index
    # has no print).
    markets = pd.DataFrame({
        "benchmark_market": list(MARKET_BENCHMARKS)})
    markets["_k"] = np.int64(1)
    frame["_k"] = np.int64(1)
    frame = frame.merge(markets, on="_k").drop(columns=["_k"])
    frame = frame.merge(market, on=["benchmark_market", "date"],
                        how="left")

    # Corr-parity POOLED-CALENDAR windows (the offsets convention): each
    # mode's rolling frame carries ONLY that mode's COMPLETE days — RAW:
    # dates where BOTH industries have aggregates; OFFSET: dates where
    # both plus the market print exist. A date only one side trades
    # (e.g. an HK member printing through a CN holiday) is NOT a day of
    # the pair's calendar — not an in-window null — so windows are the
    # trailing N COMMON trading days and the LIVE frontier sample is
    # always full. Null data only shrinks the common-day history: a
    # pair with fewer than ceil(0.9·N) complete days (young/sparse
    # pairs) emits NULL for that window (the >10%-null rule).
    keys = ["code", "benchmark_code", "benchmark_market"]
    both = frame["pct_code"].notna() & frame["pct_bench"].notna()
    all3 = both & frame["pct"].notna()
    mode_frames = {
        "raw": frame[both][[*keys, "date", "pct_code", "pct_bench"]],
        "off": frame[all3][[*keys, "date", "pct_code", "pct_bench",
                            "pct"]],
    }
    results = []
    for pre, cols in (("raw", HEDGE_RAW_COLS), ("off", HEDGE_OFFSET_COLS)):
        mf = mode_frames[pre].sort_values(
            [*keys, "date"]).reset_index(drop=True)
        mf["a"] = mf["pct_code"] - (mf["pct"] if pre == "off" else 0.0)
        mf["b"] = mf["pct_bench"] - (mf["pct"] if pre == "off" else 0.0)
        mf = mf.drop(columns=["pct_code", "pct_bench", "pct"],
                     errors="ignore")
        mf["ab"] = mf["a"] * mf["b"]
        mf["aa"] = mf["a"] * mf["a"]

        # The stored value per window: the MINIMUM-VARIANCE hedge ratio
        # h* = -Cov(A, B) / Var(A) over the trailing N common days (A =
        # the subject industry = the hedging instrument, B = the
        # benchmark industry = the position). h* > 0 (window covariance
        # negative — the "opposite moves" regime, typically the OFFSET
        # mode) = BUY that many units of the subject; h* < 0 = short
        # it. Cov/Var via rolling-mean identities (mAB - mA*mB etc.);
        # the k/(k-1) sample correction cancels in the ratio.
        for n, col in zip(RATIO_WINDOWS, cols):
            mp = _MIN_PERIODS[n]
            m_a = grouped_rolling_agg(mf, keys, "a", n,
                                      min_periods=mp, sort=False)
            m_b = grouped_rolling_agg(mf, keys, "b", n,
                                      min_periods=mp, sort=False)
            m_ab = grouped_rolling_agg(mf, keys, "ab", n,
                                       min_periods=mp, sort=False)
            m_aa = grouped_rolling_agg(mf, keys, "aa", n,
                                       min_periods=mp, sort=False)
            cov = m_ab - m_a * m_b
            var_a = m_aa - m_a * m_a
            hedge = (cov / var_a.where(var_a > _VAR_FLOOR)) * -1.0
            mf[col] = hedge.where(np.isfinite(hedge))
        results.append(mf[[*keys, "date", *cols]])

    out = (results[0].merge(results[1], on=[*keys, "date"], how="outer")
           .sort_values([*keys, "date"]).reset_index(drop=True))
    out = out[out["date"].isin(sample_ns)][
        ["code", "benchmark_code", "benchmark_market", "date",
         *VALUE_COLS]].copy()
    # Constant grain on every row (industry x industry is the only
    # grain); attached at the write boundary, never on the compute frame.
    out["interval"] = stride
    out["grain"] = _GRAIN_INDUSTRY
    for col in VALUE_COLS:
        out[col] = out[col].where(out[col].abs() <= _VALUE_BOUND)

    rows = sanitize_for_db_insert(
        out[["code", "benchmark_code", "benchmark_market", "interval",
             "date", "grain", *VALUE_COLS]],
        numeric_cols=VALUE_COLS, round_to=6, date_cols=["date"],
    )
    total = 0
    if rows:
        if force:
            total = await batched_copy_by_key_async(
                conn, TABLE, rows, key="code", label="pair-pct-ratios")
        else:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, TABLE, rows,
                key_columns=["code", "benchmark_code", "benchmark_market",
                             "interval", "date", "grain"],
                date_column="date",
            )
            total = n_copied + n_upserted
    logger.info(f"    -> wrote {total:,} hedge-ratio grid rows across "
          f"{out['code'].nunique() if not out.empty else 0} subject "
          f"industries")

    # Purge STALE off-grid points: as the frontier moves, only the
    # CURRENT frontier date stays as the live sample; anything else
    # past the last grid date is a previous frontier's leftover.
    if not force and frontier is not None and frontier > grid_dates[-1]:
        status = await conn.execute(
            f'DELETE FROM {TABLE} WHERE "interval" = $1 '
            f"AND grain = {_GRAIN_INDUSTRY} AND date > $2 AND date <> $3",
            stride, grid_dates[-1], frontier)
        logger.info(f"    -> stale off-grid purge: {status}")

    # Refresh planner statistics after the bulk write (stale stats turn
    # the API's grain/interval-filtered universe scans into seq scans —
    # the db-sql-write post-write rule).
    await analyze_tables_async(conn, [TABLE])

    await upsert_analysis_identity(
        conn, name=ANALYSIS_NAME, detail_name=ANALYSIS_NAME,
        description=ANALYSIS_DESCRIPTION)
    logger.info(f"\n  pair_pct_change_ratios: {total:,} industry x "
          f"industry hedge-ratio rows written (2 modes, markets "
          f"{', '.join(MARKET_BENCHMARKS)}, stride {stride}, "
          f"{len(target)}/{len(grid_dates)} grid dates); "
          f"wall time: {time.time() - t0:.1f}s")
