"""hedge_pairs shared constants — the label-code maps, the confidence
depth scale, the portfolio-rule mapping and the text fragments every
sub engine (trio / spread / standalone) and the portfolio-fragment
writer read. Family-level facts only; no compute."""
from __future__ import annotations

from _common.labels import (
    PORTFOLIO_SUB_TYPE_CODE,
    SIGNAL_SUB_TYPE_CODE,
    SIGNAL_TYPE_CODE,
)
from analysis.trends import REENTRY_EXIT_DAYS

from analysis.analysis_signals import config as cfg

DEPTH_SCALE = 10.0   # RSI points per unit of confidence depth
HP_TYPE = SIGNAL_TYPE_CODE["hedge_pairs"]
WITNESS_TYPE = HP_TYPE          # the witness records ride the family's
                                # own vocabulary (hp_rsi_witness)
# the per-ROLE witness sub types (2026-10-25, user-directed "diff legs
# or roles should have diff sub types"): the market / leg A / leg B
# witnesses each carry their own label; hp_rsi_witness (5830) stays
# registered for old-history decode only
WITNESS_SUBS = {
    "m": SIGNAL_SUB_TYPE_CODE["hp_rsi_witness_mkt"],
    "a": SIGNAL_SUB_TYPE_CODE["hp_rsi_witness_leg_a"],
    "b": SIGNAL_SUB_TYPE_CODE["hp_rsi_witness_leg_b"],
}
# the spread event's per-ROLE member sub types (2026-10-25): the
# bought laggard leg A and the sold leader leg B each their own label
# — payload vocabulary only, no rule rows behind them
SPREAD_MEMBER_SUBS = {
    "buy": SIGNAL_SUB_TYPE_CODE["hp_spread_buy_leg"],
    "sell": SIGNAL_SUB_TYPE_CODE["hp_spread_sell_leg"],
}
# WHICH PORTFOLIO RULE each orchestrating sub writes (2026-10-25, the
# portfolio_sub_type split — config HP_PORTFOLIO_SUB_TYPES), as the
# engine-facing int-keyed map: signal_sub_type code -> (the
# portfolio_sub_type code, the rule_params name). Orchestrating rows
# stamp it on their _portfolio_rule_params fragments (the name) and a
# dedicated _portfolio_sub_type marker (the code — run_family's
# grouping key); the REGISTERED mov_rsi cells never sit in the
# portfolio signals, each portfolio rule MAPS them through its
# signal_rule_ids only
HP_PORTFOLIO_SUBS: dict[int, tuple[int, str]] = {
    SIGNAL_SUB_TYPE_CODE[sub]: (PORTFOLIO_SUB_TYPE_CODE[ps], name)
    for sub, (ps, name) in cfg.HP_PORTFOLIO_SUB_TYPES.items()
}
TRIO_WASH_CODE = PORTFOLIO_SUB_TYPE_CODE["trio_wash"]
# the REGISTERED cells' TARGET PORTFOLIO RULES (the mapping the writer
# resolves through the _sourced_member markers): the shared market
# cells map to the trio_wash rules only (the spread disregards the
# benchmark curve), a leg cell to EVERY rule whose subs witness the
# legs (both witness them today)
SOURCED_MARKET_SUBS = (TRIO_WASH_CODE,)
SOURCED_LEG_SUBS = tuple(sorted({ps for ps, _ in
                                 HP_PORTFOLIO_SUBS.values()}))
PAIR_K = REENTRY_EXIT_DAYS
UNIVERSE = ("offset-opposite industry pairs (trailing 255d opposite "
            "score > 0.5, greedy-disjoint), pool 'all'")
WEIGHTING = "inverse-vol 60d, lagged one row"
