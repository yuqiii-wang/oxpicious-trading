"""rf_break — the roof/floor breakout family engine (analysis.
analysis_signals.engines.signal_families.rf_break.engine, 2026-10-16).

The TURTLE breakout on this repo's own roof/floor machinery: the
trigger is the close crossing the PRIOR day's level from
analysis.mov_ave_spreads_roof_floor_estimates — per the (METHOD,
WINDOW) grid:

  method   donchian (1) — the window's max intraday high / min low;
           the raw structural extreme.
           median   (5) — the NaN-skipping median of the five estimate
           methods per (date, period): the cross-method CONSENSUS
           level (smoother than the raw extreme; catches breaks the
           lone donchian wick-extreme misses).
  window   {60, 120, 255, 500} trading days.

THE 2026-10-26 LEGACY CLEANUP (user-directed "remove roof for buy,
floor for sell decisions, but totally replace with the above
findings"): the CATCH-ALL sub types are RETIRED —

  rf{W}{infix}_roof     BUY  — the first close ABOVE the prior day's
      roof (any breach): RETIRED. The depth-split study measured the
      shallow band drifting up at every depth with the catch-all
      diluted ~2x by it; the deep tier below IS the claim.
  rf{W}{infix}_floor    SELL — the first close BELOW the prior floor:
      RETIRED. The gap study measured the pooled population
      sign-flipping by level agreement (tight-gap tiers ~0, wide-gap
      tiers bouncing -2..-6.6 in 8/8 sections) — an unconditioned
      floor sell is a mix of exit edge and bounce, and the user
      retired the decision.

The inventory is now THREE all-BUY legs (the gdepth/deep buy-only
precedent — this family carries no sell at all):

  rf{W}{infix}_roof_deep  BUY — the DEPTH-GATED continuation: close
      beyond the roof PAST the self-adaptive k bar (grid
      RF_ROOF_DEEP_BARS {1, 2, 3}%). The 2026-10-26 depth-split study
      (temp_scripts/rf_depth_split_study.py): the breach-depth
      gradient is monotone UP — >1-2% beyond the roof measures ~2x
      the catch-all's forward blend (+2.00% @65.6 hit vs +0.90 on
      rf255_med) — the regime-change claim the shallow band dilutes.
  rf{W}{infix}_floor_deep BUY — the CAPITULATION V-BOTTOM: close
      below the floor PAST the k bar (grid RF_FLOOR_DEEP_BARS
      {2, 3, 5}%). The study REVERSED the hypothesized deep-floor
      sell: the deeper below the consensus floor, the harder the
      forward bounce (rf500_med deep5: sell pool -4.66% = forward
      +4.66% @85% up-rate) — the falling knife reverses at depth.
      Measured gap-AGNOSTIC (the 2026-10-26 two-method gap study:
      flat across |donchian − median| terciles — the washout works
      regardless of level agreement).
  rf{W}_floor_bounce      BUY — the CONSENSUS-BROKEN / WICK-INTACT
      bounce (2026-10-26 gap study, temp_scripts/rf_gap_study.py):
      the close breaks the prior day's MEDIAN consensus floor while
      the donchian wick floor still holds below it — the dip stops at
      the raw extreme when the consensus gives way. Measured at ALL
      FOUR windows (t 3.0-4.5, hit 57-67%: W60 +1.51 n=164, W120
      +1.54 n=104, W255 +2.50 n=66, W500 +2.64 n=42); the mirrored
      sell measures -1.5..-2.6 everywhere. The controlled-dip twin
      of floor_deep's washout: the two floor buys tile the whole
      breach range. Cross-method by construction (ONE population per
      window — no method infix).

2026-10-26 INVERSION FIX: the floor side's breach mask had read the
un-signed ratio (close ABOVE the prior floor — the roof condition)
since the 2026-10-16 first build, so the floor sub types measured the
recovery-back-above-the-floor population instead of breakdowns (27
positive-value "floor" events in six years, the wrong population
behind the "floor cells measured out" record). The breach pct is now
DIRECTIONAL — positive = beyond the level in the trade's direction —
so the recorded signal value reads the breakdown depth for floors.
The prior floor evidence is void; the first directional rebuild
re-measures every floor cell.

NO cross-window carving: each (method, window) breach fires its own
sub type — the windows are different CLAIM STRENGTHS (a new 60d high
and the simultaneous new 255d high are different facts), and the
first-build A/B measured the carve-out variant STARVING the mid
windows (the 255d cells lost their strongest days to the 500d claim
and fell below the gate floor — the 2024-09-30 events vanished with
them), so every window keeps its own full population and the gate
decides. The trigger day's own level never sets its own bar (the
level is shifted one row on the code's own rows). REGIME-FREE with a
trend split (the ma_cross precedent — per-regime cells measured thin
and sign-flippy, and the studies' starving corners were regime-corner
artifacts). The ohlcv boost opts in (the turnover-climax axis widens
the trust-weighted population).

THE SHARED VARIANT PASSES (2026-10-26, user-directed "create shared pe
and ohlcv logic ... use them where possible or relevant"): the deep
floor legs (the capitulation V-bottom) run BOTH boosted variants on
the shared PK axes — the PE-EXTREMES conditioned population (the
rebound study's capitulation barbell: the washout bounces at cheap /
rich PE extremes, the middle dead) and the OHLCV-boosted one (the
washout's turnover climax amt_climax >= HEAVY — capitulation volume
at lows); the roof deep leg runs the OHLCV-boosted population only
(the classic volume-confirmed breakout: >2% past the roof on heavy
turnover is the regime-change claim's confirmation); the floor bounce
(the controlled dip) runs the PE-conditioned variant. Each variant is
its own run_cells call — a SEPARATE row set on the is_pe_boosted /
is_ohlcv_boosted PK axes, judged independently by the same measured
gate (a variant that cannot clear the bar emits nothing — the
negative-optimization self-prune). The gates are the SHARED class
stack (engines._variant_base.VariantGate + engines._pe_boost /
engines._ohlcv_boost, the 2026-10-27 OOP restructure); the PE gate
skips entirely when the valuation store has no rows for the scope
(every non-index sec_type today).
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import grouped_shift

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines._ohlcv_boost import OhlcvBoostGate
from analysis.analysis_signals.engines._pe_boost import PeBoostGate
from analysis.analysis_signals.engines._variant_base import VariantGate
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.trends import REENTRY_EXIT_DAYS
from analysis.analysis_signals.fetch.inputs import fetch_rf_inputs

# the breach depth normalizer: 2% beyond the level = depth 1.0 (the
# confidence scale's unit, capped at 2)
_RF_DEPTH_UNIT = 0.02

# the estimates-table level prefix per config method infix (donchian
# reads roof{W}/floor{W} columns, median reads mroof{W}/mfloor{W})
_METHOD_PREFIX = {"": "", "_med": "m"}


class RoofFloorEntry(RuleEntry):
    """roof/floor breakout — one entry, the (method, window, side)
    grid."""

    FAMILY = "rf_break"
    PACKAGE = "rf_break"
    # the 2026-10-26 legacy cleanup: the roof/floor CATCH-ALLs are
    # RETIRED (their labels 5600-5633 stay in the vocabulary for
    # old-history decode only) — the inventory is the deep tiers per
    # (method, window) + the cross-method floor bounce per window,
    # ALL action BUY (the family carries no sell)
    SUB_TYPES = tuple(
        f"rf{w}{infix}_{suffix}"
        for infix, _ in cfg.RF_BREAK_METHODS
        for suffix in ("roof_deep", "floor_deep")
        for w in cfg.RF_BREAK_WINDOWS
    ) + tuple(
        f"rf{w}_floor_bounce" for w in cfg.RF_BREAK_WINDOWS
    )
    REGIME_FREE = True
    USES_TREND_SPLIT = True
    OHLCV_BOOST = True

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_rf_inputs(self.conn, self.sec_type, self.codes)

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        keys = ["code"]
        # the SHARED variant gates (engines._pe_boost / engines.
        # _ohlcv_boost — instances are per-run, they cache the frame's
        # condition): the PE-EXTREMES capitulation barbell + the
        # turnover-climax confirmation; the PE gate goes inactive when
        # the valuation store has no rows for the scope
        pe_gate = PeBoostGate("extremes")
        amt_gate = OhlcvBoostGate()
        for gate in (pe_gate, amt_gate):
            gate.prepare(frame)
        deep_gates: dict[str, tuple[VariantGate, ...]] = {
            "roof": (amt_gate,),
            "floor": (pe_gate, amt_gate),
        }
        # the DIRECTIONAL breach per (method, side, window), computed
        # ONCE for both methods: positive = beyond the level in the
        # trade's direction (roof buys price ABOVE it, floor legs price
        # BELOW it — the breakdown; the un-signed ratio would fire the
        # floor on mere above-floor closes, the 2026-10-26 inversion
        # fix). The deep tiers read their OWN method's breach, the
        # floor bounce reads BOTH methods' floor breach
        breach: dict[tuple[str, str, int], pd.Series] = {}
        for infix, _method_code in cfg.RF_BREAK_METHODS:
            p = _METHOD_PREFIX[infix]
            for side in ("roof", "floor"):
                sign = 1.0 if side == "roof" else -1.0
                for w in cfg.RF_BREAK_WINDOWS:
                    level = frame[f"{p}{side}{w}"].astype("float64")
                    prev_level = grouped_shift(
                        frame.assign(_l=level), keys, ["_l"],
                        out_names=["_pl"], periods=1,
                    )["_pl"]
                    breach[(infix, side, w)] = (
                        frame["price"] / prev_level - 1.0) * 100.0 * sign
        for infix, method_code in cfg.RF_BREAK_METHODS:
            p = _METHOD_PREFIX[infix]
            method_label = "donchian" if infix == "" else "median"
            # the DEEP tiers (2026-10-26, the depth-split study's
            # graduates — temp_scripts/rf_depth_split_study.py): the
            # breach DEPTH as its own claim, BOTH action BUY. roof_
            # deep = the depth-gated continuation (the study's
            # monotone-UP gradient: >1-2% beyond the roof measures
            # ~2x the catch-all's forward blend); floor_deep = the
            # CAPITULATION V-BOTTOM (the deeper below the consensus
            # floor the harder the forward bounce — the study
            # REVERSED the hypothesized deep-floor sell). The k bar
            # self-adapts per cell from the config grids (deepest
            # bar first — ties claim the deepest), the ma_cross
            # gdepth precedent.
            for side, bars, policy in (
                ("roof", cfg.RF_ROOF_DEEP_BARS,
                 "continuation_depth_gated"),
                ("floor", cfg.RF_FLOOR_DEEP_BARS,
                 "capitulation_v_bottom"),
            ):
                for w in cfg.RF_BREAK_WINDOWS:
                    br = breach[(infix, side, w)]
                    col = f"_rf{p}{w}_{side}_pct"
                    frame[col] = br
                    cands = [
                        Candidate(
                            key=f"{p}{side}{w}_deep{k:g}",
                            mask=(br > k).fillna(False),
                            reference=0.0,
                            depth=(br / _RF_DEPTH_UNIT)
                            .clip(lower=0.0),
                            params={
                                "kind": f"{side}_break_deep",
                                "window": w,
                                "method": method_label,
                                "min_breach_pct": k,
                                "bar_def": {
                                    "kind": "prior_day_level",
                                    "level": f"{p}{side}{w}",
                                    "method": method_label,
                                    "method_code": method_code,
                                    "source": "analysis.mov_ave_"
                                              "spreads_roof_floor_"
                                              "estimates",
                                    "shift": 1,
                                    "min_breach_pct": k,
                                },
                            },
                        )
                        for k in sorted(bars, reverse=True)
                    ]
                    call = dict(
                        k=REENTRY_EXIT_DAYS, value_col=col,
                        depth_factor=0.5,
                        extra_params={
                            "side_policy": policy,
                            "value_unit":
                                "pct beyond the prior day's level",
                        },
                    )
                    r, h = self.run_cells(
                        frame, f"rf{w}{infix}_{side}_deep", "buy",
                        cands, **call,
                    )
                    rules.extend(r)
                    history.extend(h)
                    # the SHARED VARIANT PASSES (2026-10-26): separate
                    # run_cells calls per boosted gate — the population
                    # is the plain candidates' masks AND-gated by the
                    # gate's condition, the fragment rides bar_def
                    # under the gate's own key, the rows stamp the
                    # axis on the PK; an inactive gate (no axis data
                    # this scope) passes silently
                    for gate in deep_gates[side]:
                        r, h = gate.run_pass(
                            self, frame, f"rf{w}{infix}_{side}_deep",
                            "buy", cands, call,
                        )
                        rules.extend(r)
                        history.extend(h)
        # the FLOOR BOUNCE (2026-10-26 gap study, temp_scripts/
        # rf_gap_study.py): the close breaks the prior day's MEDIAN
        # consensus floor while the DONCHIAN wick floor still holds
        # below it — the consensus-broken / wick-intact dip. The
        # wick-bounded depth (the median breach can never exceed the
        # donchian-median gap while the wick holds) is the structural
        # definition of "shallow" the depth study's numeric band
        # could not express; measured t 3.0-4.5 at all four windows.
        # ONE cross-method population per window (no method infix)
        for w in cfg.RF_BREAK_WINDOWS:
            med_br = breach[("_med", "floor", w)]
            don_br = breach[("", "floor", w)]
            col = f"_rf{w}_floor_bounce_pct"
            frame[col] = med_br
            mask = ((med_br > 0.0) & (don_br <= 0.0)).fillna(False)
            cands = [Candidate(
                key=f"floor_bounce{w}",
                mask=mask,
                reference=0.0,
                depth=(med_br / _RF_DEPTH_UNIT).clip(lower=0.0),
                params={
                    "kind": "floor_bounce",
                    "window": w,
                    "level_method": "median",
                    "guard_method": "donchian",
                    "bar_def": {
                        "kind": "prior_day_level",
                        "level": f"mfloor{w}",
                        "method": "median",
                        "method_code": 5,
                        "source": "analysis.mov_ave_spreads_"
                                  "roof_floor_estimates",
                        "shift": 1,
                        "guard": {
                            "level": f"floor{w}",
                            "method": "donchian",
                            "method_code": 1,
                            "condition":
                                "close >= prior day's floor "
                                "(the wick extreme intact)",
                        },
                    },
                },
            )]
            call = dict(
                k=REENTRY_EXIT_DAYS, value_col=col,
                depth_factor=0.5,
                extra_params={
                    "side_policy": "consensus_break_wick_intact",
                    "value_unit":
                        "pct below the prior day's median floor",
                },
            )
            r, h = self.run_cells(
                frame, f"rf{w}_floor_bounce", "buy", cands, **call,
            )
            rules.extend(r)
            history.extend(h)
            # the PE-conditioned controlled dip (the shared variant
            # pass — the barbell's cheap/rich extremes; the wick-held
            # dip at a middle PE is the dead zone by the same study)
            r, h = pe_gate.run_pass(
                self, frame, f"rf{w}_floor_bounce", "buy", cands, call,
            )
            rules.extend(r)
            history.extend(h)
        return rules, history
