"""Vectorised anchor computation for analyze.mov_ave_spread OHLC columns.

Drop-in replacement for the per-window reference sweep
(``_compute_group_ohlc_columns`` in ``ohlc.py``, kept there as the
reference implementation for A/B regression).  Output semantics are
IDENTICAL (same anchor positions, values, dates and NULL handling):

  region(i, w)     = [max(0, i-w+1), i]             (close-based, FULL
                    window — no cooldown truncation)
  1st anchor       = argmax (max/min sign-space) of valid CLOSE in the
                    1st half [a, a+h-1] where h = L // 2.  Ties ->
                    earliest date.  NULL when the half holds no valid
                    close.
  2nd anchor       = argmax of valid CLOSE in [top + gap, b] where
                    gap = ohlc_second_gap_td(w) — ceil(0.20*W) with a
                    20td floor for W >= 60 (config.py). The time-distance
                    gap is enforced CONSTRUCTIVELY by shifting the 2nd
                    anchor's search range so the result is guaranteed to
                    be at least `gap` trading days after the 1st anchor.
                    Ties -> earliest.  NULL when the dynamic
                    range has no valid close OR when the 1st anchor is
                    too close to b (top + gap > b).  The 1st anchor
                    stays valid in that case.

Everything is plain NumPy per (sec_type, code) group — no cuDF
rolling-apply CPU fallback anywhere.

How the O(n*w) reference sweep becomes O(n log w) array ops:

  * Sparse-table ("doubling") argmax over the group's valid closes
    answers any half's first-occurrence argmax with two gathers + one
    compare — one static table per side, shared by all windows.
  * NaN closes are skipped like ``nanargmax`` via a -inf fill in "sign
    space" (close for the max side, -close for the min side).

Everything is plain NumPy per (sec_type, code) group — no cuDF
rolling-apply CPU fallback anywhere.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from _common.df_utils import host_array
from analyze.mov_ave_spread.config import ohlc_second_gap_td


# ---------------------------------------------------------------------------
#  Sparse-table range argmax (first occurrence)
# ---------------------------------------------------------------------------
def _build_argmax_table(vals: np.ndarray) -> np.ndarray:
    """Sparse table for first-occurrence range argmax over ``vals``.

    ``table[k][j]`` is the argmax (first occurrence on ties) of
    ``vals[j : j + 2**k]`` clipped to the array end.  ``vals`` must be
    NaN-free; map NaN/absent to -inf before calling.
    """
    n: int = len(vals)
    if n == 0:
        return np.empty((0, 0), dtype=np.int64)
    levels: int = int(np.floor(np.log2(n))) + 1
    table: np.ndarray = np.empty((levels, n), dtype=np.int64)
    table[0] = np.arange(n, dtype=np.int64)
    for k in range(1, levels):
        half: int = 1 << (k - 1)
        prev: np.ndarray = table[k - 1]
        # right[j] = prev[j + half] when the right half exists, else prev[j]
        right: np.ndarray = np.empty(n, dtype=np.int64)
        if half < n:
            right[: n - half] = prev[half:]
            right[n - half:] = prev[n - half:]
        else:
            right[:] = prev
        table[k] = np.where(vals[prev] >= vals[right], prev, right)
    return table


def _range_argmax(
    table: np.ndarray,
    vals: np.ndarray,
    lo: np.ndarray,
    hi: np.ndarray,
    valid: np.ndarray,
) -> np.ndarray:
    """Per-row first-occurrence argmax position of ``vals[lo .. hi]``.

    Rows outside ``valid`` (or with empty/out-of-bounds ranges) get -1.
    ``lo`` and ``hi`` are inclusive bounds in the table's domain.
    """
    out: np.ndarray = np.full(len(lo), -1, dtype=np.int64)
    n: int = table.shape[1]
    if n == 0:
        return out
    q: np.ndarray = valid & (lo <= hi) & (lo >= 0) & (hi < n)
    if not q.any():
        return out
    l: np.ndarray = np.where(q, lo, 0)
    h: np.ndarray = np.where(q, hi, 0)
    ln: np.ndarray = h - l + 1
    k: np.ndarray = np.floor(np.log2(ln)).astype(np.int64)
    p1: np.ndarray = table[k, l]
    p2: np.ndarray = table[k, h - np.left_shift(1, k) + 1]
    res: np.ndarray = np.where(vals[p1] >= vals[p2], p1, p2)
    out[q] = res[q]
    return out


# ---------------------------------------------------------------------------
#  Safe gathers (-1 = absent)
# ---------------------------------------------------------------------------
def _gather_pos(arr: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """``arr[idx]`` for idx >= 0, -1 elsewhere (``arr`` may be empty)."""
    out: np.ndarray = np.full(len(idx), -1, dtype=np.int64)
    m: np.ndarray = idx >= 0
    if m.any():
        out[m] = arr[idx[m]]
    return out


def _gather_float(arr: np.ndarray, pos: np.ndarray) -> np.ndarray:
    out: np.ndarray = np.full(len(pos), np.nan, dtype=np.float64)
    m: np.ndarray = pos >= 0
    if m.any():
        out[m] = arr[pos[m]]
    return out


def _gather_date(dates: np.ndarray, pos: np.ndarray) -> np.ndarray:
    out: np.ndarray = np.full(len(pos), np.datetime64("NaT", "ns"))
    m: np.ndarray = pos >= 0
    if m.any():
        out[m] = dates[pos[m]]
    return out


# ---------------------------------------------------------------------------
#  Half-split anchors (max/min close per window) with dynamic 2nd range
# ---------------------------------------------------------------------------
def _select_anchor_positions(
    a: np.ndarray,
    b: np.ndarray,
    w: int,
    has_ext: np.ndarray,
    V: np.ndarray,
    svals: np.ndarray,
    vtbl: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Positions of the (1st, 2nd) anchors, or -1 when absent.

    Phase 1 — 1st anchor: the sign-space argmax of valid closes in the
    1st half of the region [a, b], i.e. [a, a+h-1] where
    h = (b - a + 1) // 2.  Ties go to the earliest position.

    Phase 2 — 2nd anchor: after locating the 1st anchor at ``top``,
    the 2nd anchor is the argmax in [top + gap, b] where
    gap = ohlc_second_gap_td(w) — the time-distance gap is enforced
    CONSTRUCTIVELY by starting the 2nd search `gap` trading days
    AFTER the 1st anchor.  If the 1st anchor is so close to ``b``
    that top + gap > b, the 2nd anchor comes back -1 but the 1st
    anchor remains valid.

    ``vtbl`` is the sparse argmax table over ``svals[V]`` — the valid-
    close positions — so each query is a plain range argmax.  NaN
    closes are -inf in svals and therefore skipped.  Ties -> earliest.

    Args:
      a: int ndarray of window-start positions.
      b: int ndarray of row positions (window end).
      w: rolling window size in trading days.
      has_ext: bool ndarray — True when the window has any valid close.
      V: sorted int ndarray of valid-close group positions.
      svals: float ndarray of sign-space values at ALL positions.
      vtbl: sparse argmax table over svals[V].
    """
    n_rows: int = len(a)
    if len(V) == 0:
        z: np.ndarray = np.full(n_rows, -1, dtype=np.int64)
        return z, z.copy()

    # Phase 1 — 1st anchor in the 1st half [a, a+h-1]
    h: np.ndarray = (b - a + 1) // 2
    ok1: np.ndarray = has_ext & (h >= 1)
    mid: np.ndarray = a + h  # exclusive end of the 1st half

    j1lo: np.ndarray = np.searchsorted(V, a, side="left")
    j1hi: np.ndarray = np.searchsorted(V, mid, side="left") - 1
    q1: np.ndarray = _range_argmax(
        vtbl, svals[V],
        np.where(ok1, j1lo, -1), np.where(ok1, j1hi, -1), ok1,
    )
    top: np.ndarray = _gather_pos(V, q1)  # -1 when 1st half empty

    # Phase 2 — 2nd anchor from dynamic range [top + gap, b]
    # Enforce the time-distance gap construction (20% of W, 20td floor
    # for W >= 60 — see config.ohlc_second_gap_td).
    gap: int = ohlc_second_gap_td(w)
    j2lo_abs: np.ndarray = top + gap  # absolute start positions
    j2hi_abs: np.ndarray = b          # absolute end positions
    ok2: np.ndarray = (top >= 0) & (j2lo_abs <= b)
    if not ok2.any():
        sec: np.ndarray = np.full(n_rows, -1, dtype=np.int64)
    else:
        j2lo: np.ndarray = np.searchsorted(V, j2lo_abs, side="left")
        j2hi: np.ndarray = np.searchsorted(V, j2hi_abs, side="right") - 1
        q2: np.ndarray = _range_argmax(
            vtbl, svals[V],
            np.where(ok2, j2lo, -1), np.where(ok2, j2hi, -1), ok2,
        )
        sec = _gather_pos(V, q2)  # -1 when dynamic range has no valid close

    return top, sec


# ---------------------------------------------------------------------------
#  Per-group driver (all windows in one call)
# ---------------------------------------------------------------------------
def compute_group_anchors_all_windows(
    g: pd.DataFrame,
    windows: Sequence[int],
) -> pd.DataFrame:
    """Compute ALL anchor VALUE + DATE columns for one (sec_type, code) group.

    Args:
        g: DataFrame with columns [date, price, high, low] sorted by
           date (the FULL per-code history).  The group's index is
           preserved on the result.
        windows: rolling window sizes (trading days).

    Returns:
        DataFrame indexed like ``g`` with, per window W, the columns
        high_Wd, high_date_Wd, high_2nd_Wd, high_2nd_date_Wd,
        high_line_slope_Wd, low_Wd, low_date_Wd, low_2nd_Wd,
        low_2nd_date_Wd, low_line_slope_Wd.
    """
    n: int = len(g)
    # Unwrap ONCE at the pandas→numpy boundary (B-A1 convention): arrays
    # from a proxied frame's .to_numpy() are proxy-subclass ndarrays
    # whose every downstream numpy op dispatches through the cudf
    # fast/slow machinery. host_array hands back RAW host arrays so the
    # whole compute body stays plain host numpy.
    # NOTE: no dtype= kwarg on to_numpy — cuDF's to_numpy(dtype=float64)
    # rejects NaN-carrying columns (ValueError -> fallback); unwrap via
    # host_array first, THEN astype in plain numpy.
    close: np.ndarray = host_array(g["price"].to_numpy()).astype("float64")
    high: np.ndarray = host_array(g["high"].to_numpy()).astype("float64")
    low: np.ndarray = host_array(g["low"].to_numpy()).astype("float64")
    dates: np.ndarray = host_array(g["date"].to_numpy())

    idx: np.ndarray = np.arange(n, dtype=np.int64)
    isval: np.ndarray = ~np.isnan(close)
    pref: np.ndarray = np.concatenate(([0], np.cumsum(isval))).astype(np.int64)

    out: dict[str, np.ndarray] = {}

    # One sign-parametrised code path: sign-space svals = sign * close so
    # the min-side rule becomes the same argmax rule under negation
    # (NaN closes -> -inf, skipped like nanargmax).  The sparse table
    # over the valid-close positions is shared by all windows.
    V: np.ndarray = np.flatnonzero(isval).astype(np.int64)
    for sign, tag, second_src in ((1.0, "high", high), (-1.0, "low", low)):
        svals: np.ndarray = np.where(isval, sign * close, -np.inf)
        vtbl: np.ndarray = (
            _build_argmax_table(svals[V]) if len(V) else
            np.empty((0, 0), dtype=np.int64)
        )

        for w in windows:
            # FULL window region [a, i] — no cooldown truncation.
            a: np.ndarray = np.maximum(0, idx - w + 1)
            b: np.ndarray = idx
            nv: np.ndarray = pref[b + 1] - pref[a]
            has_ext: np.ndarray = nv > 0

            top, sec = _select_anchor_positions(
                a, b, w, has_ext, V, svals, vtbl,
            )
            out[f"{tag}_{w}d"] = _gather_float(close, top)
            out[f"{tag}_date_{w}d"] = _gather_date(dates, top)
            out[f"{tag}_2nd_{w}d"] = _gather_float(second_src, sec)
            out[f"{tag}_2nd_date_{w}d"] = _gather_date(dates, sec)
            # Roof/floor line slope through the two anchors, in price
            # units per trading day: (2nd value - top value) / (2nd pos -
            # top pos).  The time-distance gap is enforced
            # constructively (2nd anchor searched from top + gap to b),
            # so sec - top >= gap > 0 wherever both exist.  NaN when
            # either anchor is absent.
            slope: np.ndarray = np.full(n, np.nan)
            ok2: np.ndarray = (top >= 0) & (sec >= 0)
            if ok2.any():
                t_pos: np.ndarray = top[ok2]
                s_pos: np.ndarray = sec[ok2]
                slope[ok2] = (
                    second_src[s_pos] - close[t_pos]
                ) / (s_pos - t_pos)
            out[f"{tag}_line_slope_{w}d"] = slope

    # Build the output with the REAL pandas class (B-A1 convention):
    # the proxied constructor walks columns of host ndarrays element-by-
    # element through the cudf fast path; the real class materializes
    # instantly and the per-code frames are batched by the caller.
    # Under plain (non-proxied) pandas there is no _fsproxy_slow — fall
    # back to the class itself (it IS the real class then).
    real_pd_df = getattr(pd.DataFrame, "_fsproxy_slow", pd.DataFrame)
    # Unwrap the proxy index ONCE — the REAL ctor would otherwise
    # dispatch on the proxy Index (Index._typ AttributeError fallback).
    return real_pd_df(out, index=host_array(g.index))
