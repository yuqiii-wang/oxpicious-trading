"""The ohlcv-boost config table — THE single place the boost semantics
per (family, sec_type, SIDE) live
(analysis.analysis_signals.engines.ohlcv_boost._configs).

A family opts in by having an entry here (its engine imports the
formula and scores its anchors through side_configs(family, sec_type));
families / sec_types / sides without an entry keep the unboosted shape
(every flag FALSE).

CALIBRATION PROVENANCE — the 2026-09-27/28 fused-score study on the
THEN-forecast layer's mov_rsi anchor population (retraced 2026-10-06
in docs/ohlcv_boost_summary.md; artifacts in
temp_scripts/output/fused_score/ — fused_ladders.json, fused_fit.json,
fused_5_top_vs_bottom/, fused_6_top_pattern/):

  - index BOTTOM, the 3-axis + cross-term form: the bounded least-
    squares fit (beta in [0, 1.5], gamma in [-1, 1], code- and
    month-cluster bootstrap CIs exclude 0), K = score p99 at 1.5x
    weight, depth the measured slope. Adopted 2026-09-27 after the
    hand-set seed FAILED the first rebuild verification (36% of
    buckets improv > 0 — the all-days pattern ladder is wrong on the
    anchor population; the anchor-measured ladder is the shipped one).
  - index TOP, PATTERN-ONLY (beta_vol = beta_rsi = gamma = 0): the
    step-5 diagnosis found the 3-axis form unshippable on top — the
    base flips sign across epochs (4/7 years negative), the vol/depth
    per-year slopes flip sign, and the thick top vol cells carry
    NEGATIVE edges — while the PATTERN axis is positive 7/7 years.
    Step-6 calibration (fused_6_top_pattern.json): beta 1.0001
    (code-cluster CI [0.94, 1.07]), K = 170, walk-forward improv > 0
    in 5/6 years, 64% of codes positive.
  - etf BOTTOM, vol + depth only (the gaps store carries no etf
    pattern rows — the ladder measures empty): the pre-truncation
    study's fitted values, all walk-forward gates passed 6/6.
    Recalibrate when etf anchors exist again.

The ladders were measured on the RETIRED forecast engine's population
and are REUSED verbatim (2026-10-06, user direction) — the current
signals tier computes its own buckets off the same trigger anatomy
(streak-merged delay-0 anchors on the is_typical population), so the
transfer is reasonable but NOT re-validated; a recalibration study on
the new population should replace the values in place. Vol cells
carried the study's n >= 500 evidence floor (thinner cells fall back
to the marginal vol_state edge). One deviation from the retired
module: the retired TOP side shipped NO selection child
(selection_bar_bp None) — the single flag carries the boost verdict
on every configured side, so the top config ships bar_bp 0.0 (its
pattern-only score flags favorable-anatomy top buckets/rows).
"""
from __future__ import annotations

from analysis.analysis_signals.engines.ohlcv_boost._axes import (
    PATTERN_CODE as _P,
)
from analysis.analysis_signals.engines.ohlcv_boost._axes import (
    PX_SPEED_CODE as _PS,
)
from analysis.analysis_signals.engines.ohlcv_boost._axes import (
    VOL_STATE_CODE as _VS,
)
from analysis.analysis_signals.engines.ohlcv_boost._config import (
    OhlcvBoostConfig,
)

_PAT = _P  # spike_fade=0 flush_recover=1 gap_up_advance=2 gap_up_decline=3
#            gap_down_advance=4 gap_down_decline=5 neutral=6
_VS_ = _VS  # shrink=0 normal=1 heavy=2
_PS_ = _PS  # sharp_dn=0 slow_dn=1 flat=2 slow_up=3 sharp_up=4

# family -> sec_type -> side -> config
OHLCV_BOOST_CONFIGS: dict[
    str, dict[str, dict[str, OhlcvBoostConfig]],
] = {
    "mov_rsi": {
        "index": {
            # BOTTOM: the 3-axis + cross-term form (fused_fit.json).
            "bottom": OhlcvBoostConfig(
                pattern_edge_bp={
                    _PAT["spike_fade"]: 105.2,        # 冲高回落
                    _PAT["gap_up_advance"]: 149.1,    # 高开高走
                    _PAT["gap_up_decline"]: -71.0,    # 高开低走
                    _PAT["gap_down_decline"]: -15.7,  # 低开低走
                    _PAT["gap_down_advance"]: 104.1,  # 低开高走
                    _PAT["flush_recover"]: 75.2,      # 探底回升
                    _PAT["neutral"]: -51.0,
                },
                vol_state_edge_bp={
                    _VS_["heavy"]: 261.0,   # 放量 (6/7 yrs)
                    _VS_["normal"]: 16.5,
                    _VS_["shrink"]: -47.9,  # 缩量 the drag leg
                },
                vol_cell_edge_bp={
                    (_VS_["heavy"], _PS_["sharp_dn"]): 324.4,   # capitulation flush
                    (_VS_["normal"], _PS_["sharp_up"]): 175.9,
                    (_VS_["normal"], _PS_["sharp_dn"]): 154.0,
                    (_VS_["heavy"], _PS_["flat"]): 125.7,
                    (_VS_["normal"], _PS_["slow_up"]): 112.3,
                    (_VS_["shrink"], _PS_["sharp_dn"]): 87.4,
                    (_VS_["heavy"], _PS_["slow_dn"]): -23.6,
                    (_VS_["normal"], _PS_["slow_dn"]): -30.3,
                    (_VS_["normal"], _PS_["flat"]): -38.9,
                    (_VS_["shrink"], _PS_["slow_dn"]): -59.5,
                    (_VS_["shrink"], _PS_["flat"]): -62.2,
                },
                beta_ohlc=0.8715,     # fitted (bounded LS; cluster
                beta_vol=0.7524,      #   bootstrap CIs exclude 0)
                beta_rsi=0.7463,
                gamma_ohlc_vol=0.7817,  # measured super-additive
                depth_bp_per_z=68.5,    # measured slope
                score_bp_per_weight=270.0,  # fitted p99 @ 1.5x
            ),
            # TOP: PATTERN-ONLY (fused_6_top_pattern.json) — the
            # vol/depth axes are OFF (their per-year slopes flip sign
            # on top); flush_recover 探底回升 is the one big edge.
            "top": OhlcvBoostConfig(
                pattern_edge_bp={
                    _PAT["spike_fade"]: -9.4,        # 冲高回落
                    _PAT["gap_up_advance"]: -55.9,   # 高开高走
                    _PAT["gap_up_decline"]: 41.5,    # 高开低走
                    _PAT["gap_down_decline"]: -13.7, # 低开低走
                    _PAT["gap_down_advance"]: -5.7,  # 低开高走
                    _PAT["flush_recover"]: 254.4,    # 探底回升
                    _PAT["neutral"]: -25.7,
                },
                beta_ohlc=1.0001,       # fitted (CI_code [0.94, 1.07])
                score_bp_per_weight=170.0,  # fitted p99 @ 1.5x
            ),
        },
        # etf: BOTTOM only — pattern axis OFF (no etf pattern rows);
        # vol + depth carry the etf-measured values (gates 6/6).
        "etf": {
            "bottom": OhlcvBoostConfig(
                pattern_edge_bp={},   # unmeasured on etf — axis off
                beta_ohlc=0.0,
                beta_vol=0.9185,
                beta_rsi=0.7246,
                gamma_ohlc_vol=0.0,
                depth_bp_per_z=48.8,  # measured etf slope
                score_bp_per_weight=130.0,
                vol_state_edge_bp={   # same ordering, ~3x weaker
                    _VS_["heavy"]: 77.0,
                    _VS_["normal"]: 3.0,
                    _VS_["shrink"]: -27.0,
                },
                vol_cell_edge_bp={
                    (_VS_["normal"], _PS_["sharp_up"]): 182.1,
                    (_VS_["heavy"], _PS_["sharp_dn"]): 156.1,
                    (_VS_["normal"], _PS_["sharp_dn"]): 136.2,
                    (_VS_["shrink"], _PS_["sharp_dn"]): 111.2,
                    (_VS_["heavy"], _PS_["slow_dn"]): 43.3,
                    (_VS_["normal"], _PS_["slow_up"]): 14.2,
                    (_VS_["heavy"], _PS_["flat"]): -13.7,
                    (_VS_["normal"], _PS_["slow_dn"]): -15.3,
                    (_VS_["shrink"], _PS_["slow_dn"]): -31.7,
                    (_VS_["normal"], _PS_["flat"]): -46.5,
                    (_VS_["shrink"], _PS_["flat"]): -48.5,
                },
            ),
        },
    },
}


def enabled(family: str) -> bool:
    """Whether the family carries an ohlcv-boost config."""
    return family in OHLCV_BOOST_CONFIGS


def side_configs(
    family: str, sec_type: str,
) -> dict[int, OhlcvBoostConfig]:
    """The (family, sec_type) per-side configs, keyed by the SIDE CODE
    (stats.label_dim 'side'); empty when the family / sec_type carries
    no entry (the unboosted shape)."""
    from _common.labels import SIDE_CODE

    by_name = OHLCV_BOOST_CONFIGS.get(family, {}).get(sec_type, {})
    return {
        SIDE_CODE[name]: cfg
        for name, cfg in by_name.items()
        if name in SIDE_CODE
    }


__all__ = ["OHLCV_BOOST_CONFIGS", "enabled", "side_configs"]
