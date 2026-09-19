"""Shared wide-grid engine base (analyze.analysis_forecasts._engine).

``WideEngineBase`` — the ABC behind the forecast bucket engines
(compute_rsi / compute_std / compute_pairs /
compute_px_vol / compute_margin_ratio / compute_pe / compute_dividend).
One metric per compute module; the
module's ``compute_<family>_results`` factory builds its engine
subclass and returns ``engine.run()``.

The ABC owns the per-month machinery every engine previously carried
inline (the 2026-09-15 engine pass):

  - ``month_context`` — the MonthWindow prologue: empty-window skip,
    the DATE-space full-window live gate (first_ord < mw.lo_ord), the
    window-sliced NC0/FIN/path-extreme change matrices, the per-(code,
    horizon) reversal bars (reverse_thresholds(*window_sigmas(...))),
    the market-hype slice and the (C, 1) live2 broadcast view.
  - ``run`` — the shared month loop: per window → per MaskBatch (the
    subclass's stacked (T, C, K) bucket masks for one config group) →
    iter_bucket_subsets (streak-merge or one-day; the (side, hype)
    subsets as group-ascending sparse cell lists) →
    aggregate_horizons_sparse → build_result_rows (the 5 period rows
    next/5d/20d/60d/mixed) → yield (stat_month, rows) month-major so
    __main__ can write one atomic transaction per month.
  - ``quantile_threshold`` — the column-wise linear-interpolated
    quantile gather from a sorted window matrix (the mov_rsi percentile
    machinery; also imported by the signals layer).

Subclasses implement two hooks:

  - ``emit_batches(mc)`` — yield a MaskBatch per config group of the
    window slice: the raw per-day qualifying tests stacked (T, C, K)
    side-major, the optional signed TRIGGER EXCESS tensor (value −
    qualifying bar), optional uneven per-side config-axis ranges, and
    the engine-private carry payload (e.g. the family window).
  - ``base_rows(mc, carry, side, hyped, kk, ii, mean_streak)`` — the
    motivation fields + config JSONB of one emitted bucket, as a
    COLUMNAR payload: a dict of (R,) lists (one entry per emitted
    bucket, aligned with ``kk``/``ii``) built with vectorized /
    comprehension passes — never a per-row dict loop (the metric files
    stay loop-free; the shared ``wide.build_result_rows`` does the
    per-bucket row plumbing once).

Families that don't fit the mask pipeline (compute_base — no buckets;
compute_high_low_streaks — ex-post anchors; compute_opp_pair —
industry space) reuse ``month_context`` directly and keep their
bespoke loops.

Device path: the engines are numpy-exact end to end. The GPU work
happens BELOW them — the fetch layer's grouped pandas ops and the
(T, C) wide matrices are real host ndarrays
(wide.grid.scatter_column unwraps the cudf.pandas proxy once at the
pandas→numpy boundary), so the engine math never pays a
GPU↔CPU transfer or a cudf fallback.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import (ClassVar, Generic, Iterable, Iterator, NamedTuple,
                    TypeVar)

import numpy as np

from analyze.analysis_forecasts.config import FORWARD_HORIZONS, MM_HORIZONS
from analyze.analysis_forecasts.wide import (
    HorizonAgg,
    MonthWindow,
    aggregate_horizons_sparse,
    build_result_rows,
    iter_bucket_subsets,
    reverse_thresholds,
    window_sigmas,
)

Carry = TypeVar("Carry")


@dataclass(frozen=True)
class MonthContext:
    """One stat month's shared window context (the engines' prologue).

    All matrices are the window slice [lo, hi) of the full (T, C) grid
    — Tw = hi - lo rows — except live / live2 / thr_n which are
    per-code (C,) arrays and win_ord the sliced day-ordinal grid.
    """
    mw: MonthWindow
    lo: int
    hi: int
    live: np.ndarray            # (C,) bool full-window gate
    live2: np.ndarray           # (C, 1) broadcast view of live
    HY: np.ndarray              # (Tw, C) bool market-hype cells
    NC0s: dict[int, np.ndarray]                       # (Tw, C) per horizon
    FINs: dict[int, np.ndarray]                       # (Tw, C) per horizon
    PATH0s: dict[int, tuple[np.ndarray, np.ndarray]]  # MM horizons only
    thr_n: dict[int, np.ndarray]                      # (C,) bars per horizon
    win_ord: np.ndarray | None  # (Tw,) int64 day ordinals


class MaskBatch(NamedTuple, Generic[Carry]):
    """One config group's stacked bucket masks of a month window.

    mask_raw    — (Tw, C, K) bool raw per-day qualifying tests (NaN
                  compares are False upstream, so invalid days never
                  enter a bucket); side-major K in SIDES order.
    excess3     — optional (Tw, C, K) signed TRIGGER EXCESS
                  (value − qualifying bar) aligned with mask_raw — the
                  scalar-bar engines' trigger_excess source; None for
                  the state families (NULL arrays).
    side_slices — optional {side: slice} uneven per-side config-axis
                  ranges (the state engines' layout); None → K splits
                  into len(SIDES) equal contiguous ranges.
    carry       — engine-private payload identifying the batch (e.g.
                  the family window the masks were built for).
    """
    mask_raw: np.ndarray
    excess3: np.ndarray | None
    side_slices: dict[str, slice] | None
    carry: Carry


class WideEngineBase(ABC, Generic[Carry]):
    """The (T, C) wide-grid bucket engine ABC (see module docstring)."""

    # Side names in config-axis order (the mask stack's side-major
    # halves). The state engines overriding side_slices must keep the
    # SAME side names/order iter_bucket_subsets walks.
    SIDES: ClassVar[tuple[str, ...]]

    # True (default) — STREAK-MERGE consecutive qualifying rows into
    # ONE signal at the run's mid row (wide.apply_streak_midpoints).
    # False — ONE-DAY signals (the pairs families: consecutive cross
    # days are mutually exclusive; margin_ratio: every qualifying day
    # is its own 1-day signal).
    MERGE: ClassVar[bool] = True

    def __init__(
        self,
        *,
        mats: dict[str, np.ndarray],
        chg: dict[str, np.ndarray],
        windows: list[MonthWindow],
        codes: list[str],
        sec_type: str,
        hype: np.ndarray,
        first_ord: np.ndarray,
        grid_ord: np.ndarray | None = None,
    ) -> None:
        self.mats = mats
        self.chg = chg
        self.windows = windows
        self.codes = codes
        self.C = len(codes)
        self.sec_type = sec_type
        self.hype = hype
        self.first_ord = first_ord
        self.grid_ord = grid_ord

    # ------------------------------------------------------------------
    #  Shared month machinery
    # ------------------------------------------------------------------

    def month_context(self, mw: MonthWindow) -> MonthContext | None:
        """The shared month prologue — None when the window has no grid
        rows or no code passes the full-window gate."""
        lo, hi = mw.lo, mw.hi
        if lo >= hi:
            return None  # no grid rows in this window at all
        # Full-window gate in DATE space (absolute ordinals): a code is
        # live only once its own history strictly precedes the window
        # start (first data month + 60 months = first snapshot).
        # Row-space lo would clamp to 0 when the grid begins after the
        # nominal window start and wrongly pass grid-start codes.
        live = self.first_ord < mw.lo_ord
        if not live.any():
            return None
        NC0s = {n: self.chg[f"NC0_{n}"][lo:hi] for n in FORWARD_HORIZONS}
        FINs = {n: self.chg[f"FIN_{n}"][lo:hi] for n in FORWARD_HORIZONS}
        # Window-sliced PATH-extreme matrices (FMAX0/FMIN0) — the
        # swing-aware reversal event inputs.
        PATH0s = {
            n: (self.chg[f"FMAX0_{n}"][lo:hi], self.chg[f"FMIN0_{n}"][lo:hi])
            for n in MM_HORIZONS
        }
        # Per-(code, horizon) reversal bar for this window (the fixed
        # 0.01 bar in "fixed" mode; adaptive k·σ in "std" mode).
        thr_n = reverse_thresholds(*window_sigmas(NC0s, FINs))
        return MonthContext(
            mw=mw,
            lo=lo,
            hi=hi,
            live=live,
            live2=live[:, None],
            HY=self.hype[lo:hi],
            NC0s=NC0s,
            FINs=FINs,
            PATH0s=PATH0s,
            thr_n=thr_n,
            win_ord=None if self.grid_ord is None else self.grid_ord[lo:hi],
        )

    def run(self) -> Iterator[tuple[date, list[dict]]]:
        """Yield (stat_month, bucket rows) per stat month — the shared
        detect → aggregate → expand month loop. The rows are the
        (5·R,) period payloads of build_result_rows (bucket-major, the
        four horizons plus the blended 'mixed' row per bucket)."""
        for mw in self.windows:
            mc = self.month_context(mw)
            if mc is None:
                continue
            rows: list[dict] = []
            for batch in self.emit_batches(mc):
                for (side, hyped, kk, ii, st, sc, fk, L_int, exc,
                     mean_streak) in iter_bucket_subsets(
                    batch.mask_raw, mc.HY, mc.live2, self.C, self.SIDES,
                    batch.side_slices,
                    merge=self.MERGE,
                    excess3=batch.excess3,
                ):
                    agg: dict[int, HorizonAgg] = aggregate_horizons_sparse(
                        st, sc, fk, self.C, self._side_width(batch, side),
                        side, mc.NC0s, mc.FINs, mc.thr_n,
                        path0s=mc.PATH0s,
                        win_ord=mc.win_ord,
                        lens=L_int,
                        vals=exc,
                    )
                    base = self.base_rows(
                        mc, batch.carry, side, hyped, kk, ii, mean_streak)
                    rows.extend(build_result_rows(agg, kk, ii, base, mc.thr_n))
            if rows:
                yield mw.stat_month, rows

    def _side_width(self, batch: MaskBatch, side: str) -> int:
        """The side's config-axis width P (the flat group id = code·P +
        config key space of aggregate_horizons_sparse — must match
        iter_bucket_subsets' slicing exactly)."""
        if batch.side_slices is not None:
            sl = batch.side_slices[side]
            return sl.stop - sl.start
        return batch.mask_raw.shape[2] // len(self.SIDES)

    # ------------------------------------------------------------------
    #  Subclass hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def emit_batches(self, mc: MonthContext) -> Iterable[MaskBatch]:
        """Yield the window's MaskBatch(es) — the family's stacked
        bucket masks per config group (see MaskBatch)."""

    @abstractmethod
    def base_rows(
        self,
        mc: MonthContext,
        carry: Carry,
        side: str,
        hyped: bool,
        kk: np.ndarray,
        ii: np.ndarray,
        mean_streak: np.ndarray,
    ) -> dict[str, list]:
        """The (R,)-long COLUMNAR motivation payload of one emitted
        subset — every bucket's shared fields (identity + family bucket
        keys + lookback + streak_signal_days + config JSONB), one list
        entry per bucket aligned with kk/ii. Built vectorized (the
        compute_rsi precedent); ``wide.build_result_rows`` fans the
        columns out across the period rows."""


def quantile_threshold(
    S: np.ndarray,
    valid_n: np.ndarray,
    col: np.ndarray,
    q: float,
) -> np.ndarray:
    """Column-wise linear-interpolated quantile gather from the sorted
    window matrix S (NaN-last). Columns with valid_n == 0 → NaN.

    τ = S[⌊pos⌋] + frac·(S[⌈pos⌉] − S[⌊pos⌋]) with pos = q·(valid_n−1)
    — the SEMANTIC CONTRACT of every percentile bar in the repo. The
    compute-side engines resolve the bars through the cudf.pandas
    quantile pass instead (the GPU column sort + interpolation — see
    compute_rsi; pandas-CPU fallback is automatic under cudf.pandas);
    this numpy form is the exactness reference — keep the signature.
    """
    n_safe = np.maximum(valid_n, 1)
    pos = q * (n_safe - 1)
    i0 = np.floor(pos).astype(np.int64)
    i1 = np.minimum(i0 + 1, n_safe - 1)
    frac = pos - i0
    thr = S[i0, col] + frac * (S[i1, col] - S[i0, col])
    return np.where(valid_n > 0, thr, np.nan)
