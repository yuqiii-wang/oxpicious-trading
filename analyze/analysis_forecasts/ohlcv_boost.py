"""Ohlcv score boost — the trust-weighted measurement population
(analyze.analysis_forecasts.ohlcv_boost).

ONE isolated module owns the ``ohlcv_type = 'ohlcv_boost'`` semantics;
its logic never reads (nor is read by) its SELECTION sibling
(analyze.analysis_forecasts.ohlcv_selection — the score-positive
strict subset of the same population). The 2026-09-27 OHLCV
CONSOLIDATION: the four legacy derived populations (ohlc_boost /
ohlc_selection / vol_boost / vol_selection) were RETIRED — their day-0
axes (the OHLC pattern, the amount state, the gap anatomy) live only
inside the ohlcv score now, and the derived children are the ohlcv
pair:

  - The PARENT forecast keeps its FULL original trigger population.
  - The FUSED_BOOST child carries the SAME trigger days as the parent
    (the boost convention — only the selection modules filter a
    population), with each streak's contribution to the forecast stats
    WEIGHTED by its day-0 OHLCV SCORE. Its forecast_identities
    registry row carries ``ohlcv_type = 'ohlcv_boost'``.

The score (every axis expressed in the SAME unit, the sign-aligned 5d
forward bp excess vs the average streak, so the fuse is a
trust-weighted sum of expected edges):

    score_bp = β_ohlc · ê_ohlc(pattern)           the OHLC pattern ladder
             + β_vol  · ê_vol(vol_state, speed)   the vol×amt CELL ladder
             + β_rsi  · ê_rsi(depth)              the RSI tail depth past
                                                  the bucket's own bar
             + γ      · ê_ohlc · ê_vol / 100      the cross-axis
                                                  interaction (bp)
    w        = max(1 + score_bp / K, floor)

THE SIDES ARE SEPARATE CONFIGS (bucket -> sec_type -> side), because
the 2026-09-28 step-5/6 calibration (temp_scripts/study_fused_5/6,
outputs in temp_scripts/output/fused_score/fused_5_top_vs_bottom/ +
fused_6_top_pattern/) measured the axes' persistence per side and it
DIFFERS structurally:

  - bottom: the three-axis + cross-term form. The base is positive
    7/7 years, every axis' per-year slope persists (depth 7/7, vol
    6/7 positive), and the thick vol cells carry the positive edges.
    β/γ/K the 2026-09-27 bounded fit (code- and month-cluster
    bootstrap CIs exclude 0); the config cites fused_fit.json.
  - top: PATTERN-ONLY (β_vol = β_rsi = γ = 0). The step-5 diagnosis
    found the three-axis form unshippable here — all β at the 0 bound,
    G2 47% / G3 2/6 — for three structural reasons the top config
    fixes: (1) the top BASE flips sign across epochs (4/7 years
    negative; 2024-26 all negative), so the score is measured
    RELATIVE to the base (improv), never as a level rescue; (2) the
    vol/depth axes' per-year slopes flip sign on top (vol negative
    2020-22 then +2.77 in 2024; depth -1.27 in 2020) while the PATTERN
    axis is positive 7/7 years — the one persistent axis, so the
    epoch-unstable axes are dropped entirely; (3) the thick top vol
    cells carry NEGATIVE edges (normal~flat 257k rows -31.9bp) while
    the positive edges live in heavy cells — a linear index over the
    vol ladder inverts; the pattern ladder labels 99.9% of anchors
    (no thin/thick inversion) and carries the study's strongest
    per-code robustness (frac_pos 0.915, t=27.3). The step-6
    calibration (fused_6_top_pattern.json): β = 1.0001 (code-cluster
    CI [0.94, 1.07]), K = 170 (score p99 at 1.5x), walk-forward G1 5/6
    years improv > 0 — including +127/+11/+8 bp in the three
    negative-base years 2024/25/26 — G2 64% of codes positive (bar
    60%); it beats the rejected 3-axis form in 5/6 years. The TOP
    side carries NO selection child (``selection_bar_bp = None`` —
    the score-positive subset was not gated and stays off).

  - ê_ohlc: the day-0 anchor's STORED pattern label
    (analysis.mov_ave_spreads_detail_ohlc_gaps) through this config's
    ladder; a missing label is NEUTRAL (0).
  - ê_vol: the day-0 (vol_state, px_speed) CELL edge — the study_vol_amt_edges 15-cell table (n >= CELL_MIN_N) — falling back
    to the MARGINAL vol_state edge (the study's B table), then to
    NEUTRAL when the px_vol registry row itself is missing. Bottom
    only (the top config's vol axis is off).
  - ê_rsi: the anchor's tail DEPTH past the bucket's own percentile
    bar — the cells' ``excess`` column (value − bar), side-aligned
    (bottom → −excess), z-scored WITHIN the bucket identity and capped
    at ±depth_z_cap — times depth_bp_per_z. Bottom only.

The weighted mean divides by Σw, so the absolute scale is
self-calibrating and the weight only fixes the RELATIVE boost of one
streak against another (the sibling modules' convention). Improv (the
UI column) = the weighted mean minus the plain mean.

All evaluation is vectorized (native cudf ops — keyed merges, map /
np arithmetic and groupby transforms, no row loops, no
nullable-boolean kernels; the per-side loop is over at most the two
config entries) and stays in the repo's cudf.pandas frame contract:
the flag rides frames as INT64 0/1, the weight as float64, and
neither reaches the writer (they collapse into the registry's
ohlcv_type verdict via the engine's fan-out, while the weight only
shapes the ohlcv span's weighted aggregation in _dfengine).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class OhlcvBoostConfig:
    """One (family × sec_type × SIDE)'s ohlcv-score weighting: each
    day-0 anchor's axes (stored OHLC pattern label; the px_vol
    registry's (vol_state, px_speed) cell; the RSI tail depth past the
    bucket's own percentile bar) map through the config's
    measured-edge ladders to the trust-weighted score translated to a
    weight ``w = max(1 + score/K, floor)`` that rides its whole anchor
    ladder into the engine's weighted aggregation. The ohlcv child
    keeps the parent's FULL trigger population (same days; only the
    selection modules filter). An axis with an empty ladder / zero β
    is OFF for the config (the top side ships pattern-only).
    ``vol_cell_edge_bp`` entries carry the CELL edges measured at
    n >= CELL_MIN_N; cells without an entry fall back to the
    ``vol_state_edge_bp`` MARGINAL edge; a missing registry row is
    neutral. ``selection_bar_bp`` is the SELECTION sibling's score bar
    for the side (None = the side carries no selection child — the
    top side's score-positive subset was never gated)."""

    pattern_edge_bp: dict[str, float] = field(default_factory=dict)
    vol_state_edge_bp: dict[str, float] = field(default_factory=dict)
    vol_cell_edge_bp: dict[tuple[str, str], float] = field(
        default_factory=dict)
    beta_ohlc: float = 1.0
    beta_vol: float = 0.0
    beta_rsi: float = 0.0
    gamma_ohlc_vol: float = 0.0
    depth_bp_per_z: float = 0.0
    depth_z_cap: float = 2.0
    score_bp_per_weight: float = 150.0
    weight_floor: float = 0.5
    min_anchors: int = 1
    selection_bar_bp: float | None = 0.0


# The evidence floor a 15-cell entry needs to enter the config (the
# study's own MIN_CELL_N=30 reports thin cells; the seed keeps the
# cells with at least ~500 anchors and lets thinner ones fall back to
# the marginal state edge).
CELL_MIN_N = 500

# The config table — THE single place the ohlcv-score semantics live.
# Keyed (family, sec_type, SIDE): each side its own ladders, trust
# weights and selection bar (see the module docstring for the measured
# reason the sides are separate configs). A family opts in by having
# an entry AND its engine naming ohlcv_boost_bucket; families /
# sec_types / sides without an entry keep the un-weighted single-
# population shape (no ohlcv child for them).
#
# Calibration provenance (temp_scripts/study_fused_* steps; outputs in
# temp_scripts/output/fused_score/ — fused_ladders.json, fused_fit.json,
# fused_2_coeffs.csv, fused_3_oof.csv, fused_4_percode.csv,
# fused_5_top_vs_bottom/, fused_6_top_pattern/):
#   - ladders: measured ON the delay-0 RSI-bottom anchor population vs
#     the side base (snapshots >= 2023-12-31) — the pattern ladder on
#     the ANCHOR population, where two of the 2026-09-27 all-days
#     guide's edges flip sign; the vol cells the 15-cell table.
#   - β / γ / δ / K: the bounded least-squares fit (β ∈ [0, 1.5],
#     γ ∈ [-1, 1]) with code- and month-cluster bootstrap CIs; δ the
#     measured depth slope; K the score p99 at 1.5x weight.
#   - index BOTTOM adopted 2026-09-27 after the FIRST rebuild
#     verification failed E1 with the hand-set seed (36% of buckets
#     improv>0 — the seed's all-days pattern ladder is wrong on the
#     anchor population); the calibrated set is the shipped rule.
#   - index TOP adopted 2026-09-28 as PATTERN-ONLY after the 3-axis
#     form failed its gates (step-5 diagnosis + step-6 calibration,
#     G1 5/6 + G2 64%; see the module docstring).
#   - etf was calibrated on the pre-truncation study (its walk-forward
#     gates passed 6/6) and rides the fitted values; no etf anchors
#     exist while the rebuild scope is index-only — recalibrate when
#     etf data returns. etf has no TOP entry (its top ladders were
#     never measured post-truncation).
OHLCV_BOOST_CONFIGS: dict[str, dict[str, dict[str, OhlcvBoostConfig]]] = {
    "mov_rsi": {
        "index": {
            # BOTTOM: the 3-axis + cross-term form — the 2026-09-27
            # calibrated set (fused_fit.json), unchanged.
            "bottom": OhlcvBoostConfig(
                pattern_edge_bp={
                    "spike_fade": 105.2,       # 冲高回落 (anchor-measured)
                    "gap_up_advance": 149.1,   # 高开高走
                    "gap_up_decline": -71.0,   # 高开低走 (FLIPS vs all-days)
                    "gap_down_decline": -15.7,  # 低开低走
                    "gap_down_advance": 104.1,  # 低开高走 (FLIPS vs all-days)
                    "flush_recover": 75.2,     # 探底回升
                    "neutral": -51.0,
                },
                vol_state_edge_bp={
                    "heavy": 261.0,   # 放量 +261bp vs parent (6/7 yrs)
                    "normal": 16.5,
                    "shrink": -47.9,  # 缩量 the drag leg
                },
                vol_cell_edge_bp={
                    ("heavy", "sharp_dn"): 324.4,   # the capitulation flush
                    ("normal", "sharp_up"): 175.9,
                    ("normal", "sharp_dn"): 154.0,
                    ("heavy", "flat"): 125.7,
                    ("normal", "slow_up"): 112.3,
                    ("shrink", "sharp_dn"): 87.4,
                    ("heavy", "slow_dn"): -23.6,
                    ("normal", "slow_dn"): -30.3,
                    ("normal", "flat"): -38.9,
                    ("shrink", "slow_dn"): -59.5,
                    ("shrink", "flat"): -62.2,
                },
                beta_ohlc=0.8715,            # fitted (bounded LS, cluster
                beta_vol=0.7524,             #   bootstrap CIs exclude 0
                beta_rsi=0.7463,             #   for all three)
                gamma_ohlc_vol=0.7817,       # measured super-additive
                depth_bp_per_z=68.5,         # measured slope (was 15 prior)
                score_bp_per_weight=270.0,   # fitted p99 @ 1.5x
            ),
            # TOP: PATTERN-ONLY — the 2026-09-28 step-6 calibration
            # (fused_6_top_pattern.json). The vol/depth axes are OFF
            # (empty ladders, zero β): their per-year slopes flip sign
            # on the top side (the step-5 diagnosis) and the thick top
            # vol cells carry negative edges. β the bounded fit
            # (code-cluster CI excludes 0), K the score p99 at 1.5x.
            # No selection child (not gated).
            "top": OhlcvBoostConfig(
                pattern_edge_bp={           # the anchor-measured TOP ladder
                    "spike_fade": -9.4,        # 冲高回落
                    "gap_up_advance": -55.9,   # 高开高走
                    "gap_up_decline": 41.5,    # 高开低走
                    "gap_down_decline": -13.7,  # 低开低走
                    "gap_down_advance": -5.7,  # 低开高走
                    "flush_recover": 254.4,    # 探底回升 the one big edge
                    "neutral": -25.7,
                },
                beta_ohlc=1.0001,            # fitted (CI_code [0.94, 1.07])
                score_bp_per_weight=170.0,   # fitted p99 @ 1.5x
                selection_bar_bp=None,       # no gated top selection child
            ),
        },
        # etf: BOTTOM only — pattern axis OFF (the gaps store carries no
        # etf pattern rows — the ladder measures empty); vol + depth
        # axes carry the etf-measured values; β/K the fitted values
        # (the 2026-09-27 pre-truncation study's gates ALL passed on
        # etf: Improv > 0 in 6/6 walk-forward years, 69% of codes
        # positive, beats both single-axis mirrors 6/6). No etf anchors
        # exist while the rebuild scope is index-only — recalibrate
        # when etf returns; no TOP entry (top ladders unmeasured).
        "etf": {
            "bottom": OhlcvBoostConfig(
                pattern_edge_bp={},          # unmeasured on etf — axis off
                beta_ohlc=0.0,               # (fitted: unidentifiable,
                                             # the pattern column is 0)
                beta_vol=0.9185,             # fitted (gates passed 6/6)
                beta_rsi=0.7246,
                gamma_ohlc_vol=0.0,
                depth_bp_per_z=48.8,         # measured etf slope
                score_bp_per_weight=130.0,
                vol_state_edge_bp={
                    "heavy": 77.0,   # same ordering, ~3× weaker than index
                    "normal": 3.0,
                    "shrink": -27.0,
                },
                vol_cell_edge_bp={
                    ("normal", "sharp_up"): 182.1,
                    ("heavy", "sharp_dn"): 156.1,
                    ("normal", "sharp_dn"): 136.2,
                    ("shrink", "sharp_dn"): 111.2,
                    ("heavy", "slow_dn"): 43.3,
                    ("normal", "slow_up"): 14.2,
                    ("heavy", "flat"): -13.7,
                    ("normal", "slow_dn"): -15.3,
                    ("shrink", "slow_dn"): -31.7,
                    ("normal", "flat"): -46.5,
                    ("shrink", "flat"): -48.5,
                },
            ),
        },
    },
}

# The feature columns the score reads — the engine's window slice must
# carry the stored pattern label + the px_vol registry states (both
# already fetched for the rsi stage: one slice serves every
# module). The RSI depth rides
# the CELLS' own ``excess`` column, not the window.
_WINDOW_COLUMNS: dict[str, tuple[str, ...]] = {
    bucket: ("pattern", "vol_state", "px_speed")
    for bucket in OHLCV_BOOST_CONFIGS
}


def enabled(bucket: str) -> bool:
    """Whether the bucket family carries a ohlcv-boost config."""
    return bucket in OHLCV_BOOST_CONFIGS


def window_columns(bucket: str, sec_type: str = "") -> tuple[str, ...]:
    """The feature columns the family's window slice needs: the stored
    pattern label + the px_vol registry cell states."""
    return _WINDOW_COLUMNS.get(bucket, ())


def side_configs(
    bucket: str, sec_type: str,
) -> dict[str, OhlcvBoostConfig]:
    """The (bucket, sec_type)'s per-side configs — empty when the
    family / sec_type carries no ohlcv-boost entry (the historical
    single-population shape)."""
    return OHLCV_BOOST_CONFIGS.get(bucket, {}).get(sec_type, {})


def _day0_edges(
    day0: pd.DataFrame,
    win: pd.DataFrame,
    ids: list[str],
    cfg: OhlcvBoostConfig,
) -> pd.DataFrame:
    """One side's day-0 anchors with the config's axis edges (steps
    2-3 of the module recipe: the anchor features join + the side's
    depth z + the stored labels through the ladders — missing →
    neutral 0)."""
    day0 = day0.merge(
        win[["code", "_t", "pattern", "vol_state", "px_speed"]]
        .rename(columns={"_t": "_t0"}),
        on=["code", "_t0"], how="left",
    )
    day0["_d"] = np.where(
        day0["side"] == "bottom", -1.0, 1.0) * day0["excess"]
    g = day0.groupby(ids, sort=False)["_d"]
    d_mu = g.transform("mean")
    d_sd = g.transform("std")
    z = ((day0["_d"] - d_mu) / d_sd.where(d_sd > 1e-9)) \
        .fillna(0.0).clip(-cfg.depth_z_cap, cfg.depth_z_cap)

    # ê_ohlc: pattern through the ladder (missing label / unknown
    # pattern → 0). ê_vol: the (vol_state, px_speed) CELL ladder first,
    # the MARGINAL state ladder for cells without a measured entry, 0
    # when the registry row itself is missing (NULL concat → NaN map).
    e_o = day0["pattern"].map(cfg.pattern_edge_bp) \
        .fillna(np.float64(0.0)).astype("float64")
    cell_key = day0["vol_state"] + "~" + day0["px_speed"]
    e_v = cell_key.map(
        {f"{vs}~{ps}": e for (vs, ps), e in cfg.vol_cell_edge_bp.items()}
    ).fillna(day0["vol_state"].map(cfg.vol_state_edge_bp)) \
     .fillna(np.float64(0.0)).astype("float64")
    day0["_eo"] = e_o
    day0["_ev"] = e_v
    day0["_er"] = (z * cfg.depth_bp_per_z).astype("float64")
    return day0


def apply(
    bucket: str,
    sec_type: str,
    cells: pd.DataFrame,
    win: pd.DataFrame,
    ids: list[str],
) -> pd.DataFrame:
    """Stamp the streak-merged anchor rows with ``is_ohlcv_boosted``
    and each streak's ``ohlcv_boost_weight`` — PER SIDE (each side's
    config entry scores its own anchors; sides without an entry stay
    unflagged).

    ``ids`` is the family's bucket-identity columns (code / regime /
    side + BUCKET_COLS). The weight is decided at the streak's DAY-0
    anchor (delay == 0 — the moment the signal becomes observable):
    the anchor's features map through the side config's measured-edge
    ladders to the trust-weighted score, translated to the weight
    ``w = max(1 + score/K, floor)``; rows without a stored label /
    registry row are NEUTRAL on that axis (edge 0). The WHOLE anchor
    ladder (delay 1..5 shares the streak's ``(ids, _t0)`` key,
    ``_t0 = _t - delay``) inherits both the flag and the weight. NO
    filtering: every in-scope streak of a bucket holding >= min_anchors
    streaks carries the flag, so the ohlcv child's population IS the
    parent's (same trigger days); the engine's weighted aggregation
    applies the weights to the measurement. Families / sec_types /
    sides without an applicable config get an all-0 flag column (the
    historical single-population shape).

    All steps run on device frames in INCREASING host dependence
    (per side, at most two entries):

      step 1  cudf  streak day-0 ordinal        (_t0 = _t - delay)
      step 2  cudf  anchor features (window join) + depth z (groupby)
      step 3  cudf  stored labels → aligned edges (missing → neutral 0)
      step 4  cudf  trust-weighted score → floored weight
      step 5  cudf  broadcast flag + weight to the anchor ladder
    """
    cfgs = side_configs(bucket, sec_type)
    if not cfgs:
        cells["is_ohlcv_boosted"] = np.int64(0)
        cells["ohlcv_boost_weight"] = np.float64(1.0)
        return cells

    # ---- step 1 (cudf): the streak's day-0 ordinal -------------------
    # one row per (bucket identity, streak) — the scoring population.
    # ``side`` and ``excess`` ride the cells (the standard cell columns
    # + the RSI tail depth); each configured side scores its own slice.
    a = cells.assign(_t0=cells["_t"] - cells["delay"])
    scored: list[pd.DataFrame] = []
    for side, cfg in sorted(cfgs.items()):
        day0 = a[(a["delay"] == 0) & (a["side"] == side)]
        day0 = day0[ids + ["_t0", "excess"]].drop_duplicates()
        if day0.empty:
            continue

        # ---- steps 2-3 (cudf): features + the side's axis edges -------
        day0 = _day0_edges(day0, win, ids, cfg)

        # ---- step 4 (cudf): trust-weighted score → floored weight -----
        # score in bp; the cross term carries γ per (100bp × 100bp) so a
        # fitted γ ~ O(1) is interpretable. The bucket's streak count
        # gates the child's existence.
        score = (cfg.beta_ohlc * day0["_eo"] + cfg.beta_vol * day0["_ev"]
                 + cfg.beta_rsi * day0["_er"]
                 + cfg.gamma_ohlc_vol * day0["_eo"] * day0["_ev"] / 100.0)
        day0["_w"] = np.maximum(
            1.0 + score / cfg.score_bp_per_weight, cfg.weight_floor)
        n = day0.groupby(ids, sort=False)["_w"].size() \
            .rename("_n").reset_index()
        day0 = day0.merge(n, on=ids, how="left")
        day0["_bst"] = (day0["_n"] >= cfg.min_anchors) \
            .fillna(False).astype("int64")
        scored.append(day0[ids + ["_t0", "_bst", "_w"]])

    # ---- step 5 (cudf): broadcast to the whole anchor ladder ---------
    # every rung of a streak inherits its day-0 weight (the delay
    # semantics: one verdict per streak). Sides without a config never
    # enter ``scored`` — their rows fall through the left-join fillna.
    day0_all = (
        pd.concat(scored, ignore_index=True) if scored
        else pd.DataFrame(columns=ids + ["_t0", "_bst", "_w"]))
    out = a.merge(day0_all, on=ids + ["_t0"], how="left")
    out["is_ohlcv_boosted"] = out["_bst"].fillna(0).astype("int64")
    out["ohlcv_boost_weight"] = out["_w"].fillna(1.0).astype("float64")
    return out.drop(columns=["_bst", "_w", "_t0"])


__all__ = [
    "OhlcvBoostConfig",
    "OHLCV_BOOST_CONFIGS",
    "CELL_MIN_N",
    "enabled",
    "window_columns",
    "side_configs",
    "apply",
]
