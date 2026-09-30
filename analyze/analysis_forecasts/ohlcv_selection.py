"""Ohlcv score selection — the SCORE-GATED related-subset population
(analyze.analysis_forecasts.ohlcv_selection).

ONE isolated module owns the ``ohlcv_type = 'ohlcv_selection'``
semantics. It is the SELECTION sibling of the ohlcv_boost module — the
2026-09-27 consolidation that RETIRED the four legacy derived
populations (ohlc_boost / ohlc_selection / vol_boost / vol_selection):
their day-0 anatomy axes now live only inside the OHLCV SCORE, and the
derived children are the ohlcv pair:

  - ``ohlcv_boost``   (ohlcv_boost.py) — the parent's FULL trigger
    population, each streak re-weighted by its day-0 ohlcv score.
  - ``ohlcv_selection`` (THIS module) — a STRICT SUBSET: the streaks
    whose day-0 ohlcv score clears the side's selection bar. Fewer
    trigger rows than the parent, all of them carrying a non-negative
    expected-edge reading.

The score source is SINGLE: the ladders and the trust weights β / γ /
K live in ohlcv_boost.OHLCV_BOOST_CONFIGS — SIDE-KEYED (bucket ->
sec_type -> side; see that module for the measured reason the sides
are separate configs). This module re-derives nothing: it recomputes
the same day-0 score (the shared ``_day0_edges`` helper — drift here
would be a correctness bug) and applies the side's own bar.

Rule (mov_rsi BOTTOM side, index + etf sec_types):

    day-0 ohlcv score >= OHLCV_BOOST_CONFIGS[..][..][side]
                        .selection_bar_bp   (0.0 where shipped)

The bar cuts the unfavorable-anatomy half (shrink/flat amount cells,
edge patterns, shallow tails score negative) and keeps the favorable
half, so every selection bucket reads as a favorable-anatomy
population — its sign-aligned forward edge must be POSITIVE, and the
study gate is: a selection population that fails to clear zero means
the bar (or a ladder) is wrong — re-measure and fix, never ship a
negative selection. The TOP side carries NO selection child
(``selection_bar_bp = None`` — its score-positive subset was not
gated and stays off).

The condition is evaluated at the STREAK'S DAY-0 anchor (the moment
the signal becomes observable): one verdict per streak, its whole
anchor ladder inherits it. Bucket-independent, so it is stamped per
(bucket identity, streak) and broadcasts identically to every bucket
that owns the streak.

All evaluation is vectorized (native cudf ops — keyed merges, map /
np arithmetic, no row loops, no nullable-boolean kernels; the
per-side loop is over at most the two configured entries) and stays
in the repo's cudf.pandas frame contract: the flag rides frames as
INT64 0/1 and renders as '0'/'1' at the writer's COPY boundary.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analyze.analysis_forecasts.ohlcv_boost import (
    _day0_edges,
    enabled,
    side_configs,
    window_columns,
)


def apply(
    bucket: str,
    sec_type: str,
    cells: pd.DataFrame,
    win: pd.DataFrame,
    ids: list[str],
) -> pd.DataFrame:
    """Stamp the streak-merged anchor rows with ``is_ohlcv_selection``.

    The selection reads the streak's DAY-0 anchor (delay == 0): the
    side's ohlcv score (the same one ohlcv_boost computes — the shared
    helper) maps the anchor's stored pattern label + (vol_state,
    px_speed) cell + RSI tail depth through the shared ladders, and
    the streak qualifies when the score clears the side's
    ``selection_bar_bp``. Sides without a config — or with the bar
    ``None`` (no gated selection child) — never qualify. The WHOLE
    anchor ladder (delay 1..5 shares the streak's ``(ids, _t0)`` key)
    inherits the flag. Families / sec_types / sides without an
    applicable bar get an all-0 column (the historical
    single-population shape).

    All steps run on device frames in INCREASING host dependence
    (per side, at most two entries):

      step 1  cudf  streak day-0 ordinal        (_t0 = _t - delay)
      step 2  cudf  anchor features (window join) + depth z (groupby)
      step 3  cudf  stored labels → aligned edges (missing → neutral 0)
      step 4  cudf  score >= bar → INT64 verdict
      step 5  cudf  broadcast the flag to the anchor ladder
    """
    cfgs = {
        side: cfg for side, cfg in side_configs(bucket, sec_type).items()
        if cfg.selection_bar_bp is not None
    }
    if not cfgs:
        cells["is_ohlcv_selection"] = np.int64(0)
        return cells

    # ---- step 1 (cudf): the streak's day-0 ordinal -------------------
    a = cells.assign(_t0=cells["_t"] - cells["delay"])
    scored: list[pd.DataFrame] = []
    for side, cfg in sorted(cfgs.items()):
        day0 = a[(a["delay"] == 0) & (a["side"] == side)]
        day0 = day0[ids + ["_t0", "excess"]].drop_duplicates()
        if day0.empty:
            continue

        # ---- steps 2-3 (cudf): features + the side's axis edges -------
        day0 = _day0_edges(day0, win, ids, cfg)

        # ---- step 4 (cudf): score >= bar → INT64 verdict ---------------
        score = (cfg.beta_ohlc * day0["_eo"] + cfg.beta_vol * day0["_ev"]
                 + cfg.beta_rsi * day0["_er"]
                 + cfg.gamma_ohlc_vol * day0["_eo"] * day0["_ev"] / 100.0)
        day0["_sel"] = (score >= cfg.selection_bar_bp) \
            .fillna(False).astype("int64")
        scored.append(day0[ids + ["_t0", "_sel"]])

    # ---- step 5 (cudf): broadcast to the whole anchor ladder ---------
    day0_all = (
        pd.concat(scored, ignore_index=True) if scored
        else pd.DataFrame(columns=ids + ["_t0", "_sel"]))
    out = a.merge(day0_all, on=ids + ["_t0"], how="left")
    out["is_ohlcv_selection"] = out["_sel"].fillna(0).astype("int64")
    return out.drop(columns=["_sel", "_t0"])


__all__ = [
    "enabled",
    "window_columns",
    "apply",
]
