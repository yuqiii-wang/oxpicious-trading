"""The ohlcv-boost config dataclass (analysis.analysis_signals.engines.
ohlcv_boost._config).

ONE side's boost weighting: each trigger anchor's day-0 axes (the
stored OHLC pattern code; the (vol_state, px_speed) amount/price-speed
cell; the value's tail depth past the bucket's own bar) map through
the config's MEASURED-EDGE ladders to the trust-weighted score

    score_bp = beta_ohlc * e_ohlc(pattern)
             + beta_vol  * e_vol(vol_state, px_speed)
             + beta_rsi  * e_rsi(depth)
             + gamma     * e_ohlc * e_vol / 100     (bp)

    w        = max(1 + score_bp / score_bp_per_weight, weight_floor)

Every axis is expressed in the SAME unit — the sign-aligned forward
excess in bp vs the side base — so the fuse is a trust-weighted sum of
expected edges. ``bar_bp`` is the verdict bar a score must clear for
the anchor (history rows) / its bucket's mean score (threshold + curve
rows) to stamp ``is_ohlcv_boosted`` TRUE; None disables flagging for
the side. An axis with an empty ladder / zero beta is OFF for the
config. All ladders are keyed by the AXIS CODES (_axes — int keys;
strings never enter the frames).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OhlcvBoostConfig:
    """One (family, sec_type, SIDE)'s boost ladders + verdict bar (see
    the module docstring). ``vol_cell_edge_bp`` entries carry the CELL
    edges measured at the study's evidence floor; a cell without an
    entry falls back to the ``vol_state_edge_bp`` MARGINAL edge; a
    missing registry row (AXIS_MISSING on either axis) reads NEUTRAL
    (0) on that axis. ``bar_bp`` None = the side never flags."""

    pattern_edge_bp: dict[int, float] = field(default_factory=dict)
    vol_state_edge_bp: dict[int, float] = field(default_factory=dict)
    vol_cell_edge_bp: dict[tuple[int, int], float] = field(
        default_factory=dict,
    )
    beta_ohlc: float = 1.0
    beta_vol: float = 0.0
    beta_rsi: float = 0.0
    gamma_ohlc_vol: float = 0.0
    depth_bp_per_z: float = 0.0
    depth_z_cap: float = 2.0
    score_bp_per_weight: float = 150.0
    weight_floor: float = 0.5
    min_anchors: int = 1
    bar_bp: float | None = 0.0


__all__ = ["OhlcvBoostConfig"]
