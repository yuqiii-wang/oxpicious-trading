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
           level (smoother than the raw wick extreme; catches breaks
           the lone donchian wick-extreme misses).
  window   {60, 120, 252, 504} trading days.

THE ROOF GAP GUARD (2026-11-18, user-directed "stop rf break signal
if one day rise/drop is too large that the day opportunity is
lost" — temp_scripts/rf_gap_guard_study.py): a roof_deep trigger is
STOPPED when the max ONE-DAY rise over the confirmation window (the
last RF_BREAK_PERSIST_DAYS traded days) reaches RF_BREAK_GAP_BAR_PCT
(7%) — the move came from a single spent jump, the day's opportunity
is gone. Measured on the shipped population: trigger-day rises <= 7%
all measure +1.45..+2.72% forward blend, the >= 7% class -1.41%
@32.2% hit (the bar drops 974 such triggers; kept +2.41% vs pool
+2.19%). ROOF-ONLY BY EVIDENCE: the floor side measures the EXACT
INVERSE — big-drop days are the capitulation washout's edge,
monotone up (>7% drops +6.84% @98.2% hit; every exclusion bar makes
the floor worse, kept +2.5..+3.0 vs dropped +5.1..+8.0) — do not
symmetrize without new measurements.

THE PERSISTENCE + TREND CONFIRMATION (2026-11-18, user-directed
"update the roof/floor break to as multiple days remained exceeded
the bands and the upward/downward trend maintained"): the trigger is
no longer the episode-first CROSS — the close must have REMAINED
beyond the band for RF_BREAK_PERSIST_DAYS (3) CONSECUTIVE trading
days of the window's own level, the signal day being the
CONFIRMATION day the run reaches its Nth day (the episode K-absorb
still dedupes later re-qualifications), AND the code's own trend
state must agree with the break direction that day: roof legs only
on rise/top (the upward structure maintained), floor legs only on
fall/bottom (downward maintained — RF_BREAK_TREND_GATE). The
candidate's own day condition (the deep k bar, the bounce's
wick-intact guard) rides on the confirmation day as before; the
bar_def documents persist_days + trend_gate on every rule. The
forward blend therefore measures from the confirmation day (a
built-in N-1-day delay vs the first cross — the cost of the
confirmation, by design).

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
      rf252_med) — the regime-change claim the shallow band dilutes.
  rf{W}{infix}_floor_deep BUY — the CAPITULATION V-BOTTOM: close
      below the floor PAST the k bar (grid RF_FLOOR_DEEP_BARS
      {2, 3, 5}%). The study REVERSED the hypothesized deep-floor
      sell: the deeper below the consensus floor, the harder the
      forward bounce (rf504_med deep5: sell pool -4.66% = forward
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
      sell measures -1.5..-2.6 everywhere. The controlled-dip twin of
      floor_deep's washout: the two floor buys tile the whole
      breach range. Cross-method by construction (ONE population per
      window — no method infix).

THE ROOF SIDE SCALE (2026-11-18, user-directed "roof break signals
are reduced unless very confident"; the floor-bias study's product
step — temp_scripts/rf_floor_bias_retest_squeeze_study.py +
rf_roof_scale_study.py, docs/rf_floor_bias_retest_squeeze_study_
2026-11-18.md): the H1 floor-bias confirmation (floor/roof 1.24-1.35x
at matched depth, shipped pools +1.78 vs +1.39, year majority 7/7 vs
3/6) implemented as the roof_deep legs' CONFIDENCE RECIPROCAL — every
roof_deep history row rides xRF_ROOF_CONF_SCALE (0.78) unless its own
breach pct sits inside the measured strong zone RF_ROOF_CONF_ZONE
[2, 5]% ("very confident": both studies' sweet band; the >5% blow-off
stays reduced — mixed evidence, donchian plain pool -2.08 @22% hit).
Per-trigger on the positive trading rows only (the run_cells
conf_scale_fn hook), the floor legs untouched at 1.0, the constants
documented on every roof_deep rule (rule_params.confidence.
side_scale). Measured: the zone scale IMPROVES the confidence
alignment (lift 1.596 vs 1.593 baseline, wasted mass 0.330 -> 0.327)
— the flat-only and blow-off-tier variants measured worse or
order-blind and were rejected.

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
and the simultaneous new 252d high are different facts), and the
first-build A/B measured the carve-out variant STARVING the mid
windows (the 252d cells lost their strongest days to the 500d claim
and fell below the gate floor — the 2024-09-30 events vanished with
them), so every window keeps its own full population and the gate
decides. The trigger day's own level never sets its own bar (the
level is shifted one row on the code's own rows). REGIME-FREE with a
trend split (the ma_cross precedent — per-regime cells measured thin
and sign-flippy, and the studies' starving corners were regime-corner
artifacts). The ohlcv boost opts in (the turnover-climax axis widens
the trust-weighted population).

THE SHARED VARIANT PASSES ran here 2026-10-26..2026-10-31 (the deep
floor legs in BOTH the PE-EXTREMES and the turnover-climax variants,
the roof deep leg in the OHLCV variant, the floor bounce in the PE
variant — separate row sets on the is_pe_boosted / is_ohlcv_boosted PK
axes) and were UNLINKED 2026-11-01 (user-directed "keep the pe and
ohlcv boosted logic in py, however, offload to unlink from the
computation ... delete is pe and ohlcv boosted col as from pk"): the
variant axes left the DB PKs and this engine ships the PLAIN
population only. The gate logic stays dormant in engines/_pe_boost /
engines/_ohlcv_boost for a future re-link.
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import grouped_rolling_agg, grouped_shift

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.trends import REENTRY_EXIT_DAYS
from analysis.analysis_signals.fetch.inputs import fetch_rf_inputs

# the breach depth normalizer: 2% beyond the level = depth 1.0 (the
# confidence scale's unit, capped at 2). PERCENT units matching the
# breach pct column (br): a 2.0 (% beyond the level) breach reads
# depth 1.0. FIXED 2026-11-18 (user-approved; caution-logged the same
# day): the constant was the FRACTION 0.02 while br is PERCENT — every
# breach read depth ~50-100x and CellGate's cap pinned the confidence
# amplification at its maximum 2.0 on every row, voiding the
# deeper-pierce-stronger-claim gradient (verification: the rebuilt
# confidences must match base x (1 + 0.5 x min(br/2, 2)) x scales
# exactly).
_RF_DEPTH_UNIT = 2.0

# the estimates-table level prefix per config method infix (donchian
# reads roof{W}/floor{W} columns, median reads mroof{W}/mfloor{W})
_METHOD_PREFIX = {"": "", "_med": "m"}


def _roof_conf_scale(v: float) -> float:
    """The roof_deep legs' per-trigger SIDE scale (2026-11-18, the
    floor-bias study's product step — config RF_ROOF_CONF_SCALE /
    RF_ROOF_CONF_ZONE): every roof breach rides the REDUCED
    confidence unless it is VERY CONFIDENT — the breach pct ``v``
    (the trigger's own value, pct beyond the prior day's roof) sits
    inside the measured strong zone [2, 5]%. Tiny pure helper (the
    run_cells conf_scale_fn closure body)."""
    lo, hi = cfg.RF_ROOF_CONF_ZONE
    return 1.0 if lo <= v <= hi else cfg.RF_ROOF_CONF_SCALE


def _persist_mask(frame: pd.DataFrame, br: pd.Series) -> pd.Series:
    """The N-day PERSISTENCE mask (2026-11-18, user-directed "multiple
    days remained exceeded the bands"): the breach pct ``br`` > 0 on
    each of the last RF_BREAK_PERSIST_DAYS CONSECUTIVE traded rows —
    the confirmation day is the run's Nth day. Whole-frame grouped
    shifts of the RAW 0/1 breach indicator multiplied together (int64,
    cuDF-native; warmup NaN reads 0 = not persisted). Tiny pure
    helper."""
    raw = (br > 0.0).fillna(False).astype("int64")
    run = raw
    for d in range(1, cfg.RF_BREAK_PERSIST_DAYS):
        prev = grouped_shift(
            frame.assign(_b=raw), ["code"], ["_b"],
            out_names=[f"_bp{d}"], periods=d, sort=False,
        )[f"_bp{d}"]
        run = run * prev.fillna(0).astype("int64")
    return run > 0


def _trend_gate(frame: pd.DataFrame, side: str) -> pd.Series:
    """The direction-matched TREND gate (2026-11-18, user-directed
    "the upward/downward trend maintained"): the code's own trend
    state must agree with the break side at the trigger day — roof
    legs fire only on rise/top (the upward structure maintained),
    floor legs only on fall/bottom. Tiny pure helper."""
    return frame["trend"].isin(cfg.RF_BREAK_TREND_GATE[side])


def _run_day_move_max(frame: pd.DataFrame) -> pd.Series:
    """The max ONE-DAY rise over the confirmation window (2026-11-18,
    the roof gap guard — config RF_BREAK_GAP_BAR_PCT): the directional
    close-to-close return, rolled max over the last
    RF_BREAK_PERSIST_DAYS traded rows. Tiny pure helper."""
    prev = grouped_shift(
        frame, ["code"], ["price"], out_names=["_pc"], periods=1,
        sort=False,
    )["_pc"]
    day_mv = (frame["price"] / prev - 1.0) * 100.0
    frame["_dm"] = day_mv
    return grouped_rolling_agg(
        frame, ["code"], "_dm", cfg.RF_BREAK_PERSIST_DAYS,
        min_periods=1, agg="max", sort=False,
    )


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
                # the 2026-11-18 confirmation semantics: persistence of
                # the band breach (N consecutive days beyond THIS
                # window's level) + the direction-matched trend state,
                # shared by the window's candidate grid; the ROOF gap
                # guard (same day) additionally stops the trigger when
                # one day's rise over the window is too large — the day
                # opportunity is lost (config RF_BREAK_GAP_BAR_PCT,
                # measured roof-only: the floor's big-drop days ARE the
                # washout edge)
                run_mv_max = _run_day_move_max(frame) if side == "roof" \
                    else None
                for w in cfg.RF_BREAK_WINDOWS:
                    br = breach[(infix, side, w)]
                    col = f"_rf{p}{w}_{side}_pct"
                    frame[col] = br
                    persist = _persist_mask(frame, br)
                    trend_ok = _trend_gate(frame, side)
                    gap_ok = (run_mv_max < cfg.RF_BREAK_GAP_BAR_PCT) \
                        .fillna(False) if run_mv_max is not None \
                        else None
                    # the window's SHARED conditions (the candidate's
                    # own depth bar rides per-candidate below)
                    shared = persist & trend_ok
                    if gap_ok is not None:
                        shared = shared & gap_ok
                    cands = [
                        Candidate(
                            key=f"{p}{side}{w}_deep{k:g}",
                            mask=(shared & (br > k)).fillna(False),
                            reference=0.0,
                            depth=(br / _RF_DEPTH_UNIT)
                            .clip(lower=0.0),
                            params={
                                "kind": f"{side}_break_deep",
                                "window": w,
                                "method": method_label,
                                "min_breach_pct": k,
                                "persist_days":
                                    cfg.RF_BREAK_PERSIST_DAYS,
                                "trend_gate": list(
                                    cfg.RF_BREAK_TREND_GATE[side]),
                                **({
                                    "gap_guard_pct":
                                        cfg.RF_BREAK_GAP_BAR_PCT,
                                } if side == "roof" else {}),
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
                                    "persist_days":
                                        cfg.RF_BREAK_PERSIST_DAYS,
                                    "trend_gate": list(
                                        cfg.RF_BREAK_TREND_GATE[side]),
                                    **({
                                        "gap_guard": {
                                            "day_move_bar_pct":
                                                cfg.RF_BREAK_GAP_BAR_PCT,
                                            "window_days":
                                                cfg.RF_BREAK_PERSIST_DAYS,
                                        },
                                    } if side == "roof" else {}),
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
                    if side == "roof":
                        call["conf_scale_fn"] = _roof_conf_scale
                        call["confidence_params"] = {
                            "side_scale": {
                                "kind": "roof_zone",
                                "reduced": cfg.RF_ROOF_CONF_SCALE,
                                "full_zone_pct": list(
                                    cfg.RF_ROOF_CONF_ZONE),
                                "note": "roof reduced unless very "
                                        "confident (the floor-bias "
                                        "study's 2026-11-18 roof "
                                        "scale)",
                            },
                        }
                    r, h = self.run_cells(
                        frame, f"rf{w}{infix}_{side}_deep", "buy",
                        cands, **call,
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
            # the same 2026-11-18 confirmation semantics: the MEDIAN
            # floor breach persisted N consecutive days (the dip
            # remained below the consensus band), the wick-intact guard
            # + the downward trend state on the confirmation day
            persist = _persist_mask(frame, med_br)
            trend_ok = _trend_gate(frame, "floor")
            mask = ((persist & (med_br > 0.0) & (don_br <= 0.0)
                     & trend_ok)).fillna(False)
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
                    "persist_days": cfg.RF_BREAK_PERSIST_DAYS,
                    "trend_gate": list(cfg.RF_BREAK_TREND_GATE["floor"]),
                    "bar_def": {
                        "kind": "prior_day_level",
                        "level": f"mfloor{w}",
                        "method": "median",
                        "method_code": 5,
                        "source": "analysis.mov_ave_spreads_"
                                  "roof_floor_estimates",
                        "shift": 1,
                        "persist_days": cfg.RF_BREAK_PERSIST_DAYS,
                        "trend_gate": list(
                            cfg.RF_BREAK_TREND_GATE["floor"]),
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
        return rules, history
