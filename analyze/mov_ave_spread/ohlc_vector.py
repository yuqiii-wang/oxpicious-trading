"""Vectorised anchor computation for analyze.mov_ave_spread OHLC columns.

Drop-in replacement for the per-window ``rolling().apply(raw=True)``
callbacks (``_compute_group_ohlc_columns`` in ``ohlc.py``, kept there as
the reference implementation for A/B regression).  Output semantics are
IDENTICAL (same anchor positions, values, dates and NULL handling):

  region(i, w, cd) = [max(0, i-w+1), i-cd-1]        (close-based)
  top anchor       = first-occurrence argmax/argmin of CLOSE over the
                     region; value = that date's CLOSE
  2nd anchor       = best local-max/min CLOSE peak of the region
                     strictly AFTER the top anchor, more than cd
                     trading days later; value = intraday high/low

How the O(n*w) rolling callbacks become O(n log n) array ops:

  * Sparse-table ("doubling") argmax over the whole group answers any
    region's first-occurrence argmax with two gathers + one compare.
  * A region's local extrema decompose EXACTLY into
      (a) global interior extrema — positions whose GLOBAL prev/next
          valid neighbours satisfy the extremum rule — that fall inside
          the region, plus
      (b) the region's first/last valid positions under the compacted
          boundary rules (clean[0] >= clean[1] / clean[-1] > clean[-2]).
    For any region-interior valid position the global prev/next valid
    neighbours coincide with the region's compacted neighbours, so (a)
    needs no region-relative recomputation; the boundary positions (b)
    are two per-row searchsorted lookups.
  * The ``pos - top > cd`` after-top constraint restricts candidate
    positions to the single sub-range [top+cd+1, b], answered by a sparse
    table built once per group over the extremum array.
  * NaN closes are skipped like ``nanargmax`` via a -inf fill in "sign
    space" (close for the max side, -close for the min side) plus a
    per-row valid-count mask; all-NaN regions yield NULL anchors.

Everything is plain NumPy per (sec_type, code) group — no cuDF
rolling-apply CPU fallback anywhere.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd


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


def _combine_positions(
    pA: np.ndarray,
    pB: np.ndarray,
    svals: np.ndarray,
) -> np.ndarray:
    """Better of two candidate positions: sval descending, tie -> earlier.

    ``svals`` is the sign-space close array (NaN-free, -inf filled).  -1
    encodes "absent" and never wins unless both candidates are absent.
    """
    vA: np.ndarray = np.where(
        pA >= 0, svals[np.where(pA >= 0, pA, 0)], -np.inf
    )
    vB: np.ndarray = np.where(
        pB >= 0, svals[np.where(pB >= 0, pB, 0)], -np.inf
    )
    takeA: np.ndarray = (vA > vB) | ((vA == vB) & (pA <= pB))
    return np.where(takeA, pA, pB)


# ---------------------------------------------------------------------------
#  2nd anchor (best region extremum strictly AFTER the top anchor)
# ---------------------------------------------------------------------------
def _second_extremum_pos(
    top: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
    cd: int,
    has_ext: np.ndarray,
    nv: np.ndarray,
    svals: np.ndarray,
    V: np.ndarray,
    E: np.ndarray,
    Ev: np.ndarray,
    etbl: np.ndarray,
) -> np.ndarray:
    """Position of the 2nd anchor, or -1 when absent.

    Mirrors ``_second_peak_max_pos_today_constrained_raw`` /
    ``_second_peak_min_pos_today_constrained_raw`` exactly: candidates are
    the region's local extrema (interior + compacted-boundary rules) that
    lie MORE THAN ``cd`` trading days AFTER the top anchor (positions in
    [top+cd+1, b]); the winner is the highest sign-space value, ties going
    to the earliest position (stable-sort semantics).

    The region's FIRST valid position can never qualify (it is <= the top
    anchor, which is itself a valid region position), so only the
    right-boundary extremum joins the interior-extremum candidates.

    Args:
        top: top-anchor positions (-1 where absent).
        a, b: region bounds per row (inclusive).
        cd: cooldown (trading days).
        has_ext: rows whose region is nonempty with >= 1 valid close.
        nv: count of valid closes inside each region.
        svals: sign-space close (NaN -> -inf) over the whole group.
        V: global positions of valid closes (ascending).
        E: global positions of global interior extrema (ascending).
        Ev: sign-space values at those extrema (real, no NaN).
        etbl: sparse argmax table over ``Ev``.
    """
    if len(V) == 0:
        return np.full(len(a), -1, dtype=np.int64)

    topm: np.ndarray = np.where(has_ext, top, -1)

    # -- interior-extremum sub-range strictly after the top anchor --
    # [max(a, top+cd+1), b]
    rlo: np.ndarray = np.maximum(a, topm + cd + 1)
    loR: np.ndarray = np.searchsorted(E, rlo, side="left")
    hiR: np.ndarray = np.searchsorted(E, b, side="right") - 1
    qR: np.ndarray = _range_argmax(etbl, Ev, loR, hiR, has_ext)
    c2: np.ndarray = _gather_pos(E, qR)

    # -- region right-boundary extremum (compacted-array rule) --
    mj: np.ndarray = np.searchsorted(V, b, side="right") - 1  # last valid <= b
    has2: np.ndarray = has_ext & (nv >= 2)
    M: int = len(V)
    pR: np.ndarray = np.where(
        has2 & (mj >= 0), V[np.clip(mj, 0, M - 1)], -1
    )
    prv: np.ndarray = np.where(
        has2 & (mj - 1 >= 0), V[np.clip(mj - 1, 0, M - 1)], -1
    )
    # right-boundary rule in sign space (max-side form; min side via
    # negation): sval > previous
    pR_ok: np.ndarray = (pR >= 0) & (prv >= 0) & (
        svals[np.where(pR >= 0, pR, 0)]
        > svals[np.where(prv >= 0, prv, 0)]
    )
    pR_ok &= (pR - topm) > cd
    c4: np.ndarray = np.where(pR_ok, pR, -1)

    return _combine_positions(c2, c4, svals)


# ---------------------------------------------------------------------------
#  Per-group driver (all windows in one call — tables are shared)
# ---------------------------------------------------------------------------
def compute_group_anchors_all_windows(
    g: pd.DataFrame,
    windows: Sequence[int],
    cooldowns: Sequence[int],
) -> pd.DataFrame:
    """Compute ALL anchor VALUE + DATE columns for one (sec_type, code) group.

    Args:
        g: DataFrame with columns [date, price, high, low] sorted by date
           (the FULL per-code history).  The group's index is preserved on
           the result.
        windows: rolling window sizes (trading days).
        cooldowns: per-window minimum separations (trading days),
                   aligned with ``windows``.

    Returns:
        DataFrame indexed like ``g`` with, per window W, the columns
        high_Wd, high_date_Wd, high_2nd_Wd, high_2nd_date_Wd,
        high_line_slope_Wd, low_Wd, low_date_Wd, low_2nd_Wd,
        low_2nd_date_Wd, low_line_slope_Wd.
    """
    n: int = len(g)
    close: np.ndarray = g["price"].to_numpy(dtype=np.float64)
    high: np.ndarray = g["high"].to_numpy(dtype=np.float64)
    low: np.ndarray = g["low"].to_numpy(dtype=np.float64)
    dates: np.ndarray = g["date"].to_numpy()

    idx: np.ndarray = np.arange(n, dtype=np.int64)
    isval: np.ndarray = ~np.isnan(close)
    pref: np.ndarray = np.concatenate(([0], np.cumsum(isval))).astype(np.int64)

    out: dict[str, np.ndarray] = {}
    # One sign-parametrised code path: sign-space svals = sign * close so
    # argmin/min-side rules become argmax/max-side rules under negation.
    for sign, tag, second_src in ((1.0, "high", high), (-1.0, "low", low)):
        svals: np.ndarray = np.where(isval, sign * close, -np.inf)
        tbl: np.ndarray = _build_argmax_table(svals)

        # Global interior extrema on the compacted valid sequence.
        V: np.ndarray = np.flatnonzero(isval).astype(np.int64)
        M: int = len(V)
        sv: np.ndarray = sign * close[V]
        emask: np.ndarray = np.zeros(M, dtype=bool)
        if M >= 3:
            emask[1:-1] = (sv[1:-1] > sv[:-2]) & (sv[1:-1] >= sv[2:])
        E: np.ndarray = V[emask]
        Ev: np.ndarray = sv[emask]
        etbl: np.ndarray = _build_argmax_table(Ev)

        for w, cd in zip(windows, cooldowns):
            a: np.ndarray = np.maximum(0, idx - w + 1)
            b: np.ndarray = idx - cd - 1
            ok: np.ndarray = b >= a
            bsafe: np.ndarray = np.where(ok, b, 0)
            nv: np.ndarray = pref[bsafe + 1] - pref[a]
            has_ext: np.ndarray = ok & (nv > 0)

            top: np.ndarray = _range_argmax(tbl, svals, a, b, has_ext)
            sec: np.ndarray = _second_extremum_pos(
                top, a, b, cd, has_ext, nv, svals, V, E, Ev, etbl,
            )
            out[f"{tag}_{w}d"] = _gather_float(close, top)
            out[f"{tag}_date_{w}d"] = _gather_date(dates, top)
            out[f"{tag}_2nd_{w}d"] = _gather_float(second_src, sec)
            out[f"{tag}_2nd_date_{w}d"] = _gather_date(dates, sec)
            # Roof/floor line slope through the two anchors, in price
            # units per trading day: (2nd value - top value) / (2nd pos -
            # top pos). The 2nd anchor is strictly more than cd trading
            # days after the top, so the denominator is >= cd+1 (never
            # 0). NaN when either anchor is absent or the 2nd anchor's
            # intraday value is NaN (matching {tag}_2nd_{w}d NULL).
            slope: np.ndarray = np.full(n, np.nan)
            ok2: np.ndarray = (top >= 0) & (sec >= 0)
            if ok2.any():
                t_pos: np.ndarray = top[ok2]
                s_pos: np.ndarray = sec[ok2]
                slope[ok2] = (
                    second_src[s_pos] - close[t_pos]
                ) / (s_pos - t_pos)
            out[f"{tag}_line_slope_{w}d"] = slope

    return pd.DataFrame(out, index=g.index)
