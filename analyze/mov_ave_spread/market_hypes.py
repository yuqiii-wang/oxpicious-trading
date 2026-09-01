"""Internal market-hypes step for analyze.mov_ave_spread.

Market-hype EPISODE detection for ETF + Index + Stock: one row per
(sec_type, code, min_checkin_period, episode) in
analysis.mov_ave_market_hypes — an episode being a CONCATENATED span of
trading dates around a maximal run of "hyped" dates, extended through
the surrounding check-in evidence and bucketed BY ITS LENGTH.

=======================================================================
  FINANCIAL SEMANTICS
=======================================================================

A market is "hyped" on date t when trading amount AND price volatility
are BOTH elevated — SUSTAINEDLY — over a check-in window, each measured
against its own CENTERED 20-year percentile threshold:

1. CENTERED PERCENTILE THRESHOLDS (per date, per code — the audit base
   window spans BOTH directions around the audited date, NOT a
   trailing/rolling-back window):
     trading_amt_threshold[t] = the HYPE_TRADING_AMT_THRESHOLD_PCT-th
         percentile (linear interpolation) of daily trading_amount over
         the centered base window of HYPE_THRESHOLD_HALF_WINDOW_ROWS
         (2550 = 10 trading years) rows BEFORE t, t itself, and 2550
         rows AFTER t (HYPE_THRESHOLD_WINDOW_ROWS = 5101 rows ≈ 20
         trading years total).
     std_threshold[t] = the HYPE_STD_THRESHOLD_PCT-th percentile of
         std_{W}days over the same centered window, where W = the
         check-in window (matching timescale: the volatility metric is
         the W-day rolling population σ already computed by the parent
         pipeline).
   A base window with fewer than HYPE_THRESHOLD_MIN_PERIODS (255 = 1
   trading year) observations has no thresholds -> the date is not
   hyped. Bases near the start / end of a code's history are naturally
   truncated — the newest dates have no future rows yet, so their base
   is effectively the trailing 10y. Because the base looks both ways,
   historical rows use their FOLLOWING decade (retrospective audit,
   look-ahead by design): as new data arrives, the ideal thresholds of
   the last 10 years of dates shift — run a --force rebuild to refresh
   historical rows' flags.

2. CHECK-IN CONDITION (per date s):
     checkin[s] = trading_amount[s] > trading_amt_threshold[s]
                  AND std_{W}days[s] > std_threshold[s]
   Strict > on both legs. NULL turnover / σ counts as NOT a check-in.

3. SATISFACTION (per date t, "within min_checkin_period from today
   date"):
     is_hyped[t] = (count of check-in dates within the last W rows
                    ending at t, inclusive) / W * 100
                   > HYPE_CHECKIN_SATISFACTION_THRESHOLD
   Strict greater-than. The denominator is the full W rows — missing
   data counts against satisfaction. The first W-1 rows of each code
   have no full window -> not hyped.

4. EPISODE CONCAT + EXTENSION + BUCKETING (what the table stores):
   The per-date is_hyped series from (3) is collapsed, per (sec_type,
   code, min_checkin_period), into maximal runs of CONSECUTIVE hyped
   dates ("cores"), then each core is extended through the check-in
   evidence that fed its satisfaction — bridging interior non-check-in
   days — and finally bucketed by its SPAN:
     - CONCAT/EXTEND start: the FIRST check-in within the W rows
       ending at the core's first hyped date (the lookback window that
       produced the core's first satisfaction verdict — its earliest
       evidence). This is what lets an episode start at the FIRST
       big-move day of a turmoil instead of ~W rows later, when the
       trailing satisfaction count finally crosses the threshold (the
       2024-09-24 rally audit, 159673.SZ: the 20d satisfaction only
       crossed 60% on 2024-10-21 — a full month late — while the
       check-ins began on the rally's day 1).
     - CONCAT/EXTEND end: symmetric — the LAST check-in within the W
       rows starting at the core's last hyped date (the decaying tail).
     - Episodes never overlap within one bucket: each episode's start
       is clipped to just after the previous episode's end.
     - BUCKET BOUNDS: hype_days (the span in trading dates, start and
       end inclusive) must satisfy W <= hype_days < HYPE_EPISODE_SPAN_MAX
       [W] (the NEXT check-in window; the longest window is bounded by
       HYPE_MAX_EPISODE_ROWS = the whole ±10y base ≈ 20 trading years).
       min_checkin_period IS the minimum episode span; the next window
       is the exclusive maximum — e.g. 20d-bucket episodes span 20..59
       rows, 60d-bucket 60..119, 255d-bucket 255..5100. A core whose
       own consecutive span already reaches the bucket max is dropped
       from that bucket: sustained activity of that length is the
       domain of the NEXT bucket up, whose own (longer-window,
       smoother) satisfaction flags it.
     - trading_amt_hype_days / std_hype_days count the days within the
       stored span on which each leg individually checked in
       (diagnostics for which leg drove the episode).
   Only qualifying spans are stored — non-hyped dates leave no
   footprint (the pre-episode revision wrote one is_hyped row per
   date, TRUE and FALSE alike; superseded).

Row multiplicity: one row per EPISODE per check-in window in
HYPE_CHECKIN_PERIODS (5/20/60/120/255). min_checkin_period IS part of
the PK — different windows can produce episodes with identical spans,
so the window must disambiguate the rows. The three threshold columns
record the build's parameter set; they are NOT part of the PK —
changing them requires a --force rebuild.

Source: the same source DataFrame already loaded by the parent
mov_ave_spread.fetch_source_data — reuses the ``trading_amount`` and
``std_{W}days`` columns (the σ columns are pre-computed by
helpers.compute_rolling_stds in the parent pipeline). No second DB
round-trip.

This module is an INTERNAL step of analyze.mov_ave_spread — invoked
from __main__.py right after the trading-amt-ratios step, reusing the
same DB connection + source DataFrame.

REBUILD SEMANTICS (margin_changes precedent): episode boundaries shift
whenever new dates arrive (the trailing episode of a code extends; the
centered threshold windows move), and non-hyped dates leave no
footprint — date-level coverage cannot be diffed against an episodes
table. There is therefore NO per-date incremental upsert: every run
DELETEs the step's entire scope (one sec_type — or one code in --code
mode, where the caller already deleted the code's rows) and recomputes
ALL episodes from the FULL per-code history. The step re-runs whenever
the parent pipeline processes its sec_type; the parent's up-to-date
early-skip (detail / trading_amt / ratios / OHLC missing dates + an
empty-hypes check) governs whether that happens.

Force mode (``force=True``): API-compatible extra — the parent's
--force truncates the table upfront; the step's scoped DELETE already
guarantees a clean slate for its scope either way.

GPU note: the centered percentile uses pandas ``groupby(...).rolling
(center=True).quantile()``. cuDF lacks rolling-quantile support, so
when cudf.pandas is active this op transparently falls back to the CPU
pandas implementation (same contract as the grouped-EWM helper in
rsi.py). The episode assembly itself runs on host numpy arrays
(extracted once per window) — the run/extension math is
index-arithmetic-heavy and searchsorted-based, which cuDF does not
express; the extraction cost is one host copy of three boolean columns
per window, accepted for a much simpler and faster algorithm.
"""
from __future__ import annotations

import asyncio
import time

import numpy as np
import pandas as pd

from _common.build_commons import copy_insert_async
from _common.df_utils import column_subset, host_array, safe_columns
from analyze._common import (
    sanitize_for_db_insert,
    upsert_analysis_identity,
)
from analyze.mov_ave_spread.config import (
    HYPE_CHECKIN_PERIODS,
    HYPE_CHECKIN_SATISFACTION_THRESHOLD,
    HYPE_EPISODE_SPAN_MAX,
    HYPE_STD_COLUMN_BY_PERIOD,
    HYPE_STD_THRESHOLD_PCT,
    HYPE_THRESHOLD_HALF_WINDOW_ROWS,
    HYPE_THRESHOLD_MIN_PERIODS,
    HYPE_THRESHOLD_WINDOW_ROWS,
    HYPE_TRADING_AMT_THRESHOLD_PCT,
    MARKET_HYPES_ANALYSIS_NAME,
    MARKET_HYPES_COLUMNS,
    MARKET_HYPES_DESCRIPTION,
    MARKET_HYPES_TABLE,
)


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

# Episode rows per COPY chunk (bounds the row-dict list materialized
# between the DataFrame and asyncpg's COPY stream — same spirit as
# DEFAULT_CHUNK_TARGET_ROWS in analyze._common.upsert).
_EPISODE_CHUNK_ROWS = 100_000


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
    to realign — the same contract as ``_grouped_ewm_pandas`` in rsi.py.

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


def sanitize_market_hypes_rows(df: pd.DataFrame) -> list[dict]:
    """Sanitize one chunk of the episodes frame for asyncpg COPY
    (NaN/inf -> None + to_dict).

    The frame must already carry the three recorded build-parameter
    columns (attached by run_market_hypes). The NUMERIC(6,4) parameter
    columns are rounded to 4 decimal places; the date / integer key
    columns pass through.
    """
    if df.empty:
        return []
    numeric_params = [
        "min_checkin_satisfaction_threshold",
        "min_trading_amt_threshold",
        "min_std_threshold",
    ]
    return sanitize_for_db_insert(
        df, numeric_cols=numeric_params, round_to=4,
    )


async def _copy_episodes_chunked(
    conn, pool, episodes: pd.DataFrame, *, max_concurrent: int,
) -> int:
    """COPY-insert the episodes frame in row-count chunks.

    Bounds peak memory like build_and_insert_chunked (never
    materializes the full row-dict list): each ~_EPISODE_CHUNK_ROWS
    slice is sanitized inside the concurrency semaphore, then streamed
    via COPY on ``conn`` (sequential) or on a pool connection
    (parallel). COPY is safe because the caller DELETEd the whole
    scope first — the inserted episodes are guaranteed conflict-free.

    Chunks are row-count slices (not date-bounded): episode rows carry
    no single "date" key and each (sec_type, code, start_date,
    end_date, min_checkin_period) PK appears exactly once in the frame,
    so no two chunks can conflict even under parallel COPY.
    """
    n_total = len(episodes)
    if n_total == 0:
        return 0

    bounds = [
        (lo, min(lo + _EPISODE_CHUNK_ROWS, n_total))
        for lo in range(0, n_total, _EPISODE_CHUNK_ROWS)
    ]
    n_chunks = len(bounds)
    columns = list(MARKET_HYPES_COLUMNS)

    use_parallel = (
        pool is not None and max_concurrent > 1 and n_chunks > 1
    )
    if not use_parallel:
        total = 0
        for i, (lo, hi) in enumerate(bounds, start=1):
            rows = sanitize_market_hypes_rows(episodes.iloc[lo:hi])
            n = await copy_insert_async(
                conn, MARKET_HYPES_TABLE, rows, columns=columns,
            )
            total += n
            print(f"      episodes chunk {i}/{n_chunks}: COPY {n:,} rows "
                  f"(cumulative {total:,})", flush=True)
        return total

    pool_max = getattr(pool, "_maxsize", max_concurrent)
    concurrency = max(1, min(max_concurrent, n_chunks, pool_max))
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    counter = [0]
    print(f"      parallel COPY: {n_chunks} chunks, {concurrency} "
          f"concurrent (pool max_size={pool_max})", flush=True)

    async def _task(i: int, lo: int, hi: int) -> int:
        # Acquire the semaphore BEFORE building the chunk's dicts so at
        # most ``concurrency`` chunks' rows are in flight (the same
        # memory bound as build_and_insert_chunked).
        async with sem:
            rows = sanitize_market_hypes_rows(episodes.iloc[lo:hi])
            async with pool.acquire() as c:
                n = await copy_insert_async(
                    c, MARKET_HYPES_TABLE, rows, columns=columns,
                )
        async with lock:
            counter[0] += n
            so_far = counter[0]
        print(f"      episodes chunk {i}/{n_chunks} done: COPY {n:,} rows "
              f"(cumulative {so_far:,})", flush=True)
        return n

    results = await asyncio.gather(*[
        _task(i, lo, hi) for i, (lo, hi) in enumerate(bounds, start=1)
    ])
    return sum(results)


# ---------------------------------------------------------------------------
#  Pipeline (internal step — invoked from mov_ave_spread.__main__)
# ---------------------------------------------------------------------------

async def run_market_hypes(
    conn,
    df: pd.DataFrame,
    *,
    force: bool = False,
    pool=None,
    max_concurrent: int = 20,
    sec_type: str | None = None,
    code_filter: str | None = None,
) -> None:
    """Run the market-hypes EPISODE pipeline against the source data
    already loaded by the parent mov_ave_spread.

    Reuses the caller's DB connection and source DataFrame (the
    ``trading_amount`` and ``std_{W}days`` columns are reused — no
    second DB fetch). The DataFrame must contain the FULL per-code
    history so the centered ±10y percentile windows have enough rows on
    each side of every date (up to 2550 per side).

    Pipeline
      1. Compute the per-window is_hyped + check-in + per-leg flags over
         the FULL per-code history (centered percentile thresholds +
         check-in counts).
      2. Assemble the hyped runs into CONCATENATED episodes (extend
         through the check-in evidence; bucket by span into
         [W, next window)) — start_date / end_date / hype_days /
         trading_amt_hype_days / std_hype_days per window.
      3. DELETE the step's entire scope (the given sec_type — or the
         single --code, whose rows the caller already deleted), then
         COPY-insert the recomputed episodes. There is no per-date
         incremental upsert: episode boundaries shift when new dates
         arrive and non-hyped dates leave no footprint
         (margin_changes precedent).
      4. Upsert analysis.analysis_identity registry.

    Args:
      conn: asyncpg connection (reused from parent).
      df: source DataFrame with at least columns [sec_type, code, date,
          trading_amount, std_5days, std_20days, std_60days,
          std_120days, std_255days]. Must be the FULL per-code history
          (the centered ±10y percentile windows need up to 2550 rows on
          EACH side of every date).
      force: accepted for API compatibility with the other internal
          steps — the rebuild is wholesale regardless. The parent's
          --force additionally truncates the table upfront.
      pool: optional connection pool for parallel COPY chunks.
      max_concurrent: maximum parallel COPY chunks.
      sec_type: when provided, process only this sec_type (parent loop
                passes one sec_type at a time to bound memory). When
                None, infers sec_types from the DataFrame.
      code_filter: single-code mode (--code): rebuild the episodes of
                   this code only.
    """
    t0 = time.time()
    print("\n" + "=" * 78, flush=True)
    print("  MOV_AVE_MARKET_HYPES (internal step of mov_ave_spread)",
          flush=True)
    print("=" * 78, flush=True)

    if code_filter is not None:
        print(f"    mode: SINGLE-CODE (wholesale episode rebuild for "
              f"{code_filter})", flush=True)
    elif force:
        print("    mode: FORCE (wholesale episode rebuild; the parent "
              "truncated the table upfront)", flush=True)
    else:
        print("    mode: WHOLESALE PER-SEC_TYPE (episodes are rebuilt on "
              "every run — new dates shift episode boundaries)",
              flush=True)

    needed_cols = list(dict.fromkeys(
        ["sec_type", "code", "date", "trading_amount"]
        + [HYPE_STD_COLUMN_BY_PERIOD[w] for w in HYPE_CHECKIN_PERIODS]
    ))
    available = column_subset(df, needed_cols)
    hype_df = df[available].copy()

    if hype_df.empty:
        print("    -> no source data; skipping market-hypes step.",
              flush=True)
        return

    if sec_type is not None:
        sec_types = (sec_type,)
    else:
        sec_types = tuple(sorted(hype_df["sec_type"].unique()))

    # ---- Step 1: compute is_hyped per window over full history ------
    print("\n[h1/3] Computing market-hype flags per check-in window "
          f"({', '.join(str(w) for w in HYPE_CHECKIN_PERIODS)} rows; "
          f"centered ±{HYPE_THRESHOLD_HALF_WINDOW_ROWS}-row "
          f"(20y total) percentile thresholds at "
          f"{HYPE_TRADING_AMT_THRESHOLD_PCT:.1f}% amt / "
          f"{HYPE_STD_THRESHOLD_PCT:.1f}% std; satisfaction > "
          f"{HYPE_CHECKIN_SATISFACTION_THRESHOLD:.1f}%)...",
          flush=True)
    hype_df = hype_df.sort_values(
        ["sec_type", "code", "date"]
    ).reset_index(drop=True)
    hype_df = compute_market_hypes(hype_df)

    # ---- Step 2: assemble concat/extended/bucketed episodes ---------
    print("\n[h2/3] Assembling hyped episodes (concat through check-in "
          f"evidence; span buckets [W, next window) for "
          f"{', '.join(str(w) for w in HYPE_CHECKIN_PERIODS)} "
          f"windows)...", flush=True)
    episodes = hype_episodes(hype_df)
    del hype_df
    # Attach the recorded build parameters + fix the table column order.
    episodes = episodes.reindex(columns=list(MARKET_HYPES_COLUMNS))
    episodes["min_checkin_satisfaction_threshold"] = (
        HYPE_CHECKIN_SATISFACTION_THRESHOLD
    )
    episodes["min_trading_amt_threshold"] = HYPE_TRADING_AMT_THRESHOLD_PCT
    episodes["min_std_threshold"] = HYPE_STD_THRESHOLD_PCT
    n_codes = (
        episodes[["sec_type", "code"]].drop_duplicates().shape[0]
        if not episodes.empty
        else 0
    )
    print(f"    -> {len(episodes):,} episodes across {n_codes:,} "
          f"(sec_type, code) groups", flush=True)

    # ---- Step 3: replace the scope's rows wholesale -----------------
    if code_filter is not None:
        for st in sec_types:
            status = await conn.execute(
                f"DELETE FROM {MARKET_HYPES_TABLE} "
                f"WHERE sec_type = $1 AND code = $2",
                st, code_filter,
            )
            n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
            print(f"    -> deleted {n_del:,} existing episode rows "
                  f"({st}/{code_filter})", flush=True)
    else:
        for st in sec_types:
            status = await conn.execute(
                f"DELETE FROM {MARKET_HYPES_TABLE} WHERE sec_type = $1",
                st,
            )
            n_del = int(status.rsplit(" ", 1)[-1]) if status else 0
            print(f"    -> deleted {n_del:,} existing episode rows "
                  f"({st})", flush=True)

    n = await _copy_episodes_chunked(
        conn, pool, episodes, max_concurrent=max_concurrent,
    )
    del episodes
    print(f"    -> inserted {n:,} episode rows", flush=True)

    # ---- Step 4: register in analysis_identity ----------------------
    print(f"\n[h3/3] Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=MARKET_HYPES_ANALYSIS_NAME,
        detail_name="mov_ave_market_hypes",
        description=MARKET_HYPES_DESCRIPTION,
    )

    print(f"\n  mov_ave_market_hypes wall time: "
          f"{time.time() - t0:.1f}s", flush=True)
