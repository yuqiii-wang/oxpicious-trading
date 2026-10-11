"""Price × Trading-Amount state registry constants for
analysis.mov_ave_spread.

analysis.mov_ave_price_vs_amt — per-date Price × Trading-Amount state
registry (the px_vol family's date-level record). One row per
(sec_type, code, date): every day with valid inputs joins EXACTLY ONE of
the 15 price-speed × amount-state categories. The category definitions
live in THIS package (steps/px_vol.py's feature layer + the PX_VOL_*
calibration constants below) and every row records them, so consumers can
verify what they read. Consumer: the MA-Spread UI's Px-Vol States date
shading.

Internal step of the parent mov_ave_spread pipeline (see
steps/price_vs_amt.py) — reuses the parent source DataFrame's price +
trading_amount columns; REBUILT WHOLESALE per sec_type (ETF adj_close
back-adjustments rewrite price history — market_regimes precedent).
"""
PRICE_VS_AMT_TABLE = "analysis.mov_ave_price_vs_amt"
PRICE_VS_AMT_ANALYSIS_NAME = "mov_ave_price_vs_amt"

# ---- px_vol calibration constants (recorded on every registry row) --------
#
# The Price × Amount state's per-code ADAPTIVE bars: a day's speed /
# volume states are t / z scores vs the code's OWN trailing moments
# (σ_ret spans ~1%–2.9% daily across equity-like indices; fixed ±2% /
# 量比 1.5/0.8 bars systematically misfire on high/low-σ codes).
#
# Calibration: k/z bars are chosen to reproduce the legacy fixed
# thresholds' POOLED trigger rates on equity-like indices (up 8.4% /
# down 7.6% / 放量 4.0% / 缩量 14.2%), so bucket sample shares stay
# comparable with the studies. The z bars were calibrated on the
# RETIRED ratio5d metric and carry over to log_level as the starting
# calibration (re-measure the pooled rates after a rebuild).

# Rolling windows (rows): σ_ret of ret_1d + the log-amount level
# moments; PX_VOL_LB_WINDOW is the classic 量比 base window
# (trading_amount mean of t-lb_window..t-1) — evidence-only since the
# log_level refactor. All shifted 1 row before use.
PX_VOL_SIGMA_WINDOW = 255
PX_VOL_SIGMA_MIN_DAYS = 60
PX_VOL_LB_WINDOW = 5

# The vol leg's metric (recorded on every analysis.mov_ave_price_vs_amt
# row as amt_metric — consumers can check which DEFINITION a registry
# was built with):
#   "log_level" — px_z = z-scored log(trading_amount) vs the code's own
#                 trailing PX_VOL_SIGMA_WINDOW-row moments (shift 1) —
#                 the LEVEL statement heavy/shrink claim. The retired
#                 "ratio5d" (z-scored 量比 vs its own moments) fired
#                 heavy on drought bounces: in a declining-volume
#                 regime the 5-day base collapses, so a day whose
#                 amount sat far below the code's level scored
#                 ratio ≈ 1.7 → z > 2 ("Amt Up" on visibly low amt).
PX_VOL_AMT_METRIC = "log_level"

# t = ret_1d / σ_ret state bars.
PX_VOL_K_SLOW_UP = 1.26
PX_VOL_K_SLOW_DN = 1.29
PX_VOL_K_SHARP = 2.0

# Amount-level z state bars (px_z).
PX_VOL_Z_HEAVY = 2.0
PX_VOL_Z_SHRINK = -0.92

# σ_ret floor: below this the code is bond-like (σ ≈ 0.01–0.02% for
# 中证短融/企债) and no state row is ever written — tiny wiggles would
# be classified as extremes.
PX_VOL_SIGMA_FLOOR = 0.005

# Speed-state names in PX_VOL_SPEEDS order and volume states in
# PX_VOL_VOL_STATES order (the 15 = 5 × 3 categories). The order IS the
# stats.label_dim code order (px_speed: sharp_dn=0..sharp_up=4,
# vol_state: shrink=0..heavy=2) — the classify emits each state's INDEX
# directly as the stored SMALLINT code (the label-code contract; guard
# asserts keep this file in lockstep with _common.labels).
PX_VOL_SPEEDS: tuple[str, ...] = (
    "sharp_dn", "slow_dn", "flat", "slow_up", "sharp_up",
)
PX_VOL_VOL_STATES: tuple[str, str] = ("shrink", "normal", "heavy")

from _common.labels import PX_SPEED_CODE, VOL_STATE_CODE as _VS

assert PX_VOL_SPEEDS == tuple(PX_SPEED_CODE), (
    "PX_VOL_SPEEDS order drifted from _common.labels.PX_SPEED_CODE"
)
assert PX_VOL_VOL_STATES == tuple(_VS), (
    "PX_VOL_VOL_STATES order drifted from _common.labels.VOL_STATE_CODE"
)

# Reversal side per speed (recorded on the registry's side column as its
# stats.label_dim 'side' code): the up speeds expect reversal DOWN
# (top), the down speeds reversal UP (bottom); flat carries flat — no
# directional claim.
PX_VOL_SPEED_SIDE: dict[str, str] = {
    "sharp_up": "top", "slow_up": "top",
    "flat": "flat",
    "slow_dn": "bottom", "sharp_dn": "bottom",
}

PRICE_VS_AMT_COLUMNS = (
    "sec_type", "code", "date",
    "px_speed", "vol_state", "side",
    "px_t", "px_z", "ret_1d", "px_sigma", "amt_ratio",
    "sigma_window", "lb_window",
    "k_slow_up", "k_slow_dn", "k_sharp",
    "z_heavy", "z_shrink", "sigma_floor",
    "amt_metric",
)

PRICE_VS_AMT_DESCRIPTION = (
    "Per-(code, date) Price × Trading-Amount state registry (ETF + "
    "Index + Stock): every day with valid inputs joins exactly ONE of "
    "the 15 px_speed × vol_state categories — the σ-standardized 1-day "
    "price change (t = ret_1d / the code's own rolling-255 σ_ret "
    "ddof=1, min 60, shifted 1 row; σ below sigma_floor 0.005 excludes "
    "bond-like codes: sharp_up t > 2.0 / slow_up 1.26 < t <= 2.0 / "
    "flat / slow_dn / sharp_dn t < -2.0) × the z-scored log "
    "trading-amount LEVEL (z vs the rolling-255 moments of "
    "log(trading_amount) shifted 1 row: heavy z > 2.0 / normal / "
    "shrink z < -0.92 — a LEVEL statement vs the code's own "
    "trailing-year amount distribution; the retired 量比-ratio z "
    "fired heavy on drought bounces, i.e. 'Amt Up' on visibly low "
    "amount). NULL trading_amount → no row. side per speed: "
    "top (up speeds — expect reversal down) / bottom (down speeds) / "
    "flat (no directional claim). The px_t / px_z / ret_1d / "
    "px_sigma / amt_ratio columns record the state evidence (amt_ratio "
    "is the classic 量比, evidence-only since the log_level refactor) "
    "so consumers recompute nothing; amt_metric records the vol leg's "
    "definition so consumers can check which one a registry was built "
    "with. Rows are REBUILT WHOLESALE "
    "per sec_type on every pipeline run (ETF adj_close back-adjustments "
    "rewrite price history — the market_regimes precedent). Source: the "
    "shared per-sec_type price/amt series (COALESCE(adj_close, close) "
    "for ETF, estimated closes excluded — the analysis_signals input "
    "conventions); the feature layer + classification live in this "
    "package (steps/px_vol.py + the PX_VOL_* constants)."
)
