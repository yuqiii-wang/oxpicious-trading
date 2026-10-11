"""The ohlcv-boost score — the GENERAL formula
(analysis.analysis_signals.engines.ohlcv_boost._score).

Family-agnostic: any engine scores ITS trigger anchors by passing the
anchor frame + its bucket-identity columns + its per-side configs
(the ladders live in _configs, keyed family → sec_type → side). One
anchor = one row with

    ids                       the engine's bucket-identity columns
    side          int16       the stats.label_dim 'side' code
    excess        float64     the value's depth past the bucket's OWN
                              bar (value - bar; side-signed by the
                              bucket's bar convention)
    pattern       int8        the day-0 OHLC pattern code (_axes;
                              AXIS_MISSING when the store has no row)
    vol_state     int8        the day-0 amount-level state code
    px_speed      int8        the day-0 price-speed state code

and gains three columns per anchor row:

    boost_score   float64  score_bp (see _config — the fused
                           measured-edge trust score)
    boost_weight  float64  w = max(1 + score/K, floor) — the trust
                           weight (the general formula's output; the
                           flag consumers use only score/flag today)
    boost_flag    int64    1 where score >= the side's bar_bp (0 when
                           the side carries no bar) — the history-row
                           verdict (ONE anchor IS one trigger streak's
                           first day)

bucket_flags() aggregates the anchors to any group level (the pct
buckets for signal_thresholds, the (code, window, side, regime) curve
cells for signal_curves): TRUE where the group's anchors' MEAN score
clears the side's bar on >= min_anchors anchors. The flag is an
analysis marker — it gates nothing (detection, bars, confidence and
is_active are computed exactly as without it).

COMPUTE LAYOUT (cudf-dtypes contract): whole-frame cudf.pandas only —
boolean slice / assign / elementwise where-mask / groupby transform
for the within-bucket depth z — no host numpy, no strings, no nullable
dtypes (missing axes arrive as the int8 AXIS_MISSING code; a missing
edge maps to 0.0 NEUTRAL, never NaN). The per-side loop is over at
most the two configured entries (the retired forecast module's
convention). Vol-cell fallback: an unknown cell key falls to the
MARGINAL vol_state edge (a missing px_speed lands there too — the
arithmetic key vs*100+ps never collides for ps in 0..4), a missing
vol_state to NEUTRAL.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.labels import SIDE_CODE

from analysis.analysis_signals.engines.ohlcv_boost._config import (
    OhlcvBoostConfig,
)

# The vol CELL ladder's arithmetic frame key: vol_state * 100 + px_speed
# (int codes; the cell dict is flattened to the same key). ps <= 4 and
# vs >= 0 keep the encoding injective; AXIS_MISSING (-1) keys can never
# collide with a measured cell.
_CELL_KEY_SCALE = 100


def anchor_scores(
    anchors: pd.DataFrame,
    ids: list[str],
    cfgs: dict[int, OhlcvBoostConfig],
    excess_col: str = "excess",
) -> pd.DataFrame:
    """Score every anchor row through its side's config (see the module
    docstring). ``excess_col`` names the anchor frame's depth column
    (the value-past-the-bar excess; mov_rsi's cells carry it renamed
    as ``trig_excess``). Sides without a config (or an empty
    ``cfgs``) keep score 0 / weight 1 / flag 0 — the unboosted
    shape."""
    score_col = pd.Series(np.nan, index=anchors.index, dtype="float64")
    weight_col = pd.Series(np.nan, index=anchors.index, dtype="float64")
    flag_col = pd.Series(np.nan, index=anchors.index, dtype="float64")
    bottom = np.int16(SIDE_CODE["bottom"])
    for side_code, cfg in sorted(cfgs.items()):
        sl = anchors[anchors["side"] == np.int16(side_code)]
        if sl.empty:
            continue

        # the side-aligned tail depth, z-scored WITHIN the bucket
        # identity (the cells' own excess scale varies per bucket) and
        # capped — the retired formula's depth axis, generalized.
        d = sl[excess_col] * np.where(sl["side"] == bottom, -1.0, 1.0)
        g = d.groupby([sl[col] for col in ids], sort=False)
        d_mu = g.transform("mean")
        d_sd = g.transform("std")
        z = ((d - d_mu) / d_sd.where(d_sd > 1e-9)) \
            .fillna(0.0).clip(-cfg.depth_z_cap, cfg.depth_z_cap)

        # e_ohlc: the pattern through the ladder (missing/unknown code
        # → NEUTRAL 0). e_vol: the (vol_state, px_speed) CELL ladder
        # first, the MARGINAL state ladder for cells without a measured
        # entry, NEUTRAL 0 when the registry row itself is missing.
        e_o = sl["pattern"].map(
            {int(c): float(e) for c, e in cfg.pattern_edge_bp.items()},
        ).fillna(0.0).astype("float64")
        cell_key = sl["vol_state"].astype("int64") * _CELL_KEY_SCALE \
            + sl["px_speed"].astype("int64")
        e_v = cell_key.map({
            int(vs) * _CELL_KEY_SCALE + int(ps): float(e)
            for (vs, ps), e in cfg.vol_cell_edge_bp.items()
        })
        e_v = e_v.fillna(sl["vol_state"].map(
            {int(vs): float(e) for vs, e in cfg.vol_state_edge_bp.items()},
        )).fillna(0.0).astype("float64")
        e_r = z * cfg.depth_bp_per_z

        score = (
            cfg.beta_ohlc * e_o
            + cfg.beta_vol * e_v
            + cfg.beta_rsi * e_r
            + cfg.gamma_ohlc_vol * e_o * e_v / 100.0
        )
        weight = np.maximum(
            1.0 + score / cfg.score_bp_per_weight, cfg.weight_floor,
        )
        if cfg.bar_bp is None:
            flag = pd.Series(0.0, index=sl.index, dtype="float64")
        else:
            flag = (score >= cfg.bar_bp).astype("float64")

        # index-aligned assembly: each side fills only its own rows —
        # `add(fill_value=0)` on the all-NaN accumulators (weight > 0.5,
        # flag in {0, 1}, so the 0-fill never collides with a real
        # value); unconfigured sides stay NaN until the final fillna
        # (elementwise binary ops — cudf-native, no host boundary).
        score_col = score_col.add(score, fill_value=0.0)
        weight_col = weight_col.add(weight, fill_value=0.0)
        flag_col = flag_col.add(flag, fill_value=0.0)

    return anchors.assign(
        boost_score=score_col.fillna(0.0).astype("float64"),
        boost_weight=weight_col.fillna(1.0).astype("float64"),
        boost_flag=flag_col.fillna(0.0).astype("int64"),
    )


def bucket_flags(
    scored: pd.DataFrame,
    ids: list[str],
    cfgs: dict[int, OhlcvBoostConfig],
    min_anchors: int = 1,
) -> pd.DataFrame:
    """The group-level verdict frame (``ids`` + ``is_ohlcv_boosted``
    int64 0/1): TRUE where the group's anchors' MEAN boost_score
    clears the side's bar on >= min_anchors anchors. Groups whose side
    carries no config (or bar None) never flag — a NaN bar comparison
    is False, so no per-group side branching is needed."""
    if scored.empty:
        empty = {col: pd.Series(dtype="float64") for col in ids}
        empty["is_ohlcv_boosted"] = pd.Series(dtype="int64")
        return pd.DataFrame(empty)
    g = scored.groupby(ids, sort=False).agg(
        _mean=("boost_score", "mean"),
        _n=("boost_score", "size"),
        _side=("side", "first"),
    ).reset_index()
    # sides with no config (or bar None) map to NaN — a NaN bar never
    # flags (notna gate below), no per-group side branching needed
    bar = g["_side"].map({
        int(code): cfg.bar_bp for code, cfg in cfgs.items()
        if cfg.bar_bp is not None
    })
    flag = (
        bar.notna()
        & (g["_mean"] >= bar)
        & (g["_n"].astype("float64") >= float(min_anchors))
    ).fillna(False).astype("int64")
    return g[ids].assign(is_ohlcv_boosted=flag)


__all__ = ["anchor_scores", "bucket_flags"]
