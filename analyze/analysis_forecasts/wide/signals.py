"""The unified bucket-signal pipeline (analyze.analysis_forecasts
.wide.signals).

Every mask-driven bucket engine (mov_rsi / mov_std /
mov_pairs / mov_pairs_ema / px_vol_state) funnels its stacked (T, C, K)
bucket masks through ``iter_bucket_subsets``: STREAK-MERGE consecutive
qualifying rows into ONE mid-anchored signal (or one-day signals for
the cross-event families), sparsify with a single np.nonzero, live-gate
per-config counts, split per side and per market-hype, and yield the
group-ascending sparse trigger-cell lists that
``horizons.aggregate_horizons_sparse`` reduces. One implementation of
the machinery the engines previously each carried inline (the 2026-09
streak migration duplicated it per engine).
"""
from __future__ import annotations

import numpy as np

# The signals layer's rolling-chain sentinel (never accepted: any real
# absolute grid row minus it exceeds any cooldown).
_NO_ACCEPT = -(2 ** 62)


def apply_cooldown_rolling(
    mask: np.ndarray,
    chain: np.ndarray,
    lo: int,
    cooldown_days: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Fixed-skip cooldown over one slice of the live-detection sweep,
    carrying the per-code state ACROSS calls (the rolling form the
    forecast engines' retired apply_cooldown took for the SIGNALS layer
    — analyze.analysis_signals: a live trigger cannot know an ongoing
    streak's mid ex-post, so day-level de-dup keeps the fixed-skip
    cooldown semantics).

    Greedy sequential over the time axis (an accepted day depends on the
    previous accepted day), vectorized across codes: row ``t`` accepts a
    trigger day whose previous accepted trigger is more than
    ``cooldown_days`` grid rows earlier. Triggers inside the skip window
    do NOT restart the cooldown — the first trigger after it is accepted
    (fixed-skip semantics).

    Args:
        mask: (Ts, C) bool raw qualifying tests of ONE slice whose first
              row sits at ABSOLUTE grid row ``lo`` (the union trading-day
              grid — a code suspended inside the skip window has slightly
              fewer of its own days skipped, negligible at 5 days).
        chain: (C,) int64 per-code rolling state — each code's last
              ACCEPTED trigger as an ABSOLUTE grid row
              (``_NO_ACCEPT`` before the first).
        lo: absolute grid row of ``mask``'s first row.
        cooldown_days: the fixed skip width (analysis_signals
              config.COOLDOWN_DAYS); ``0`` accepts every trigger.

    Returns:
        (accepted, chain) — accepted is the (Ts, C) bool of
        cooldown-ACCEPTED triggers; chain the updated per-code state to
        pass into the next call (it advances across month windows — a
        trigger late in month M-1 suppresses the early part of month M).
    """
    out = np.zeros_like(mask)
    last = chain
    for t in range(mask.shape[0]):
        cand = mask[t] & (lo + t - last > cooldown_days)
        out[t] = cand
        # np.where on the (C,) bool — cheap; the loop is T iterations of
        # a few vector ops (T ≈ 21 rows per month-end tail slice).
        last = np.where(cand, lo + t, last)
    return out, last


def apply_streak_midpoints(
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Collapse each run of CONSECUTIVE qualifying rows into ONE signal
    at the run's MID row — the streak-merge that replaced the legacy
    cooldown suppression in the forecast engines (mov_rsi / mov_std /
    px_vol_state; consumed via ``iter_bucket_subsets``, the
    engines' unified bucket-signal pipeline).

    A run of length L starting at row s keeps only row
    s + (L - 1) // 2 (0-based; the ((L-1)//2 + 1)-th day — the MEAN-MID
    anchor of the high_low_streaks convention: a 1-day run anchors
    itself, a 7-day run its 4th day, an 8-day run its 4th day). Dates
    that keep satisfying the forecast condition CONTINUOUSLY are treated
    as ONE forecast signal, measured from the mid day.

    Operates per column (the flattened (T, C·K) config stack — columns
    are config-independent). Runs are clipped at the window-slice edges:
    a streak straddling the window start anchors from its visible part
    ("for now" — the month-window slices are independent worlds).

    Returns (mid_mask, run_len): ``mid_mask`` is True only at the kept
    mid rows; ``run_len`` (int32) carries the run's day count at those
    rows (0 elsewhere). The per-bucket MEAN of the kept cells' lengths
    is written to forecast_identities.streak_signal_days by the caller.
    """
    T, M = mask.shape
    # Zero-padded shifted views isolate run STARTS / ENDS in one
    # broadcast compare (a run touching the slice edge is still bounded
    # by the False pad).
    padded = np.zeros((T + 2, M), dtype=bool)
    padded[1:-1] = mask
    core = padded[1:-1]
    starts = core & ~padded[:-2]
    ends = core & ~padded[2:]
    rs, ms = np.nonzero(starts)
    re, me = np.nonzero(ends)
    # np.nonzero emits ROW-major order across the whole matrix, so the
    # k-th start is NOT the k-th end globally — pair them PER COLUMN
    # (within a column starts and ends strictly alternate, start first,
    # so the column-major-sorted k-th start pairs with the k-th end of
    # the same column).
    rs, ms = rs[np.lexsort((rs, ms))], ms[np.lexsort((rs, ms))]
    re = re[np.lexsort((re, me))]
    L = (re - rs + 1).astype(np.int32)
    mid_rows = rs + ((L - 1) // 2).astype(np.int64)
    mid = np.zeros((T, M), dtype=bool)
    lens = np.zeros((T, M), dtype=np.int32)
    mid[mid_rows, ms] = True
    lens[mid_rows, ms] = L
    return mid, lens


def iter_bucket_subsets(
    mask_raw: np.ndarray,
    HY: np.ndarray,
    live2: np.ndarray,
    C: int,
    sides: tuple[str, ...],
    side_slices: dict[str, slice] | None = None,
    *,
    merge: bool = True,
    excess3: np.ndarray | None = None,
):
    """The UNIFIED bucket-signal pipeline of the aggregation engines
    (compute_rsi / compute_std / compute_pairs /
    compute_px_vol): streak-merge → sparsify → live-gated per-config
    counts → per-side → per-hype split, yielded as the group-ascending
    sparse cell lists aggregate_horizons_sparse consumes.

    Args:
        mask_raw: (Tw, C, K_total) bool bucket mask of ONE month window
              — the raw per-day qualifying tests (percentile compares,
              band breaches, sign flips, state equality). NaN compares
              are False upstream, so invalid days never enter.
        HY: (Tw, C) bool market-hype matrix of the same window slice.
        live2: (C, 1) bool live gate (full-window gate × ones) — codes
              without full-window history never count / never emit.
        C: number of codes.
        sides: side names in config-axis order.
        side_slices: optional {side: slice} per-side ranges of the
              config axis (the state engines' uneven layout — px_vol).
              None → K_total is split into len(sides) EQUAL contiguous
              ranges (the mov_* engines' side-major halves).
        merge: True (default) — STREAK-MERGE (apply_streak_midpoints):
              consecutive qualifying grid rows collapse into ONE signal
              at the run's MID row, run lengths ride along for the
              bucket's streak_signal_days mean and the result rows'
              streak spans. False — ONE-DAY signals: every qualifying
              day is its own signal with run length 1 (the pairs
              engines: a cross day's predecessor sits on the other side
              of zero, so consecutive cross days are mutually exclusive
              and the merge pass would be a no-op).
        excess3: optional (T, C, K_total) signed per-day per-config
              TRIGGER EXCESS (value − qualifying bar) of the same
              window slice — the scalar-bar engines' companion tensor
              (mov_rsi: the day's indicator minus its
              percentile bar; mov_std: the price minus the breached
              band edge; mov_pairs: the day's spread, the bar being the
              zero line). Gathered at the kept cells like ``lens`` and
              yielded per subset so aggregate_horizons_sparse can slice
              it into the rows' forecast_results.trigger_excess lists.
              None (state families — no scalar qualifying bar) yields
              None excess, and the result rows write NULL arrays.

    Yields per non-empty (side, is_market_hyped) subset — a tuple
        (side, hyped, kk, ii, st, sc, fk, lens, exc, mean_streak)
      side / hyped — the subset keys;
      kk, ii — (R,) config / code indices of the EMITTED buckets
              (np.nonzero(emit.T) convention of build_result_rows);
      st, sc, fk — (E,) group-ASCENDING sorted sparse cells of the
              subset (window-local row, code, flat group = code·P +
              config — the aggregate_horizons_sparse contract);
      lens — (E,) int32 run length per cell (the merged streak's day
              count; all 1s in one-day mode);
      exc  — (E,) float trigger excess per cell (the excess3 gather at
              the same cells, subset- and sort-aligned like ``lens``);
              None when excess3 was None;
      mean_streak — (R,) the bucket's mean run length at the emitted
              (code, config) positions — the source of
              forecast_identities.streak_signal_days.
    """
    K_total = mask_raw.shape[2]
    if side_slices is None:
        width = K_total // len(sides)
        slices = {
            s: slice(i * width, (i + 1) * width)
            for i, s in enumerate(sides)
        }
    else:
        slices = side_slices

    if merge:
        T = mask_raw.shape[0]
        mask3, lens3 = apply_streak_midpoints(mask_raw.reshape(T, -1))
        nz_t, nz_c, nz_k = np.nonzero(mask3.reshape(mask_raw.shape))
    else:
        nz_t, nz_c, nz_k = np.nonzero(mask_raw)
    if nz_t.size == 0:
        return
    nz_t = nz_t.astype(np.int32)
    nz_c = nz_c.astype(np.int32)
    nz_k = nz_k.astype(np.int32)
    # Run length per kept cell (the merged streak's day count; every
    # qualifying day carries its own 1-day run in one-day mode).
    if merge:
        cell_lens = lens3.reshape(mask_raw.shape)[nz_t, nz_c, nz_k]
    else:
        cell_lens = np.ones(nz_t.size, dtype=np.int32)
    # Trigger excess per kept cell (value − qualifying bar; None for
    # the state families — no excess3 tensor was passed).
    cell_exc = (
        excess3[nz_t, nz_c, nz_k] if excess3 is not None else None
    )
    # Live-gated per-config STREAK counts (one per qualifying run —
    # not-yet-live codes never emit).
    count = np.bincount(
        nz_c * K_total + nz_k, minlength=C * K_total
    ).reshape(C, K_total) * live2
    if not (count > 0).any():
        return
    hy_cells = HY[nz_t, nz_c]

    for side in sides:
        sl = slices[side]
        P = sl.stop - sl.start
        cnt_s = count[:, sl]
        if not (cnt_s > 0).any():
            continue
        in_side = (nz_k >= sl.start) & (nz_k < sl.stop)
        if not in_side.any():
            continue
        t_s = nz_t[in_side]
        c_s = nz_c[in_side]
        flat_s = c_s * P + (nz_k[in_side] - sl.start)
        hy_s = hy_cells[in_side]
        len_s = cell_lens[in_side]
        exc_s = cell_exc[in_side] if cell_exc is not None else None

        # Hype split of the bucket (PK member). Aggregates are per
        # subset — max/high/low are non-additive, so the non-hyped
        # subset cannot be derived from the full bucket minus the
        # hyped one. Each subset is a cell-list filter.
        for hyped in (False, True):
            sel = hy_s if hyped else ~hy_s
            if not sel.any():
                continue
            st = t_s[sel]
            sc = c_s[sel]
            fk = flat_s[sel]
            L_int = len_s[sel]
            exc = exc_s[sel] if exc_s is not None else None
            # Group-ascending cell order — one stable sort shared by
            # the subset count and every horizon's bincount / reduceat
            # reductions.
            order = np.argsort(fk, kind="stable")
            st = st[order]
            sc = sc[order]
            fk = fk[order]
            L_int = L_int[order]
            exc = exc[order] if exc is not None else None
            emit = (
                np.bincount(fk, minlength=C * P).reshape(C, P) > 0
            ) & (cnt_s > 0)
            if not emit.any():
                continue
            # streak_signal_days source: the bucket's MEAN run length
            # over its (hype-split) streak signals.
            L_sel = L_int.astype(np.float64)
            mean_streak = np.divide(
                np.bincount(fk, weights=L_sel, minlength=C * P),
                np.maximum(np.bincount(fk, minlength=C * P), 1),
            ).reshape(C, P)
            kk, ii = np.nonzero(emit.T)
            yield (side, hyped, kk, ii, st, sc, fk, L_int, exc,
                   mean_streak[ii, kk])
