"""Pure compute for builds.market_hypes — hype flags -> episode rows.

MIGRATED verbatim from analyze.mov_ave_spread.market_hypes (the
computation semantics are unchanged; only the module's home and its
import roots moved — the DB write / orchestration half lives in
runner.py).

The pipeline: per (sec_type, code, date) frame carrying ``price``-based
std_{W}days σ columns (fetch.py) and ``trading_amount``:

1. CENTERED PERCENTILE THRESHOLDS — trading_amount and each std_{W}days
   get a centered ±10y (2550 rows per side, 5101 total) rolling
   quantile threshold per (sec_type, code). A base with < 255
   observations has no thresholds -> the date is not hyped. The base
   looks BOTH ways (retrospective audit, look-ahead by design) — run a
   --force rebuild to refresh historical rows' flags as new data
   arrives.
2. CHECK-IN — a date checks in when trading_amount AND std_{W}days both
   EXCEED their thresholds (strict >; NULL -> not a check-in).
3. SATISFACTION — a date is hyped when MORE than
   HYPE_CHECKIN_SATISFACTION_THRESHOLD percent of the last W rows are
   check-ins (denominator = the full W rows; missing data counts
   against).
4. EPISODES — the hyped runs are collapsed per window into maximal
   consecutive cores, extended through the surrounding check-in
   evidence (first check-in of the W-row lookback before the core's
   start; last check-in of the W-row lookforward after its end),
   clipped to never overlap within one (sec_type, code), and bucketed
   by span into [W, HYPE_EPISODE_SPAN_MAX[W]).

GPU note: the centered percentile uses pandas ``groupby(...).rolling
(center=True).quantile()``. cuDF lacks rolling-quantile support, so
when cudf.pandas is active this op transparently falls back to the CPU
pandas implementation (same contract as the grouped-EWM helper in the
analyze RSI step). The episode assembly itself runs on host numpy
arrays (extracted once per window) — the run/extension math is
index-arithmetic-heavy and searchsorted-based, which cuDF does not
express; the extraction cost is one host copy of three boolean columns
per window, accepted for a much simpler and faster algorithm.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import host_array, safe_columns
from builds.market_hypes.config import (
    HYPE_CHECKIN_PERIODS,
    HYPE_CHECKIN_SATISFACTION_THRESHOLD,
    HYPE_EPISODE_SPAN_MAX,
    HYPE_STD_COLUMN_BY_PERIOD,
    HYPE_STD_THRESHOLD_PCT,
    HYPE_THRESHOLD_HALF_WINDOW_ROWS,
    HYPE_THRESHOLD_MIN_PERIODS,
    HYPE_THRESHOLD_WINDOW_ROWS,
    HYPE_TRADING_AMT_THRESHOLD_PCT,
)

import logging
logger = logging.getLogger(__name__)


# Transient (not persisted) per-window is_hyped column names on the
# source frame — collapsed into episode rows by hype_episodes.
def _is_hyped_col(checkin_period: int) -> str:
    """Transient wide column name holding is_hyped for one window."""
    return f"_is_hyped_{checkin_period}d"


# Transient per-window column names for the check-in flag and the two
# per-leg flags (0/1 float) — the episode assembly reads them to extend
# episodes through check-in evidence and to count per-leg days.
def _checkin_col(checkin_period: int) -> str:
    """Transient wide column name holding the joint check-in flag."""
    return f"_hype_checkin_{checkin_period}d"


def _std_ok_col(checkin_period: int) -> str:
    """Transient wide column name holding the volatility-leg flag."""
    return f"_hype_std_ok_{checkin_period}d"


# Transient column name for the shared liquidity-leg flag (the amt
# threshold is window-independent).
_AMT_OK_TMP = "_hype_amt_ok"


# ---------------------------------------------------------------------------
#  Compute helpers (pure pandas / cuDF)
# ---------------------------------------------------------------------------

def _grouped_rolling_quantile(
    df: pd.DataFrame, col: str, *, window: int, min_periods: int, q: float,
    center: bool = False,
) -> pd.Series:
    """(Optionally centered) rolling quantile per (sec_type, code) aligned
    to df.index.

    ``groupby(keys)[col].rolling(window, min_periods,
    center=center).quantile(q)`` returns a MultiIndex Series (group keys
    + original index). Strip the group-key levels and reindex to df.index
    to realign — the same contract as ``_grouped_ewm_pandas`` in the
    analyze RSI step.

    ``center=True`` slides the window symmetrically around each row
    (odd ``window`` = exactly (window-1)/2 rows on each side); the
    grouping still isolates windows within one (sec_type, code), so
    centered windows never span two codes. Near group edges the window
    is naturally truncated on the missing side (counts against
    min_periods).

    Stays on the pandas implementation: cuDF lacks rolling-quantile
    support, so under cudf.pandas this op falls back to CPU (accepted —
    see module docstring). NaN input values are skipped by the rolling
    window and count against min_periods.

    Shared helper: also imported by analyze.mov_ave_spread.high_low_pct
    and analyze.pe_and_dividends.pct_bands (the same centered-window
    percentile pattern).
    """
    s = pd.to_numeric(df[col], errors="coerce")
    keys = [df["sec_type"], df["code"]]
    res = (
        s.groupby(keys, sort=False)
        .rolling(window=window, min_periods=min_periods, center=center)
        .quantile(q)
    )
    res = res.reset_index(level=[0, 1], drop=True)
    return res.reindex(df.index)


def _grouped_rolling_sum(
    df: pd.DataFrame, col: str, *, window: int, min_periods: int,
) -> pd.Series:
    """Trailing rolling sum per (sec_type, code) aligned to df.index.

    Same alignment contract as ``_grouped_rolling_quantile``; used for
    the check-in count over the check-in window.
    """
    s = pd.to_numeric(df[col], errors="coerce")
    keys = [df["sec_type"], df["code"]]
    res = (
        s.groupby(keys, sort=False)
        .rolling(window=window, min_periods=min_periods)
        .sum()
    )
    res = res.reset_index(level=[0, 1], drop=True)
    return res.reindex(df.index)


def _gt_with_null_false(left: pd.Series, right: pd.Series) -> pd.Series:
    """Strict greater-than; NULL on either side yields False (not NULL).

    pandas comparisons with NaN already yield False, and cuDF yields
    nullable booleans — ``fillna(False)`` normalizes both to plain
    False so downstream rolling sums never see NULL check-ins.
    """
    return (left > right).fillna(False)


def compute_market_hypes(df: pd.DataFrame) -> pd.DataFrame:
    """Add the transient per-window hype columns used by the episode
    assembly.

    For each window W in HYPE_CHECKIN_PERIODS (sorted input required):
      1. std_threshold = centered-±10y rolling quantile (q = std pct) of
         std_{W}days per (sec_type, code) — base window of 2550 rows
         before + 2550 rows after each date (5101 rows, center=True).
      2. checkin = (trading_amount > amt_threshold)
                   & (std_{W}days > std_threshold)   [strict >, NULL->False]
      3. count = rolling sum of checkin over the last W rows
         (min_periods=W — NaN until the window is full).
      4. _is_hyped_{W}d = count / W * 100 > satisfaction pct
         (strict >; NaN count -> False).

    Transient columns attached (all 0/1 floats or bools):
      _hype_amt_ok           — liquidity-leg flag (shared, window-free)
      _hype_std_ok_{W}d      — volatility-leg flag per window
      _hype_checkin_{W}d     — joint check-in flag per window
      _is_hyped_{W}d         — satisfaction verdict per window

    The trading_amount threshold is computed ONCE and shared across all
    windows (the liquidity leg is window-independent); it uses the same
    centered ±10y base as the std thresholds.

    Requires the frame to be sorted by (sec_type, code, date) — the
    caller sorts before invoking.
    """
    if df.empty:
        for w in HYPE_CHECKIN_PERIODS:
            df[_is_hyped_col(w)] = pd.Series(dtype="bool")
            df[_checkin_col(w)] = pd.Series(dtype="float64")
            df[_std_ok_col(w)] = pd.Series(dtype="float64")
        df[_AMT_OK_TMP] = pd.Series(dtype="float64")
        return df

    # Defensive guard: no liquidity source -> no hype flags at all.
    # Host-pure membership (proxied Index.__contains__ falls back).
    if "trading_amount" not in set(safe_columns(df)):
        for w in HYPE_CHECKIN_PERIODS:
            df[_is_hyped_col(w)] = pd.Series(False, index=df.index)
            df[_checkin_col(w)] = pd.Series(0.0, index=df.index)
            df[_std_ok_col(w)] = pd.Series(0.0, index=df.index)
        df[_AMT_OK_TMP] = pd.Series(0.0, index=df.index)
        return df

    # ---- Liquidity leg: centered-±10y percentile threshold of daily
    # trading_amount (shared across all check-in windows).
    amt = pd.to_numeric(df["trading_amount"], errors="coerce")
    amt_threshold = _grouped_rolling_quantile(
        df, "trading_amount",
        window=HYPE_THRESHOLD_WINDOW_ROWS,
        min_periods=HYPE_THRESHOLD_MIN_PERIODS,
        q=HYPE_TRADING_AMT_THRESHOLD_PCT / 100.0,
        center=True,
    )
    amt_ok = _gt_with_null_false(amt, amt_threshold)
    df[_AMT_OK_TMP] = amt_ok.astype("float64")

    # ---- Per-window volatility leg + check-in count + satisfaction.
    for w in HYPE_CHECKIN_PERIODS:
        std_col = HYPE_STD_COLUMN_BY_PERIOD[w]
        if std_col not in set(safe_columns(df)):
            continue
        std = pd.to_numeric(df[std_col], errors="coerce")
        std_threshold = _grouped_rolling_quantile(
            df, std_col,
            window=HYPE_THRESHOLD_WINDOW_ROWS,
            min_periods=HYPE_THRESHOLD_MIN_PERIODS,
            q=HYPE_STD_THRESHOLD_PCT / 100.0,
            center=True,
        )
        std_ok = _gt_with_null_false(std, std_threshold)
        df[_std_ok_col(w)] = std_ok.astype("float64")

        checkin = (amt_ok & std_ok).astype("float64")
        # _grouped_rolling_sum reads a COLUMN of df — assign the
        # transient check-in flag to the frame first (so the groupby
        # keys align), then roll.
        df[_checkin_col(w)] = checkin
        checkin_count = _grouped_rolling_sum(
            df, _checkin_col(w), window=w, min_periods=w,
        )

        satisfaction_pct = checkin_count / w * 100.0
        df[_is_hyped_col(w)] = _gt_with_null_false(
            satisfaction_pct,
            pd.Series(
                HYPE_CHECKIN_SATISFACTION_THRESHOLD,
                index=df.index, dtype="float64",
            ),
        )

    return df


# ---------------------------------------------------------------------------
#  Episode assembly (per-date flags -> concat/extended/bucketed episodes)
# ---------------------------------------------------------------------------

def _episode_rows_for_window(
    df: pd.DataFrame,
    w: int,
    *,
    group_codes: np.ndarray,
    group_starts: np.ndarray,
    group_ends: np.ndarray,
    dates: np.ndarray,
) -> list[dict]:
    """Assemble one window's episodes from the transient flag columns.

    Implements the CONCAT + EXTENSION + BUCKETING pipeline (see the
    module docstring, step 4) for one check-in window ``w``:

      cores  — maximal runs of consecutive hyped rows per group;
      extend — each core's start slides back to the FIRST check-in in
               the w rows ending at the core's first hyped row (the
               lookback evidence that produced the core's first
               satisfaction verdict), and its end slides forward to the
               LAST check-in in the w rows starting at the core's last
               hyped row (the decaying tail). Interior non-check-in
               days are bridged — this is the "concat";
      clip   — no overlap with the previous episode of the same group
               and span < HYPE_EPISODE_SPAN_MAX[w];
      bucket — keep only spans >= w (the bucket minimum); cores whose
               own consecutive span already reaches the bucket max are
               dropped (the next bucket up owns sustained activity of
               that length).

    All heavy lifting is vectorized numpy (searchsorted over the
    global check-in positions for the extensions, prefix sums for the
    per-leg day counts); only the per-run clip loop is scalar Python
    (O(1) per run). Runs are processed in positional order so the
    no-overlap clip is a single left-to-right pass.

    Args:
      df: the sorted (sec_type, code, date) frame carrying the
          transient flag columns for ``w``.
      w: the check-in window / bucket minimum.
      group_codes: int array — group ordinal per row (0-based).
      group_starts / group_ends: per-group first/last row positions.
      dates: the date column as a host numpy datetime64 array.

    Returns:
      A list of episode row dicts with keys sec_type, code, start_date,
      end_date, min_checkin_period, hype_days, trading_amt_hype_days,
      std_hype_days.
    """
    hi = HYPE_EPISODE_SPAN_MAX[w]

    # Unwrap ONCE at the pandas→numpy boundary (B-A1 convention): the
    # whole episode detection below is raw host numpy — proxied arrays
    # from .to_numpy() would dispatch every downstream op through the
    # cudf fast/slow machinery.
    hyped = host_array(
        df[_is_hyped_col(w)].fillna(False).astype(bool).to_numpy()
    ).astype(bool)
    checkin = host_array(df[_checkin_col(w)].fillna(0.0).to_numpy()) > 0.0
    amt_ok = host_array(df[_AMT_OK_TMP].fillna(0.0).to_numpy()) > 0.0
    std_ok = host_array(df[_std_ok_col(w)].fillna(0.0).to_numpy()) > 0.0

    # ---- Runs of consecutive hyped rows (cores) ---------------------
    # Positions of hyped rows; a new run starts wherever consecutive
    # hyped positions are not row-adjacent or belong to another group.
    hyp_idx = np.flatnonzero(hyped)
    if hyp_idx.size == 0:
        return []

    if hyp_idx.size == 1:
        run_starts = hyp_idx
        run_ends = hyp_idx
    else:
        step = hyp_idx[1:] - hyp_idx[:-1]
        new_run = (step > 1) | (
            group_codes[hyp_idx[1:]] != group_codes[hyp_idx[:-1]]
        )
        run_starts = np.concatenate(([hyp_idx[0]], hyp_idx[1:][new_run]))
        run_ends = np.concatenate((hyp_idx[:-1][new_run], [hyp_idx[-1]]))
    run_group = group_codes[run_starts]

    # ---- Extension targets (vectorized searchsorted) ----------------
    # Global positions of check-in rows — the extension evidence.
    chk_pos = np.flatnonzero(checkin)
    if chk_pos.size == 0:
        # No check-ins at all -> no satisfaction could ever have fired
        # -> unreachable with consistent flags; kept as a guard.
        return []

    # Backward: first check-in >= max(group_start, s - w + 1), and it
    # must lie <= s; otherwise the core start stands.
    lb = np.maximum(group_starts[run_group], run_starts - w + 1)
    j = np.searchsorted(chk_pos, lb, side="left")
    # j may equal chk_pos.size for runs past the last check-in — the
    # clipped gather below guards that with a sentinel.
    safe_j = np.minimum(j, chk_pos.size - 1) if chk_pos.size else j
    cand = chk_pos[safe_j] if chk_pos.size else np.full_like(run_starts, -1)
    valid = (j < chk_pos.size) & (cand <= run_starts)
    ext_s = np.where(valid, cand, run_starts)

    # Forward: last check-in <= min(group_end, e + w - 1), and it must
    # lie >= e; otherwise the core end stands.
    ub = np.minimum(group_ends[run_group], run_ends + w - 1)
    j2 = np.searchsorted(chk_pos, ub, side="right") - 1
    valid2 = (j2 >= 0) & (chk_pos[np.maximum(j2, 0)] >= run_ends)
    cand2 = chk_pos[np.maximum(j2, 0)]
    ext_e = np.where(valid2, cand2, run_ends)

    # ---- Per-run clip pass (no overlap, span cap, bucket filter) ----
    # Prefix sums for the per-leg day counts (O(1) per episode).
    amt_cs = np.concatenate(([0], np.cumsum(amt_ok, dtype=np.int64)))
    std_cs = np.concatenate(([0], np.cumsum(std_ok, dtype=np.int64)))

    # sec_type / code values per group for the output rows (host arrays).
    sec_vals = host_array(df["sec_type"].to_numpy())
    code_vals = host_array(df["code"].to_numpy())

    rows: list[dict] = []
    prev_end_by_group: dict[int, int] = {}
    for k in range(run_starts.size):
        g = int(run_group[k])
        s = int(run_starts[k])
        e = int(run_ends[k])
        prev_end = prev_end_by_group.get(g, -1)

        if e - s + 1 >= hi:
            # Core alone reaches the bucket max: sustained activity of
            # the next bucket's length — dropped here (the next bucket
            # up flags it with its own, smoother satisfaction). Its
            # core still blocks backward extension of the next run.
            prev_end_by_group[g] = e
            continue

        xs = int(ext_s[k])
        xe = int(ext_e[k])
        # No overlap with the previous episode of this group.
        if xs <= prev_end:
            xs = prev_end + 1
        # Span cap: clip the forward extension first (the start carries
        # the turmoil's onset; xe >= e always holds because the core
        # span < hi, so the core itself is never truncated).
        if xe - xs + 1 > hi - 1:
            xe = xs + hi - 2

        span = xe - xs + 1
        if span >= w:
            rows.append({
                "sec_type": sec_vals[xs],
                "code": code_vals[xs],
                # np.datetime64 straight from the host numpy array — the
                # frame is built column-wise with datetime64[ns] date
                # columns (object-date proxy columns poison every
                # downstream op with MixedTypeError fallbacks);
                # sanitize_for_db_insert's M-branch converts per chunk.
                "start_date": dates[xs],
                "end_date": dates[xe],
                "min_checkin_period": w,
                "hype_days": span,
                "trading_amt_hype_days": int(
                    amt_cs[xe + 1] - amt_cs[xs]
                ),
                "std_hype_days": int(std_cs[xe + 1] - std_cs[xs]),
            })
        # The extended span is claimed either way — later runs of this
        # group cannot extend back into it.
        prev_end_by_group[g] = xe

    return rows


def hype_episodes(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse the per-window hyped runs into CONCATENATED episodes.

    For each window W in HYPE_CHECKIN_PERIODS the transient
    ``_is_hyped_{W}d`` / ``_hype_checkin_{W}d`` / per-leg flag columns
    (on a (sec_type, code, date)-sorted frame) are assembled, per
    (sec_type, code), into episodes:

      start_date / end_date — the extended span boundaries (see
          _episode_rows_for_window: the first check-in of the W-row
          lookback evidence before the core, through the last check-in
          of the W-row lookforward after it),
      hype_days             — the span length in trading dates,
          bucket-filtered to [W, HYPE_EPISODE_SPAN_MAX[W]),
      trading_amt_hype_days / std_hype_days — days within the span on
          which each leg individually checked in.

    Returns a DataFrame with the MARKET_HYPES_COLUMNS episode fields —
    one row per episode per window; empty (with those columns) when no
    date is hyped.
    """
    out_cols = [
        "sec_type", "code", "start_date", "end_date",
        "min_checkin_period", "hype_days",
        "trading_amt_hype_days", "std_hype_days",
    ]
    if df.empty:
        return pd.DataFrame(columns=out_cols)

    n = len(df)
    # Group ordinals + boundaries for the (sec_type, code) groups of
    # the sorted frame (rows of one group are contiguous).
    new_group = (
        df["sec_type"].ne(df["sec_type"].shift())
        | df["code"].ne(df["code"].shift())
    )
    # Row 0's shift() leaves an NA -> nullable bool; ``to_numpy(dtype=bool)``
    # fast path requires no nulls (else cudf fallback), so fill first.
    new_group_host = host_array(new_group.fillna(True).to_numpy(dtype=bool))
    group_codes = np.cumsum(new_group_host) - 1
    group_starts = np.flatnonzero(new_group_host)
    group_ends = np.concatenate((group_starts[1:], [n])) - 1
    dates = host_array(df["date"].to_numpy())

    all_rows: list[dict] = []
    for w in HYPE_CHECKIN_PERIODS:
        if _is_hyped_col(w) not in set(safe_columns(df)):
            continue
        all_rows.extend(
            _episode_rows_for_window(
                df, w,
                group_codes=group_codes,
                group_starts=group_starts,
                group_ends=group_ends,
                dates=dates,
            )
        )

    if not all_rows:
        return pd.DataFrame(columns=out_cols)
    # Column-wise ctor with EXPLICIT dtypes (a dict-row ctor infers object
    # dtype for the date values -> object-date proxy columns trigger
    # MixedTypeError fallbacks in every getitem/setitem/astype/reindex).
    # Dates stay GPU-native datetime64[ns]; sanitize_for_db_insert's
    # M-branch converts them to asyncpg-native values per COPY chunk.
    n_rows = len(all_rows)
    out = pd.DataFrame({
        "sec_type": [r["sec_type"] for r in all_rows],
        "code": [r["code"] for r in all_rows],
        "start_date": np.array(
            [r["start_date"] for r in all_rows], dtype="datetime64[ns]",
        ),
        "end_date": np.array(
            [r["end_date"] for r in all_rows], dtype="datetime64[ns]",
        ),
        "min_checkin_period": np.fromiter(
            (r["min_checkin_period"] for r in all_rows),
            dtype=np.int64, count=n_rows,
        ),
        "hype_days": np.fromiter(
            (r["hype_days"] for r in all_rows), dtype=np.int64, count=n_rows,
        ),
        "trading_amt_hype_days": np.fromiter(
            (r["trading_amt_hype_days"] for r in all_rows),
            dtype=np.int64, count=n_rows,
        ),
        "std_hype_days": np.fromiter(
            (r["std_hype_days"] for r in all_rows),
            dtype=np.int64, count=n_rows,
        ),
    })
    return out
