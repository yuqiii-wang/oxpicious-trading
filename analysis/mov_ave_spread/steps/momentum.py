"""Internal momentum step for analysis.mov_ave_spread.

Lookback-adaptive momentum (rate of change) for ETF + Index + Stock —
one row per (sec_type, code, date) in analysis.mov_ave_momentum.

=======================================================================
  FINANCIAL SEMANTICS
=======================================================================

momentum[n][t] = price[t] / price[t-n] - 1        (signed fractional ROC)

over the parent mov_ave_spread price column (ETF = COALESCE(
etf_adjustment.adj_close, etf_basic_stats.close); index =
index_basic_stats.close; stock = stock_basic_stats.close — reused from
the parent's already-fetched source DataFrame).

TWO FIXED lookbacks — the stable anchors:
    momentum_5days   n = 5    (one trading week)
    momentum_20days  n = 20   (one trading month)

THREE ADAPTIVE lookbacks (short / mid / long) whose n is chosen
SELF-ADAPTIVELY BY VOLATILITY per (code, regime). Each day carries the
regime stats.market_regimes classified it into (calm / hot / panic /
quiet), and the (code, regime) slice's volatility level sets the
lookbacks via EQUAL-EXPECTED-MOVE targeting (the volatility-time
formulation):

    sigma(code, regime) = median of stats.market_regimes.vol
                          (sqrt(EWMA(ret_1d^2, span=20)) — the SAME vol
                          definition the regime classification itself
                          z-scores) over the code's full-history days in
                          that regime
    n_term = clip( round( (target_move_term / sigma)^2 ),
                   min_term, max_term )

n_term is the number of trading days after which one standard deviation
of cumulative return equals the term's target move — high-volatility
codes/regimes get SHORTER lookbacks (prices go "stale" faster), calm
ones longer. The 5/20-day anchors double as the short/mid FLOORS; after
clipping, n_short <= n_mid <= n_long is enforced (cumulative max).

Calibration (retune here + the matching SQL COMMENTs together):
    term    target_move   clip bounds     typical calm A-share index
    short   3%            [5, 60]         sigma~1.2%/d -> ~6d
    mid     6%            [20, 120]                         ~25d
    long    12%           [60, 255]                        ~100d

Fallback chain when a (code, regime) slice is too thin to trust its own
median (< MIN_REGIME_VOL_DAYS non-null vol days): the code's overall
median vol; when even that is unavailable: DEFAULT_DAILY_VOL. A sigma
floor (VOL_FLOOR) guards flat series against division by zero.

=======================================================================
  REBUILD SEMANTICS (lookbacks SHIFT — wholesale per sec_type)
=======================================================================

sigma(code, regime) aggregates the code's FULL history in the regime, so
a past day's stored lookbacks — and hence its adaptive momentum values —
can shift retroactively when new days arrive (the rsi_ranks precedent;
the classification input itself is trailing/shift-1, but the per-slice
median is full-history). The table is therefore REBUILT WHOLESALE per
sec_type on every run that computes it: incremental runs detect missing
dates vs the source identity tables, then DELETE the sec_type scope and
CSV-COPY the full recompute; --force runs reuse the parent's upfront
purge; single-code runs rebuild the pre-deleted code.

=======================================================================
  GPU LAYOUT (cudf.pandas)
=======================================================================

Whole-frame GPU passes: the frame sort, the (code, date) regime LEFT
merge (hash join), the per-(code, regime) median/count groupby, the
lookback arithmetic (round / np.maximum / np.minimum Series forms), and
the two FIXED-window momentum columns (grouped_shift — GPU-native).
cuDF has no per-row-variable shift, and the adaptive lookback changes
WITH THE DAY's regime (regime days are non-contiguous, so a grouped
shift by (code, regime) would skip non-regime days and be WRONG) — the
adaptive momentum is therefore ONE sanctioned host-numpy boundary: a
per-row positional gather (price[t - n_t] via fancy indexing over the
sorted frame, guarded by per-row group starts), unwrapped once via
host_array at entry and materialized straight into the REAL-pandas
emit frame (never assigned back into the GPU frame — no mid-flow
hybrids). The emit/COPY boundary follows build_ranks_copy_frame.
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from _common.build_commons import (
    find_missing_analysis_dates,
    rec_cols,
)
from _common.db_commons import chunked_purge_async, csv_copy_frame_chunked_async
from _common.df_utils import (
    column_subset,
    epoch_col_to_dt64,
    grouped_shift,
    host_array,
    host_unique,
)
from _common.labels import SEC_TYPE_LABEL
from analysis._common import upsert_analysis_identity
from analysis.mov_ave_spread._common import null_if_overflow_counted
from analysis.mov_ave_spread._common.steps import (
    infer_sec_types,
    sec_type_code,
)
from analysis.mov_ave_spread.config import SEC_TYPE_IDENTITY_TABLE

import logging
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

MOMENTUM_TABLE = "analysis.mov_ave_momentum"
MOMENTUM_ANALYSIS_NAME = "mov_ave_momentum"

MOMENTUM_DESCRIPTION = (
    "Lookback-adaptive momentum / rate of change (ETF + Index + Stock). "
    "For each security and business date, momentum[n] = price[t]/"
    "price[t-n] - 1 over the parent mov_ave_spread price column, for two "
    "fixed anchor lookbacks (momentum_5days, momentum_20days) and three "
    "ADAPTIVE lookbacks (momentum_short / momentum_mid / momentum_long) "
    "whose n is chosen self-adaptively by volatility per (code, regime): "
    "n_term = clip(round((target_move_term / sigma(code, regime))^2), "
    "min, max) with sigma = the median stats.market_regimes.vol (sqrt "
    "EWMA(ret_1d^2, span=20)) over the code's full-history days in that "
    "regime — equal-expected-move targeting, so high-volatility regimes "
    "get SHORTER lookbacks. short: target 3% clip [5,60]; mid: 6% "
    "[20,120]; long: 12% [60,255]; ordering enforced; thin slices fall "
    "back to the code-level median vol, then a 2%/day default. lb_short "
    "/ lb_mid / lb_long record the day's effective lookbacks, vol_regime "
    "the sigma they were derived from, regime the day's market_regimes "
    "label code. Because sigma is full-history per slice, past lookbacks "
    "shift with new data — the table is rebuilt WHOLESALE per sec_type "
    "on every run that computes it (the rsi_ranks precedent)."
)

# Fixed-anchor lookback windows (trading days).
FIXED_WINDOWS = (5, 20)

# Adaptive terms in canonical (short -> long) order.
TERMS = ("short", "mid", "long")

# Equal-expected-move targets + clip bounds per term. The 5/20-day fixed
# anchors double as the short/mid floors (see the module docstring's
# calibration table). Keep in sync with the CHECK constraint and COMMENTs
# in database/sql/analysis/mov_ave_spreads/11_mov_ave_momentum.sql.
TERM_TARGET_MOVE = {"short": 0.03, "mid": 0.06, "long": 0.12}
TERM_LB_MIN = {"short": 5, "mid": 20, "long": 60}
TERM_LB_MAX = {"short": 60, "mid": 120, "long": 255}

# A (code, regime) slice needs at least this many non-null vol days to
# trust its own median; thinner slices fall back to the code-level median.
MIN_REGIME_VOL_DAYS = 20

# Terminal fallbacks: vol when the code has no vol rows at all, and the
# sigma floor guarding flat series against division by zero.
DEFAULT_DAILY_VOL = 0.02
VOL_FLOOR = 1e-4

# NUMERIC(10,6) overflow guard (|value| must be < 10^4 after 6dp rounding).
NUMERIC_MAX_ABS = 10000.0

MOMENTUM_COLUMNS = [
    "sec_type", "code", "date", "regime", "vol_regime",
    "lb_short", "lb_mid", "lb_long",
    "momentum_5days", "momentum_20days",
    "momentum_short", "momentum_mid", "momentum_long",
]

_REGIME_COLUMNS = ["code", "date", "regime", "vol"]


# ---------------------------------------------------------------------------
#  Regime registry fetch
# ---------------------------------------------------------------------------

async def _fetch_market_regimes(conn, sec_type: str) -> pd.DataFrame:
    """Fetch the sec_type's DAILY market-regime states — one row per
    (code, date) from stats.market_regimes, with the `vol` evidence
    column (sqrt(EWMA(ret_1d^2, span=20))) the adaptive lookbacks derive
    from. date as datetime64[us] (the merge alignment convention)."""
    rows = await conn.fetch(
        """
        SELECT m.code,
               extract(epoch from m.date)::float8 AS date,
               m.regime::int AS regime,
               m.vol::float8 AS vol
        FROM stats.market_regimes m
        WHERE m.sec_type = $1
        ORDER BY m.code, m.date ASC
        """,
        sec_type,
    )
    if not rows:
        return pd.DataFrame(columns=_REGIME_COLUMNS)
    df = pd.DataFrame(rec_cols(rows), columns=_REGIME_COLUMNS)
    df["date"] = epoch_col_to_dt64(df["date"], index=df.index)
    return df


# ---------------------------------------------------------------------------
#  Adaptive-lookback derivation (whole-frame grouped passes on the proxy)
# ---------------------------------------------------------------------------

def _attach_regimes(frame: pd.DataFrame, regimes: pd.DataFrame,
                    ) -> pd.DataFrame:
    """LEFT-merge the regime labels + vol evidence onto the day frame by
    (code, date) and normalize the label column.

    Days with no registry row (build lag) default to the calm code —
    the registry's own no-state-day convention — with NULL vol. The
    regime column rides as its int16 label code end-to-end (the
    label-code contract; never a string inside a cudf frame)."""
    frame = frame.merge(
        regimes[["code", "date", "regime", "vol"]],
        on=["code", "date"], how="left",
    )
    frame["regime"] = frame["regime"].fillna(np.int16(0)).astype("int16")
    return frame


def derive_adaptive_lookbacks(frame: pd.DataFrame) -> pd.DataFrame:
    """Derive the per-(sec_type, code, regime) vol_regime + lb_{short,
    mid, long} stats and merge them onto the day frame (one whole-frame
    grouped pass + one small hash join).

    The day frame must already carry sec_type / code / regime / vol (see
    _attach_regimes). Adds vol_regime (float64, NaN where the slice fell
    back) and the three lb_* float64 columns to a NEW frame; the
    int/NULL split happens at the emit boundary only (float64 carries
    through compute per the dtype rules).
    """
    # vol-day count as float64 (bool x numeric is banned inside cudf
    # accumulators — cast notna() before weighting).
    frame = frame.assign(_vol_n=frame["vol"].notna().astype("float64"))

    # Per-(code, regime) median vol + vol-day count — ONE grouped pass.
    regime_stats = frame.groupby(
        ["sec_type", "code", "regime"], sort=False, as_index=False,
    ).agg(vol_median=("vol", "median"), vol_days=("_vol_n", "sum"))

    # Code-level median vol — the thin-slice fallback.
    code_stats = frame.groupby(
        ["sec_type", "code"], sort=False, as_index=False,
    ).agg(vol_code=("vol", "median"))

    stats = regime_stats.merge(code_stats, on=["sec_type", "code"],
                               how="left")

    # Effective sigma per (code, regime): the slice median when the slice
    # is thick enough, else the code median, else the terminal default —
    # floored to VOL_FLOOR (flat series must not divide by zero).
    use_slice = (stats["vol_days"] >= MIN_REGIME_VOL_DAYS) & \
        stats["vol_median"].notna()
    vol_eff = stats["vol_median"].where(use_slice, stats["vol_code"])
    vol_eff = vol_eff.fillna(DEFAULT_DAILY_VOL)
    vol_eff = np.maximum(vol_eff, VOL_FLOOR)

    # vol_regime keeps the SLICE median only where it was actually used
    # (audit evidence; NULL on fallback — matches the SQL COMMENT).
    stats["vol_regime"] = stats["vol_median"].where(use_slice)

    # Equal-expected-move targeting: n = (target_move / sigma)^2 days,
    # rounded, clipped to the term bounds, then ordered cumulatively.
    for term in TERMS:
        raw = (TERM_TARGET_MOVE[term] / vol_eff) ** 2
        n = raw.round()
        n = np.maximum(n, float(TERM_LB_MIN[term]))
        n = np.minimum(n, float(TERM_LB_MAX[term]))
        if term == "mid":
            n = np.maximum(n, stats["lb_short"])
        elif term == "long":
            n = np.maximum(n, stats["lb_mid"])
        stats[f"lb_{term}"] = n

    n_slices = len(stats)
    n_fallback = int((~use_slice).sum())
    logger.info(
        f"    -> derived lookbacks for {n_slices:,} (code, regime) "
        f"slices ({n_fallback:,} on the code/default fallback)"
    )
    _log_lookback_summary(stats)

    # Merge back onto the day frame by (sec_type, code, regime) — a
    # small hash join; every day's key exists by construction.
    out = frame.merge(
        stats[["sec_type", "code", "regime", "vol_regime",
               "lb_short", "lb_mid", "lb_long"]],
        on=["sec_type", "code", "regime"], how="left",
    )
    return out.drop(columns=["_vol_n"])


def _log_lookback_summary(stats: pd.DataFrame) -> None:
    """One diagnostic line per term: the distribution of derived
    lookbacks across (code, regime) slices, decoded via the label maps
    (labels live only in log lines — never inside the frame)."""
    from _common.labels import REGIME_LABEL

    for term in TERMS:
        col = stats[f"lb_{term}"]
        lo = col.min()
        hi = col.max()
        med = col.median()
        logger.info(
            f"       lb_{term}: median {med:.0f}d, range "
            f"[{lo:.0f}, {hi:.0f}]d "
            f"(target move {TERM_TARGET_MOVE[term]:.0%}, clip "
            f"[{TERM_LB_MIN[term]}, {TERM_LB_MAX[term]}])"
        )
    # Per-regime medians — a tiny frame (<= 4 rows): ONE host unwrap of
    # index + values, then a host-side loop (cudf does not support frame
    # iteration; per-row .iloc would pay the transfer each line).
    by_regime = stats.groupby("regime", sort=True)[
        ["lb_short", "lb_mid", "lb_long"]].median()
    regimes_host = host_array(by_regime.index.to_numpy()).tolist()
    lbs_host = host_array(
        by_regime.to_numpy(dtype="float64", na_value=np.nan)
    )
    for r, row in zip(regimes_host, lbs_host):
        logger.info(
            f"       regime {REGIME_LABEL.get(int(r), r)}: median lbs "
            f"short/mid/long = {row[0]:.0f}/{row[1]:.0f}/"
            f"{row[2]:.0f}d"
        )


# ---------------------------------------------------------------------------
#  Momentum compute (fixed windows on GPU; adaptive via ONE host gather)
# ---------------------------------------------------------------------------

def compute_fixed_momentum(frame: pd.DataFrame) -> pd.DataFrame:
    """Add momentum_{5,20}days — the fixed-anchor ROC — via the shared
    grouped_shift helper (GPU whole-frame pass).

    The frame must be sorted by (sec_type, code, date) — the caller
    guarantees it; sort=False keeps that order. NaN until the code has
    n prior rows; base<=0 legs null out (defensive; closes are > 0)."""
    grp = ["sec_type", "code"]
    for w in FIXED_WINDOWS:
        grouped_shift(
            frame, grp, "price", out_names=f"_base{w}",
            periods=w, sort=False,
        )
        base = frame[f"_base{w}"]
        frame[f"momentum_{w}days"] = (
            frame["price"] / base - 1.0
        ).where(base > 0)
        frame = frame.drop(columns=[f"_base{w}"])
    return frame


def compute_adaptive_momentum_host(
    frame: pd.DataFrame,
) -> dict[str, np.ndarray]:
    """The three adaptive momentum columns as HOST numpy arrays — ONE
    sanctioned per-row positional-gather boundary (see the module
    docstring's GPU layout section: cuDF has no per-row-variable shift
    and regime days are non-contiguous, so a grouped shift by
    (code, regime) would be semantically wrong).

    The frame must be sorted by (sec_type, code, date) with a default
    RangeIndex. Unwraps price + group starts + the three lb columns once
    via host_array, gathers price[t - lb_t] per row (guarded by the
    row's group start), and returns {"short" | "mid" | "long": float64
    array} positionally aligned to the frame — consumed by the emit
    builder without ever re-entering the GPU frame."""
    n = len(frame)
    pos = np.arange(n, dtype=np.int64)
    sec = host_array(frame["sec_type"].to_numpy())
    code = host_array(frame["code"].to_numpy())
    change = np.empty(n, dtype=np.bool_)
    change[0] = True
    if n > 1:
        np.logical_or(sec[1:] != sec[:-1], code[1:] != code[:-1],
                      out=change[1:])
    starts = np.flatnonzero(change)
    start_per_row = np.repeat(starts, np.diff(np.append(starts, n)))

    price = host_array(
        frame["price"].to_numpy(dtype="float64", na_value=np.nan)
    )

    out: dict[str, np.ndarray] = {}
    for term in TERMS:
        lb = host_array(frame[f"lb_{term}"].to_numpy()).astype("int64")
        target = pos - lb
        in_group = target >= start_per_row
        safe = np.where(in_group, target, 0)
        base = price[safe]
        ok = in_group & np.isfinite(price) & np.isfinite(base) \
            & (base > 0.0)
        guarded = np.where(base > 0.0, base, 1.0)
        mom = np.where(ok, price / guarded - 1.0, np.nan)
        # NUMERIC(10,6) overflow guard (host-side — these arrays go
        # straight to the emit frame).
        mom = np.where(np.isfinite(mom) & (np.abs(mom) < NUMERIC_MAX_ABS),
                       mom, np.nan)
        out[term] = mom
    return out


# ---------------------------------------------------------------------------
#  Emit boundary (CSV-COPY frame — REAL pandas from host arrays)
# ---------------------------------------------------------------------------

def build_momentum_copy_frame(
    frame: pd.DataFrame, adaptive_host: dict[str, np.ndarray],
) -> pd.DataFrame:
    """Emit-boundary frame: the analysis.mov_ave_momentum columns, built
    via the REAL pandas ctor from host numpy arrays (the csv_copy
    renderer convention — the frame never round-trips through the GPU
    proxy; NaN/NaT render as empty CSV fields, i.e. NULL).

    lb_* / regime ride as plain int arrays (NOT NULL columns — no
    object/None juggling needed, unlike the ranks' nullable INTEGERs);
    momentum floats carry NaN for NULL."""
    real_pd = getattr(pd, "_fsproxy_slow", pd)
    data: dict = {
        "sec_type": host_array(frame["sec_type"].to_numpy()),
        "code": host_array(frame["code"].to_numpy()),
        "date": host_array(frame["date"].to_numpy()),
        "regime": host_array(frame["regime"].to_numpy()),
        "vol_regime": host_array(
            frame["vol_regime"].to_numpy(dtype="float64", na_value=np.nan)
        ),
        "lb_short": host_array(
            frame["lb_short"].to_numpy()).astype("int64"),
        "lb_mid": host_array(frame["lb_mid"].to_numpy()).astype("int64"),
        "lb_long": host_array(
            frame["lb_long"].to_numpy()).astype("int64"),
        "momentum_5days": host_array(
            frame["momentum_5days"].to_numpy(
                dtype="float64", na_value=np.nan)
        ),
        "momentum_20days": host_array(
            frame["momentum_20days"].to_numpy(
                dtype="float64", na_value=np.nan)
        ),
        "momentum_short": adaptive_host["short"],
        "momentum_mid": adaptive_host["mid"],
        "momentum_long": adaptive_host["long"],
    }
    return real_pd.DataFrame(data, columns=MOMENTUM_COLUMNS)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_momentum(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the momentum pipeline against the source price data already
    loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the ``price``
    column — no second source fetch; only the regime registry is fetched
    fresh, one scoped query). Wholesale rebuild per sec_type on every
    run that computes (see the module docstring's rebuild semantics).

    Pipeline
      0. Determine whether there is anything to do: single-code mode
         computes all dates (rows pre-deleted by the caller); force mode
         reuses the parent's upfront purge; incremental mode diffs
         missing dates vs the identity tables — no missing dates skips,
         any missing date triggers the wholesale rebuild (purge + full
         recompute).
      1. Attach regimes + derive adaptive lookbacks (whole-frame GPU
         passes), compute fixed + adaptive momentum.
      2. Purge the scope (conflict-free COPY), CSV-COPY insert.
      3. Upsert analysis.analysis_identity.
    """
    t0 = time.time()
    logger.info("\n" + "=" * 78)
    logger.info("  MOV_AVE_MOMENTUM (internal step of mov_ave_spread)")
    logger.info("=" * 78)

    needed_cols = column_subset(df, ["sec_type", "code", "date", "price"])
    frame = df[needed_cols].copy()
    if frame.empty:
        logger.info("    -> no source data; skipping momentum step.")
        return

    sec_types = infer_sec_types(frame, sec_type)

    # ---- Phase 0: is there anything to do? ------------------------------
    if code_filter is not None:
        logger.info("    mode: SINGLE-CODE (wholesale rebuild for "
              f"{code_filter}; rows pre-deleted)")
        needs_compute = True
    elif force:
        logger.info("    mode: FORCE (wholesale rebuild; the parent "
              "purged the scope upfront)")
        needs_compute = True
    else:
        missing_union: set = set()
        for st in sec_types:
            td_st = await find_missing_analysis_dates(
                conn, MOMENTUM_TABLE,
                [SEC_TYPE_IDENTITY_TABLE[st]], sec_type=sec_type_code(st),
            )
            missing_union |= td_st
            logger.info(f"    -> {st}: momentum {len(td_st)} missing dates")
        needs_compute = bool(missing_union)
        if not needs_compute:
            logger.info("    -> DB is up to date; nothing to do.")
            return

    # ---- Phase 1: regimes -> lookbacks -> momentum ----------------------
    logger.info("\n[m1] Attaching regimes + deriving adaptive lookbacks "
          "(per (code, regime) median vol, equal-expected-move targets)...")
    regimes = await _fetch_market_regimes(conn, sec_types[0])
    logger.info(f"    -> fetched {len(regimes):,} regime registry rows")
    frame = frame.sort_values(
        ["sec_type", "code", "date"]).reset_index(drop=True)
    frame = _attach_regimes(frame, regimes)
    frame = derive_adaptive_lookbacks(frame)
    # Re-establish the canonical order after the merges (left joins
    # preserve it, but the gather boundary's positional contract is
    # load-bearing — make it explicit).
    frame = frame.sort_values(
        ["sec_type", "code", "date"]).reset_index(drop=True)

    logger.info("\n[m2] Computing momentum (fixed 5/20d anchors on the "
          "grouped-shift GPU path; adaptive short/mid/long via the "
          "positional-gather host boundary)...")
    frame = compute_fixed_momentum(frame)
    adaptive_host = compute_adaptive_momentum_host(frame)

    # Overflow guard on the frame-side fixed columns (the adaptive host
    # arrays were guarded inside the boundary).
    nulled = {}
    for w in FIXED_WINDOWS:
        c = f"momentum_{w}days"
        clean, n_n = null_if_overflow_counted(frame[c])
        frame[c] = clean
        if n_n > 0:
            nulled[c] = n_n
    if nulled:
        total = sum(nulled.values())
        per = ", ".join(f"{c}={n}" for c, n in nulled.items())
        logger.info(f"    -> NUMERIC(10,6) overflow-guard nulled "
              f"{total:,} value(s): {per}")

    emit = build_momentum_copy_frame(frame, adaptive_host)

    # ---- Phase 2: purge scope + CSV-COPY insert -------------------------
    if code_filter is not None:
        status = await conn.execute(
            f"DELETE FROM {MOMENTUM_TABLE} "
            f"WHERE sec_type = $1 AND code = $2",
            sec_type_code(sec_types[0]), code_filter,
        )
        n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
        logger.info(f"    -> single-code mode: deleted {n_del:,} momentum "
              f"rows for ({sec_types[0]}, {code_filter})")
    elif not force:
        # Incremental run with missing dates: the wholesale rebuild needs
        # the purge (force runs reuse the parent's upfront purge).
        for st in sec_types:
            n_del = await chunked_purge_async(
                conn, MOMENTUM_TABLE,
                where_sql="sec_type = $1", params=(sec_type_code(st),),
            )
            logger.info(f"    -> deleted {n_del:,} {st} momentum rows "
                  f"(wholesale rebuild)")
    total = await csv_copy_frame_chunked_async(
        conn, MOMENTUM_TABLE, emit,
        partition_key="code",
        label="momentum",
    )
    logger.info(f"    -> inserted {total:,} momentum rows "
          f"({time.time() - t0:.1f}s)")
    del frame, emit

    # ---- Phase 3: register in analysis_identity -------------------------
    logger.info("\n[m3] Upserting analysis.analysis_identity registry...")
    await upsert_analysis_identity(
        conn,
        name=MOMENTUM_ANALYSIS_NAME,
        detail_name="mov_ave_momentum",
        description=MOMENTUM_DESCRIPTION,
    )
