"""Sparse per-horizon aggregation (analyze.analysis_forecasts.wide
.horizons).

``aggregate_horizons_sparse`` — batched per-(code, config) mean / std /
endpoint-extrema / reversal stats of ALL forward horizons
over the SPARSE trigger-cell lists of one (side, hyped) bucket subset.
Bincount / reduceat reductions: work scales with the ACTUAL trigger
count instead of the dense T·C·P tensor. With ``win_ord`` it also
gathers the per-horizon ragged trigger-date lists (the calendar dates
behind occurrence_count — the forecast_results.trigger_dates column),
plus the parallel streak-span / streak-day lists (when ``lens`` is
passed) and trigger-excess lists (when ``vals`` is passed).
"""
from __future__ import annotations

from datetime import date

import numpy as np

from analyze.analysis_forecasts.config import FORWARD_HORIZONS, MM_HORIZONS

# Per-horizon aggregate bundle returned by ``aggregate_horizons_sparse``
# (each numeric member a (C, P) array): occurrence counts, sum of changes,
# sum of SQUARED changes, max / min ENDPOINT change (None at the next-day
# horizon — the max_change / min_change columns),
# reversal count, and — when ``win_ord`` was passed — the ragged
# trigger-date lists (dict keyed by flat group id; None otherwise) plus,
# when ``lens`` was passed too, the parallel STREAK SPAN lists (the
# [start, end] calendar dates of each merged signal's qualifying run) and
# the parallel STREAK DAY counts (each run's trading-day length;
# None otherwise), plus — when ``vals`` was passed — the parallel TRIGGER
# EXCESS lists (each valid cell's value − qualifying bar; None otherwise).
HorizonAgg = tuple[np.ndarray, np.ndarray, np.ndarray,
                   np.ndarray | None, np.ndarray | None,
                   np.ndarray,
                   "dict[int, list[date]] | None",
                   "dict[int, list[date]] | None",
                   "dict[int, list[date]] | None",
                   "dict[int, list[int]] | None",
                   "dict[int, list[float]] | None"]


def aggregate_horizons_sparse(
    st: np.ndarray,
    sc: np.ndarray,
    flat: np.ndarray,
    C: int,
    P: int,
    side: str,
    NC0s: dict[int, np.ndarray],
    FINs: dict[int, np.ndarray],
    thr_n: dict[int, np.ndarray],
    win_ord: np.ndarray | None = None,
    lens: np.ndarray | None = None,
    path0s: dict[int, tuple[np.ndarray, np.ndarray]] | None = None,
    vals: np.ndarray | None = None,
) -> dict[int, HorizonAgg]:
    """Per-(code, config) aggregates of ALL forward horizons over the
    SPARSE trigger cells of one (side, hyped) subset.

    Vectorized replacement of the legacy per-config 2-D
    ``aggregate_horizon``: the subset is a list of trigger cells (row,
    col, flat = col·P + config) instead of a dense (T, C, P) tensor, so
    every reduction scales with the ACTUAL trigger count E (bucket
    density ≈ 1–25%) instead of T·C·P. All groups are accumulated with
    np.bincount; the max/min forward-change extrema (MM_HORIZONS only —
    the next-day horizon has none) use one argsort of the flat group ids
    + np.maximum/minimum.reduceat over the group-contiguous sorted cells.

    Args:
        st:   (E,) grid-row index of each subset cell (ASCENDING order
              after the caller's stable argsort of ``flat``).
        sc:   (E,) code index of each subset cell (same order).
        flat: (E,) group id = code·P + config, ASCENDING (the sort key).
        C:    number of codes.
        P:    number of configs in this side batch.
        side: bucket side — "top"/"upper" reverse on change < −thr,
              "bottom"/"lower" on change > +thr, with thr = the code's
              adaptive reverse threshold for that horizon
              (reverse_thresholds output).
        NC0s: per horizon n — (T, C) n-day forward change, 0.0 on
              invalid days (build_change_matrices), so unweighted sums
              over all cells are the valid-day sums.
        FINs: per horizon n — (T, C) validity bool (finite n-day change).
        thr_n: per horizon n — (C,) reversal bar (reverse_thresholds).
        path0s: per MM horizon n — the (FMAX0, FMIN0) path-extreme
              matrices (build_change_matrices), sliced to the window
              like NC0s. The SWING-AWARE reversal event consumes
              these: at any cell the
              ADVERSE PATH EXTREME (the window's lowest close for
              top/upper, highest for bottom/lower — signed) is what
              crosses the reversal bar. None falls back to the endpoint
              change everywhere (the 1-day path IS the endpoint).
        win_ord: optional (T,) int64 day ordinals of the window's grid
              rows (grid_ord[lo:hi]). When given, each horizon's bundle
              also carries the ragged trigger-date lists td — the
              calendar dates (datetime.date, ascending — cells are
              time-ascending within each group) of the group's bucket
              days with a VALID n-day forward change, keyed by flat
              group id, i.e. exactly the denominator of that horizon's
              cnt / mean / reverse_prob. Consumed by build_result_rows
              as the forecast_results.trigger_dates column; None member
              when win_ord is omitted.
        lens: optional (E,) int run length per subset cell (the merged
              streak's day count — apply_streak_midpoints' lens at the
              kept mid cells, subset- and sort-aligned like st/sc/flat).
              When given (event engines only) AND win_ord is given, the
              bundle also carries the parallel STREAK SPAN lists ss /
              se — per valid cell, the qualifying run's [start, end]
              calendar dates (mid row - (L-1)//2 .. mid row + L//2 on
              the grid; runs are clipped at the window slice so both
              ends always land inside) — and the parallel STREAK DAY
              lists sd — per valid cell, the run's trading-day count L.
              Consumed by build_result_rows as the
              forecast_results.streak_starts / streak_ends /
              streak_days columns (parallel to trigger_dates).
              None elsewhere.
        vals: optional (E,) float TRIGGER EXCESS per subset cell (the
              day's trigger value − the bucket's qualifying bar, signed;
              iter_bucket_subsets' exc, subset- and sort-aligned like
              st/sc/flat). When given, each horizon's bundle also
              carries the parallel TRIGGER EXCESS lists te — per valid
              cell (the SAME valid-cell subset as td), the cell's excess
              — consumed by build_result_rows as the
              forecast_results.trigger_excess column (parallel to
              trigger_dates). None (state engines / callers without a
              scalar qualifying bar) leaves the column NULL.

    Returns (per horizon n) — a HorizonAgg bundle of (C, P) numeric
    members (see HorizonAgg) plus the per-horizon td / ss / se / sd /
    te dicts:
        cnt — bucket days with a valid n-day forward change (int);
        s   — sum of the n-day changes over those days;
        s2  — sum of SQUARED n-day changes over those days (invalid days
              contribute 0.0 — NC0 semantics), the E[x²] half of the
              std_change numerator sqrt(E[x²] − E[x]²);
        hi_e — max ENDPOINT n-day change (-inf where cnt == 0; None for
              the next-day horizon — no MM columns);
        lo_e — min ENDPOINT n-day change (+inf where cnt == 0; None
              likewise);
        rev — count of reversal days (the period's ADVERSE PATH EXTREME
              beyond the code's bar against the bucket side — the
              within-period swing, not merely the period-end close)
              among those days (int);
        td  — {flat group id: [date, ...]} of the cnt days (None when
              win_ord is None);
        ss / se — {flat group id: [date, ...]} of each signal's
              qualifying-run start / end calendar date, parallel to td
              (None when win_ord or lens is None);
        sd  — {flat group id: [int, ...]} of each signal's qualifying-
              run trading-day count, parallel to td (None when win_ord
              or lens is None);
        te  — {flat group id: [float, ...]} of each valid cell's
              trigger excess, parallel to td (None when vals is None).
    """
    CP = C * P
    # Group scaffolding over the group-ascending cells: one sort shared
    # by every horizon's extrema reduction.
    bounds = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    starts = np.concatenate(([0], bounds))
    gid = flat[starts]
    rev_top = side in ("top", "upper")

    # Shared date objects of the window's grid rows (win_ord is int64
    # days-since-epoch — the same unit as build_grid's grid_ord). Every
    # group's date list slices THIS object array, so all lists of a
    # window share one small pool of date objects.
    wdate = (
        win_ord.astype("datetime64[D]").astype(object)
        if win_ord is not None else None
    )

    out: dict[int, HorizonAgg] = {}
    for n in FORWARD_HORIZONS:
        g = NC0s[n][st, sc]          # 0.0 on invalid days (NC0 semantics)
        v = FINs[n][st, sc]
        # Per-cell PATH extremes (the forward window's highest/lowest
        # close vs the signal close, signed; 0.0 on invalid days — the
        # FMAX0/FMIN0 NaN→0 semantics). At the next-day horizon the
        # path IS the endpoint (no separate matrices); a caller without
        # path matrices (compute_opp_pair — its forward quantity is a
        # relative-MA offset change, not a close path) falls back to
        # the endpoint everywhere (the legacy endpoint semantics).
        if n in MM_HORIZONS and path0s is not None:
            fh = path0s[n][0][st, sc]
            fl = path0s[n][1][st, sc]
        else:
            fh = fl = g
        cnt = np.bincount(flat[v], minlength=CP)
        s = np.bincount(flat, weights=g, minlength=CP)
        # g is 0.0 on invalid days, so the all-cell squared sum equals
        # the valid-day squared sum (the std E[x²] pass — same trick as
        # the mean sum above).
        s2 = np.bincount(flat, weights=g * g, minlength=CP)
        # Reversal at a cell — SWING-AWARE: the period's ADVERSE PATH
        # EXTREME (lowest close for top/upper, highest for bottom/
        # lower) beyond the code's bar, i.e. the forward window swung
        # ≥ thr against the bucket side AT ANY CLOSE of the period, not
        # merely at its end. Invalid cells hold 0.0 extremes, so the
        # threshold compare is False there — matches the legacy dense
        # 0/1 flag einsum exactly.
        thr_cell = thr_n[n][sc]
        adv = fl if rev_top else fh
        rv = (adv < -thr_cell) if rev_top else (adv > thr_cell)
        rev = np.bincount(flat[rv], minlength=CP)
        # Ragged trigger dates over the VALID cells (fk-ascending subset
        # of the fk-ascending cells): per group id, the contiguous
        # [lo, hi) slice of the valid cells' dates. Groups with no valid
        # day emit no entry (build_result_rows maps a miss to NULL).
        # The valid-cell group bounds are shared by the parallel
        # trigger-excess lists below.
        td = None
        ss = None
        se = None
        sd = None
        lo_g = hi_g = None
        if wdate is not None or vals is not None:
            fk_v = flat[v]
            lo_g = np.searchsorted(fk_v, gid, side="left")
            hi_g = np.searchsorted(fk_v, gid, side="right")
        if wdate is not None:
            d_v = wdate[st[v]]
            td = {
                g_id: d_v[a:b].tolist()
                for g_id, a, b in zip(gid.tolist(), lo_g.tolist(),
                                      hi_g.tolist())
                if b > a
            }
            if lens is not None:
                # Streak spans + day counts, parallel to td (same
                # valid-cell subset): per kept mid cell, the qualifying
                # run's start / end grid rows around it (mid - (L-1)//2
                # .. mid + L//2 — runs are clipped at the window slice,
                # so both land inside) mapped to the shared calendar
                # dates, and the run's trading-day count L.
                Lv = lens[v]
                starts_all = wdate[st[v] - ((Lv - 1) // 2)]
                ends_all = wdate[st[v] + (Lv // 2)]
                ss = {}
                se = {}
                sd = {}
                for g_id, a3, b3 in zip(gid.tolist(), lo_g.tolist(),
                                        hi_g.tolist()):
                    if b3 > a3:
                        ss[g_id] = starts_all[a3:b3].tolist()
                        se[g_id] = ends_all[a3:b3].tolist()
                        sd[g_id] = Lv[a3:b3].tolist()
        # Ragged trigger excesses over the SAME valid-cell subset (the
        # group bounds above are computed whenever either parallel list
        # is requested): per group id, the contiguous [lo, hi) slice of
        # the valid cells' excess values (value − qualifying bar).
        te = None
        if vals is not None:
            w_v = vals[v]
            te = {
                g_id: w_v[a:b].tolist()
                for g_id, a, b in zip(gid.tolist(), lo_g.tolist(),
                                      hi_g.tolist())
                if b > a
            }
        if n in MM_HORIZONS:
            hi_e = np.full(CP, -np.inf)
            lo_e = np.full(CP, np.inf)
            # Groups with no valid cell keep the ±inf default (the
            # legacy where(..., ±inf).max semantics).
            hi_e[gid] = np.maximum.reduceat(np.where(v, g, -np.inf), starts)
            lo_e[gid] = np.minimum.reduceat(np.where(v, g, np.inf), starts)
            out[n] = (cnt.reshape(C, P), s.reshape(C, P), s2.reshape(C, P),
                      hi_e.reshape(C, P), lo_e.reshape(C, P),
                      rev.reshape(C, P), td, ss, se, sd, te)
        else:
            out[n] = (cnt.reshape(C, P), s.reshape(C, P), s2.reshape(C, P),
                      None, None,
                      rev.reshape(C, P), td, ss, se, sd, te)
    return out
