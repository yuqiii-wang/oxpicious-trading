"""In-engine Bollinger σ-depth value computation
(analysis.analysis_signals.engines.signal_families.boll_std._compute).

The bollinger family computes its OWN values since 2026-10-10 (the
analysis.mov_ave_boll value-source table was dropped): one
`add_boll_value_columns` pass over the fetched price frame adds, per
window W ∈ BOLL_WINDOWS, the exact columns the family's tiers read —

  boll_z_{W}days      z = (price − SMA_W) / σ_W — the classic trailing
                      construction (population σ ddof=0, min_periods=W;
                      NULL on degenerate σ below BOLL_SIGMA_FLOOR), the
                      value the fixed ±kσ bars threshold.
  is_typical_{W}days  the SIDE-MIRRORED tail-episode pass flags at the
                      FIXED BOLL_TYPICAL_BAND_S (0.6745σ — the Gaussian
                      25% tail) band: 1.0 the typical pass, 0.0 the
                      re-entry pass, NaN out-of-band (the float8 the old
                      SQL casts produced — the history rows'
                      population attribute rides it).
  ep_size_{W}days     the episode's trading-day count on EVERY in-band
                      day (NULL out-of-band) — the population marker
                      (non-NULL = in-band) + the curve tier's 1/size
                      episode weight.

The episode segmentation reuses the rsi analysis layer's mask-driven
classifier (mov_ave_spread steps/rsi_typical._classify_side) verbatim —
the SAME side-mirrored semantics the mov_ave_rsi flags segment on
(K=5 re-entry exit, ISOLATED=20 pass split, gap-1 continuation
inheritance). The classifier groups by (sec_type, code, side), so the
pass attaches the run's sec_type as its int16 label code for the call
and drops it after (the frame-only label-passenger convention).

COMPUTE LAYOUT: whole-frame vectorized cudf.pandas — grouped rolling
mean/std via the shared helper, elementwise z, mask-driven episode
classification; no per-code loops, no host boundary.

The frame MUST carry each code's FULL history (the fetch imposes no
lower date bound): the trailing windows need W prior rows for the first
target date, and the episode gaps/episode sizes read preceding band
days arbitrarily far back. z is trailing, so a day's band membership
never shifts when new data arrives — computed once per run, cleanly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.df_utils import grouped_rolling_agg
from _common.labels import SEC_TYPE_CODE

from analysis.analysis_signals.config import (
    BOLL_SIGMA_FLOOR,
    BOLL_TYPICAL_BAND_S,
    BOLL_WINDOWS,
)
from analysis.mov_ave_spread.steps.rsi_typical import _classify_side


def add_boll_value_columns(
    df: pd.DataFrame,
    sec_type: str,
    boll_windows: tuple = BOLL_WINDOWS,
) -> pd.DataFrame:
    """Add the boll_z_{W}days + is_typical_{W}days + ep_size_{W}days
    columns per window, computed from the frame's own ``price`` column
    per code over its full history. Returns df with the columns added
    (empty frames pass through unchanged — the engine's typed empty
    path carries the vocabulary separately)."""
    if df.empty:
        return df

    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    # the classifier's group key: the run's sec_type as its int16 label
    # code (frame-only passenger — attached for the call, dropped after)
    df["sec_type"] = np.int16(SEC_TYPE_CODE[sec_type])
    grp = ["sec_type", "code"]
    # the code's own trading-day position (the episode gap unit —
    # calendar gaps are not rows, the rsi_typical convention)
    df["_p"] = df.groupby(grp, sort=False).cumcount()

    for w in boll_windows:
        sma = grouped_rolling_agg(
            df, grp, "price", w, min_periods=w, agg="mean", sort=False,
        )
        sigma = grouped_rolling_agg(
            df, grp, "price", w, min_periods=w, agg="std", ddof=0,
            sort=False,
        )
        z = (df["price"] - sma) / sigma
        # cudf-safe finite test (np.isfinite has no cuDF fast path —
        # NaN fails the self-equality, NULLs fill False; the _finite_mask
        # idiom the signals tier uses)
        finite = (
            (z == z) & (z != float("inf")) & (z != float("-inf"))
        ).fillna(False)
        ok = (
            sigma.notna() & (sigma.fillna(0.0) > BOLL_SIGMA_FLOOR)
            & sma.notna() & df["price"].notna() & finite
        )
        zcol = f"boll_z_{w}days"
        df[zcol] = z.where(ok)

        # the episode layer at the FIXED σ band: top = the upper-band
        # tail (z >= +edge, the sell side), bottom = the lower-band tail
        # (z <= -edge, the buy side) — the shared SIDE-MIRRORED
        # classifier (rsi_typical semantics: K=5 re-entry exit,
        # ISOLATED=20 pass split, gap-1 continuation inheritance).
        # z.fillna(0.0) BEFORE comparing (the cudf rule — comparisons on
        # null-bearing columns yield <NA> and poison the bool masks;
        # 0.0 sits inside the band, never a mask hit).
        zf = df[zcol].fillna(0.0)
        in_top = zf >= BOLL_TYPICAL_BAND_S
        in_bot = zf <= -BOLL_TYPICAL_BAND_S
        flag_t, size_t = _classify_side(df, in_top, 0)
        flag_b, size_b = _classify_side(df, in_bot, 1)
        in_band = in_top | in_bot
        df[f"is_typical_{w}days"] = flag_t.where(
            in_top, flag_b,
        ).where(in_band, np.nan)
        df[f"ep_size_{w}days"] = size_t.where(
            in_top, size_b,
        ).where(in_band, np.nan)

    return df.drop(columns=["_p", "sec_type"])
