"""Internal RSI tail-episode (is_typical) step for analysis.mov_ave_spread.

Per-window tail-episode population flags for the 6 Wilder windows —
written back onto analysis.mov_ave_rsi (is_typical_{W}days, one column
per window) on every ranks rebuild. THE re-entry semantics live here,
in the analysis layer: the signal tiers (analysis_signals'
signal_curves / signal_thresholds) consume the stored flags instead of
running their own gap logic.

=======================================================================
  FINANCIAL SEMANTICS
=======================================================================

For each (sec_type, code, window W):

  band membership   ratio = rank_rsi_{W}days / MAX(rank_rsi_{W}days)
                    over the code's own full RSI history (the
                    mov_ave_rsi_ranks semantics). The day is IN THE TOP
                    TAIL (overbought) when ratio <= RSI_TYPICAL_BAND
                    (0.25), IN THE BOTTOM TAIL (oversold) when
                    ratio >= 1 - RSI_TYPICAL_BAND, OUTSIDE both bands
                    otherwise.

  episode sequence  within one side's tail, order the days by date and
                    measure the GAP to the previous tail day in the
                    code's own trading-day positions (gap 1 = the
                    previous trading day was also a tail day).

  classification    every IN-BAND day carries its episode PASS's flag
                    (SIDE-MIRRORED — the amplified tail event differs
                    by side; equity asymmetry: sharp falls revert,
                    sharp rises continue):

                    BOTTOM (buy) side:
                      first tail day after > ISOLATED days out
                                             -> TRUE  (isolated
                                               first-touch episode —
                                               the capitulation; the
                                               amplified rebound)
                      2 <= gap <= K + 1      -> FALSE (FLICKER re-entry)
                      K + 1 < gap <= ISOLATED+1 -> FALSE (CONTINUATION
                                               re-entry — the falling
                                               knife)

                    TOP (sell) side:
                      K + 1 < gap <= ISOLATED+1 -> TRUE  (RE-TEST — a
                                               fresh episode while the
                                               previous overbought
                                               episode ended within the
                                               window; the double top,
                                               the amplified sell)
                      2 <= gap <= K + 1      -> FALSE (FLICKER re-entry)
                      gap > ISOLATED + 1     -> FALSE (ISOLATED
                                               breakout — momentum
                                               ignition after calm;
                                               the sell backfires)

                    The gap-1 continuation days inherit their pass's
                    registration flag (forward-filled within the
                    episode: each episode's start day carries its own
                    pass flag, so the fill never crosses episodes) —
                    the deep days ride their pass's side in the
                    two-state consumers (the UI's typical/re-entry
                    populations); in-band MEMBERSHIP itself is the
                    episode-size column's job (NULL = out-of-band; the
                    run/size boundary stays K — a re-test or
                    continuation episode is its OWN episode for the
                    1/size weighting).

  episode size      beside the flags, every IN-BAND day (starts,
                    re-entries AND the gap-1 continuations) carries
                    typical_ep_size_{W}days = its episode's trading-day
                    count over the full history (NULL out-of-band).
                    The signal CURVES' population marker + weight:
                    the 2026-10-06 tails study found registration-only
                    populations sit at the band edge by construction
                    (both TRUE and FALSE are entry events — mean depth
                    ~19%, the q <= 10% tail unmeasured) while the deep
                    days are exactly the continuations; weighting every
                    in-band day 1/size makes each episode contribute
                    total weight exactly 1 (the depth response counts
                    EPISODES, not days).

  K = RSI_REENTRY_EXIT_DAYS = 5. The 2026-10-06 exit-gap study
  (analysis_signals' temp_scripts/study_reentry_gap.py) found 18.1% of
  top-1% inter-day gaps were exactly 4-trading-day market-holiday
  closures and absorbing exits <= 5 days is past the knee (K = 8/10
  change nothing further).

  ISOLATED = RSI_TYPICAL_ISOLATED_DAYS = 20 (2026-10-07; the
  same-day sell-side mirror). The TYPICAL pass is side-mirrored: on
  the BUY side it is the ISOLATED first touch (> 20 trading days out
  of the tail — the capitulation, amplified rebound); on the SELL
  side it is the RE-TEST (the previous overbought episode ended
  within the window — the double top, the amplified sell), isolated
  breakouts after calm being momentum ignitions where sells backfire.
  Episode sizes unaffected.

=======================================================================
  REBUILD SEMANTICS (band membership SHIFTS — wholesale per sec_type)
=======================================================================

Band membership is the day's position in the code's FULL-history rank
distribution, so every new day's RSI can move any day's ratio across
the band edge — the same wholesale-rebuild rationale as
mov_ave_rsi_ranks itself. The flags are rewritten WHOLESALE per
sec_type on every ranks rebuild: bulk-upsert of the (key + flag
columns) onto analysis.mov_ave_rsi (an UPSERT, not delete+insert — the
row's RSI values must survive).

=======================================================================
  GPU LAYOUT (cudf.pandas)
=======================================================================

Whole-frame vectorized: one per-code trading-day position (sorted
cumcount), per-window band masks from the rank ratios (transform-max
denominator), then per (code, side) grouped shift for the gaps. Flags
stay float64 (1.0 / 0.0 / NaN) inside the compute frame — the
boolean/NULL split happens ONCE at the emit boundary in raw host numpy
(the sanctioned DB-literal boundary; a nullable-boolean column would
defeat the upsert row renderer the same way nullable Int64 defeats the
CSV COPY renderer).
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from _common.db_commons import bulk_upsert_async
from _common.labels import SEC_TYPE_CODE
from _common.df_utils import host_array
from analysis.mov_ave_spread.steps.rsi import RSI_WINDOWS

import logging
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

RSI_TABLE = "analysis.mov_ave_rsi"

# The band half-width: a day is in a tail when its full-history rank
# ratio is within the outer RSI_TYPICAL_BAND of either extreme.
RSI_TYPICAL_BAND = 0.25

# The re-entry invalidation window (trading days out of the tail
# tolerated within ONE episode) — see the module docstring.
RSI_REENTRY_EXIT_DAYS = 5

# Trading days OUT of the tail required for a fresh episode start to
# be an ISOLATED first touch (2026-10-07 start-gap study, the 10 study
# indices). The pass semantics are SIDE-MIRRORED (equity markets are
# asymmetric — sharp falls mean-revert, sharp rises continue):
#   BUY (bottom) side: the ISOLATED start (out of band > ISOLATED
#     days) is the TYPICAL pass — the capitulation after a month of
#     calm carries the amplified rebound (W=3 starts at gap 31-60:
#     +2.36% blended, monotonically rising with absence; starts at
#     gap 7-10: -0.55% — the falling-knife re-crossings).
#   SELL (top) side: the RE-TEST start (K < gap <= ISOLATED — a fresh
#     episode whose previous overbought episode ended within the
#     window; the double top) is the TYPICAL pass — the amplified sell
#     (W=14: +2.28% vs +1.21% ep-weighted); flickers AND isolated
#     breakouts (gap > ISOLATED, momentum ignition after calm — price
#     rises a further +1.2% at 20d) are the noisy population.
# Episode SIZES are unaffected (the run boundary stays
# RSI_REENTRY_EXIT_DAYS) — only the pass flags split.
RSI_TYPICAL_ISOLATED_DAYS = 20

FLAG_COLUMNS = [f"is_typical_{w}days" for w in RSI_WINDOWS]
SIZE_COLUMNS = [f"typical_ep_size_{w}days" for w in RSI_WINDOWS]


# ---------------------------------------------------------------------------
#  Compute (whole-frame band masks + grouped gaps on the cudf proxy)
# ---------------------------------------------------------------------------

def compute_rsi_typical(ranks: pd.DataFrame) -> pd.DataFrame:
    """Add is_typical_{W}days + typical_ep_size_{W}days columns for the
    6 Wilder windows.

    Args:
      ranks: frame carrying (sec_type, code, date, rank_rsi_{W}days —
          float64, NaN = unranked) — compute_rsi_ranks' output.

    Returns:
      A NEW frame with (sec_type, code, date + the 12 columns). Flags
      stay float64 (1.0 = the TYPICAL pass's days — the buy side's
      isolated first touch / the sell side's re-test, 0.0 = the
      re-entry passes' days — flickers, falling knives and isolated
      breakouts, NaN = out of band) and sizes float64 (episode day
      count on every in-band day, NaN out-of-band) — the boolean/NULL
      split happens at the emit boundary only.
    """
    if ranks.empty:
        return ranks
    grp = ["sec_type", "code"]
    rank_cols = [f"rank_rsi_{w}days" for w in RSI_WINDOWS]
    base = ranks[["sec_type", "code", "date", *rank_cols]].copy()
    # the code's own trading-day position (the gap's unit — calendar
    # gaps are not rows, same convention as the signal tiers' _t)
    base = base.sort_values([*grp, "date"]).reset_index(drop=True)
    base["_p"] = base.groupby(grp, sort=False).cumcount()
    for w, rcol in zip(RSI_WINDOWS, rank_cols):
        fcol = f"is_typical_{w}days"
        scol = f"typical_ep_size_{w}days"
        denom = base.groupby(grp, sort=False)[rcol].transform("max")
        ratio = base[rcol] / denom
        in_top = ratio <= RSI_TYPICAL_BAND
        in_bot = ratio >= 1.0 - RSI_TYPICAL_BAND
        flag, size = _classify_side(base, in_top, 0)
        flag_bot, size_bot = _classify_side(base, in_bot, 1)
        flag = flag.where(in_top, flag_bot)
        size = size.where(in_top, size_bot)
        base[fcol] = flag.where(in_top | in_bot, np.nan)
        base[scol] = size.where(in_top | in_bot, np.nan)
    return base[["sec_type", "code", "date", *FLAG_COLUMNS, *SIZE_COLUMNS]]


def _classify_side(
    base: pd.DataFrame, in_side: pd.Series, side: int,
) -> tuple[pd.Series, pd.Series]:
    """The gap classification for one side's tail days: (flag, size)
    index-aligned to base — SIDE-MIRRORED pass flags (see the module
    docstring): buy side flag 1.0 = the isolated first-touch pass's
    days (> ISOLATED out of the tail before the episode's first day,
    or the code's first tail day), sell side flag 1.0 = the re-test
    pass's days (a fresh episode whose previous same-side episode
    ended within ISOLATED days — the double top); every other
    registration (flicker re-qualifications within K, the other
    side's continuation/isolated starts) + their gap-1 continuations
    carry 0.0; NaN = out of band. size = the episode's trading-day
    count on EVERY band day of the side (the run of band days whose
    inter-day gaps stay within the re-entry window — one episode's
    days share the size, so 1/size sums to exactly 1 per episode)."""
    grp = ["sec_type", "code", "side"]
    sub = base.loc[in_side, ["sec_type", "code", "_p"]].copy()
    sub["side"] = np.int8(side)
    sub = sub.sort_values([*grp, "_p"]).reset_index(drop=True)
    prev = sub.groupby(grp, sort=False)["_p"].shift(1)
    gap = sub["_p"] - prev
    # the EPISODE boundary (sizes/weighting): exits <= K stay in the
    # same episode — UNCHANGED by the pass semantics
    is_start = prev.isna() | (gap > RSI_REENTRY_EXIT_DAYS + 1)
    # the PASS flags, SIDE-MIRRORED (see the module docstring): on the
    # buy side the ISOLATED first touch is TRUE; on the sell side the
    # RE-TEST (K < gap <= ISOLATED — the double top) is TRUE, while
    # flickers AND isolated breakouts are the noisy FALSE population
    iso_cut = RSI_TYPICAL_ISOLATED_DAYS + 1
    is_reg = prev.isna() | (gap >= 2)
    if side == 0:  # top / sell — the re-test is the amplified event
        is_true = (gap > RSI_REENTRY_EXIT_DAYS + 1) & (gap <= iso_cut)
    else:          # bottom / buy — the isolated first touch is
        is_true = prev.isna() | (gap > iso_cut)
    # the episode = the band-day RUN (gaps within the re-entry window):
    # a new run starts at every first/start day; every band day of the
    # run — including the gap-1 continuations — shares its day count
    sub["_run"] = is_start.groupby(
        [sub["sec_type"], sub["code"]], sort=False,
    ).cumsum()
    sizes = sub.groupby([*grp, "_run"], sort=False).size() \
        .rename("_sz").reset_index()
    sub = sub.merge(sizes, on=[*grp, "_run"], how="left")
    out = (
        pd.Series(np.nan, index=sub.index, dtype="float64")
        .mask((is_reg & is_true).to_numpy(), 1.0)
        .mask((is_reg & ~is_true).to_numpy(), 0.0)
    )
    # the gap-1 continuation days inherit their pass's registration
    # flag (isolated TRUE / flicker or continuation FALSE): group-wise
    # ffill within the band-day sub frame — each episode's start day
    # carries its own pass flag, so the fill never crosses episodes
    # and every in-band day draws on its own pass's side
    out = out.groupby(
        [sub["sec_type"], sub["code"], sub["side"]], sort=False,
    ).ffill()
    sub.index = base.index[in_side.to_numpy()]
    return (
        out.set_axis(sub.index).reindex(base.index),
        sub["_sz"].set_axis(sub.index).reindex(base.index),
    )


# ---------------------------------------------------------------------------
#  Emit boundary (float64 flags -> bool/None rows for the bulk upsert)
# ---------------------------------------------------------------------------

def build_typical_rows(typical: pd.DataFrame) -> list[dict]:
    """Upsert rows: (code, sec_type, date + the 6 flag columns as
    python bool / None + the 6 episode-size columns as python int /
    None). Built via the REAL pandas ctor from host numpy object
    arrays — the sanctioned DB-literal boundary."""
    real_pd = getattr(pd, "_fsproxy_slow", pd)
    data: dict = {
        "sec_type": host_array(typical["sec_type"].to_numpy()),
        "code": host_array(typical["code"].to_numpy()),
        "date": host_array(typical["date"].to_numpy()),
    }
    for col in FLAG_COLUMNS:
        arr = host_array(
            typical[col].to_numpy(dtype="float64", na_value=np.nan)
        )
        obj = np.empty(arr.size, dtype=object)
        valid = np.isfinite(arr)
        obj[valid] = arr[valid] >= 0.5
        obj[~valid] = None
        data[col] = obj
    for col in SIZE_COLUMNS:
        arr = host_array(
            typical[col].to_numpy(dtype="float64", na_value=np.nan)
        )
        obj = np.empty(arr.size, dtype=object)
        valid = np.isfinite(arr) & (arr > 0.0)
        obj[valid] = arr[valid].astype("int64").tolist()
        obj[~valid] = None
        data[col] = obj
    frame = real_pd.DataFrame(
        data,
        columns=["sec_type", "code", "date", *FLAG_COLUMNS, *SIZE_COLUMNS],
    )
    return frame.to_dict("records")


# ---------------------------------------------------------------------------
#  Pipeline (called from the ranks rebuild — the flags need the ranks)
# ---------------------------------------------------------------------------

async def rebuild_rsi_typical(
    conn,
    typical: pd.DataFrame,
    *,
    sec_type: str | None = None,
) -> None:
    """Wholesale-upsert the is_typical_{W}days columns onto
    analysis.mov_ave_rsi for the run's scope.

    Args:
      conn: asyncpg connection (reused from the ranks rebuild).
      typical: compute_rsi_typical's output frame.
      sec_type: the run's sec_type scope (logging only — the rows
          carry their own sec_type).
    """
    if typical.empty:
        logger.info("    -> no source data; skipping RSI-typical write.")
        return

    t0 = time.time()
    rows = build_typical_rows(typical)
    logger.info(f"    -> upserting is_typical flags + episode sizes "
                f"({len(rows):,} rows)...")
    total = await bulk_upsert_async(
        conn, RSI_TABLE, rows,
        key_columns=["code", "sec_type", "date"],
    )
    logger.info(f"    -> upserted {total:,} typical-flag rows "
                f"({time.time() - t0:.1f}s)")


# ---------------------------------------------------------------------------
#  Post-write entry (run_rsi calls this AFTER run_table_step — the flags
#  ride the ranks table, which the ranks hook rebuilds first)
# ---------------------------------------------------------------------------

async def rebuild_rsi_typical_from_table(
    conn,
    *,
    sec_type: str,
    code_filter: str | None = None,
) -> None:
    """Recompute + upsert the is_typical_{W}days flags for the run's
    scope FROM analysis.mov_ave_rsi_ranks (just rebuilt by the ranks
    hook). Runs AFTER the RSI rows' own write so the flag upsert can
    never collide with it (the earlier in-hook ordering inserted
    flag-only rows into the freshly purged table and broke the COPY)."""
    t0 = time.time()
    st_code = SEC_TYPE_CODE[sec_type]
    where = "WHERE sec_type = $1"
    args: list = [st_code]
    if code_filter is not None:
        where += " AND code = $2"
        args.append(code_filter)
    rows = await conn.fetch(
        f"SELECT sec_type, code, date, "
        + ", ".join(f"rank_rsi_{w}days" for w in RSI_WINDOWS)
        + f" FROM analysis.mov_ave_rsi_ranks {where}",
        *args,
    )
    if not rows:
        logger.info("    -> no ranks rows; skipping RSI-typical write.")
        return
    real_pd = getattr(pd, "_fsproxy_slow", pd)
    ranks = real_pd.DataFrame(
        [dict(r) for r in rows],
        columns=["sec_type", "code", "date"]
        + [f"rank_rsi_{w}days" for w in RSI_WINDOWS],
    )
    # ranks arrive as int/None — float64 NaN for the compute frame
    for w in RSI_WINDOWS:
        col = f"rank_rsi_{w}days"
        ranks[col] = ranks[col].astype("float64")
    typical = compute_rsi_typical(ranks)
    await rebuild_rsi_typical(conn, typical, sec_type=sec_type)
    logger.info(f"    -> rsi_typical done in {time.time() - t0:.1f}s")
