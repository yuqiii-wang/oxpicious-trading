"""hedge_pairs — the long–long anti-correlated pair family
(analysis.analysis_signals.engines.signal_families.hedge_pairs.engine).

The 2026-10-18 online hedge-portfolio study's pair-native discoveries,
REWORKED three times the same day — market-decoupling, three-
component, sourcing — and MERGED into the TWO-LAYER design:

  THE SOURCE LAYER (the existing rsi signal rules): each component —
  the broad market + every leg industry — ships its own Wilder-RSI
  percentile-tail rule rows at its own code under signal_type 0
  (mov_rsi) with the mov_rsi sub-type vocabulary (rsi14_{1,2,3}pct,
  BOTH sides: the capitulation buys and the exhaustion sells, nested
  deepest-claim carving, the mov_rsi bar_def shape — measured on the
  component's own response, exactly the Short-Term-Tech page's
  grammar). These are the SIGNALS the portfolio coordinates; the
  BUY side is the ungated info feed, the SELL side a gated typical
  cell. History stays per-code info (dropped) — the recorded events
  are the orchestrated composites'.

  THE ORCHESTRATION LAYER (signal_type hp_rsi, 2026-10-18): the
  hedge pairs layer takes the sourced rsi signals and coordinates
  them into the portfolio rules — stamped under the DEDICATED
  hp_rsi type at the pair codes with the hp_* sub types, and each
  row's portfolio_signal_rules group REFERENCES the sourced mov_rsi
  rule ids it orchestrates:

    hp_entry_legs_rsi (BUY, response = the inverse-vol long–long
      portfolio): the COMPOSITE = the three components' capitulation
      signals PAIRED within ~5 traded days (all three witnesses
      alive; the trigger fires the day the trio completes). The
      tail pct grid {1, 2, 3}% is the run_cells candidate set. The
      market rides as a PEER vote (one of three), never a gate.

    hp_exit_mkt_high (SELL, response = the portfolio): the broad
      market's OWN exhaustion (RSI above its adaptive high tail,
      ~5d witness) coordinated at the portfolio level — the group
      references the market's sourced rsi14_{tail}pct SELL row.

    hp_catchup_rsi_hi (SELL, response = the LAGGARD leg): harvest
      leg 2 — the laggard (side = sign(ratio_z)) reaches its OWN
      exhaustion (RSI above its per-component adaptive HIGH tail)
      while the ratio still favors the other leg; the group
      references both legs' sourced rsi14_{tail}pct SELL rows.

  THE PAIR-NATIVE LAYER (signal_type hedge_pairs): the exits with no
  existing-family counterpart keep the hedge_pairs type — the
  volatility-harvest ratio trim (ONE sub hp_harvest: |ratio z| >= k
  sells the relatively HIGH leg, the k grid {1.5, 2.0} the run_cells
  candidate set — the per-cell winner picks the bar; sell correctness
  = the strong leg's forward blend <= 0) and the hedge-regime break
  stop (offset corr >= 0 within 60d of a hedgeable <= -0.30 reading).
  The 2026-10-21 DEDUP (user-directed, same-semantics subs collapse
  to the one most semantic): the staged hp_harvest_z15/z20 pair
  merged into hp_harvest (the bar is a candidate, not an identity),
  the per-window washout trio merged into ONE hp_entry_wash (the W
  window the run_cells candidate set — the recorded value the fetch's
  combined washdepth gauge, the deepest alive witness's window), and
  the observational hp_regime_flip retired — hp_entry_rho50 IS the
  hedgeable turn-on's one record (its deep gated trading anchor; the
  shallow ungated twin added nothing the deep anchor's cells
  didn't already measure).

  THE MORE-TRIGGERS GRADUATES (2026-10-19, docs/
  hedge_pairs_more_triggers_study_2026-10-19.md — the measured study;
  entry-side events grow ~15x at pooled t >= 2.4, nearly disjoint
  from the trio and from each other):

    hp_entry_wash (BUY, hedge_pairs; ONE sub since the 2026-10-21
      dedup — the per-window trio merged, the window grid
      {60,120,255} the run_cells candidate set, each window its OWN
      population): both legs' W-day Donchian-floor breakdown
      witnesses alive within the pairing window — the co-drawdown on
      a STRUCTURAL bar.
    hp_entry_wash120_mkt (BUY, hp_rsi): the wash PLUS the market's
      RSI-3pct capitulation witness — the RSI trio's structural
      cousin, the study's strongest graduate (+2.58% t=8.94, forward
      worst-trough -1.79% vs the -3.44% baseline); its portfolio
      group references the trio's sourced rsi BUY rows at the
      market-witness tail.
    hp_entry_rho50 (BUY, hedge_pairs): the offset corr's first day
      <= -0.50 — the deep hedgeable turn-on, buying the pair when
      the covariance cancellation works hardest (the grid measured
      -0.40 flat / -0.50 the bar / -0.60 weaker).
    hp_entry_catchup_reclaim60 (BUY the PAIR, hedge_pairs): the
      weak-side leg's first close back above its own MA60 — the
      ma_cross reclaim in coordination form, the ENTRY mirror of
      hp_catchup_rsi_hi (reclaim in, RSI-high out); response = the
      PORTFOLIO (the px_lag switching path measured negative — it
      abandons the reclaimed leg at the ratio's zero-cross).

  THE MOMENTUM TILT (2026-10-20, docs/
  hedge_pairs_spread_rotation_study_2026-10-20.md — the harvest's
  OPPOSITE buy/sell pair, the study's portfolio-first graduate): at
  the |ratio z| divergence the measured drift is CONTINUATION, not
  reversion — the strong leg keeps outperforming (fwd +0.37..+0.55%
  across the k grid) and the toward-strong tilt is the only one that
  improved BOTH lenses (dd +0.16..+0.90pp shallower on 62-67% of
  events; the mean-reversion direction the user proposed measured
  -1.24% per open under every stop rule and was NOT shipped). Two
  PAIR-NATIVE cells on the ONE trigger — no sourced short-term-tech
  references, no portfolio cascade (user-directed):

    hp_mom_strong (BUY, response = the strong leg): gates on the
      strong leg's own forward blend (the buy bar + the pooled tier).
    hp_mom_lag (SELL, response = the laggard leg): the pair's
      opposite action, gated on the exit-timing bar (sells are never
      pooled-gated).

  The k grid {1.5, 2.0, 2.5} is the run_cells candidate set (the
  per-cell winner picks the bar); the trigger and the response leg
  both follow sign(ratio_z) same-day (the px_strong/px_lag
  convention).

  THE DRAWDOWN LENS (the family's coordination aim — max drawdown the
  negative signal): _stamp_dd stamps every hedge rule row's
  _performance.dd block {min20_pct, p10_pct, baseline_pct, n} — the
  cell's triggers' forward worst-trough within 20d vs the response
  path's all-days baseline — from the fetch's ddmin20_* gauges; the
  recorded evidence the study's gate proposal reads (the dir_ave bar
  stays the verdict; the dd block is the coordination evidence).

  THE PORTFOLIO-TIER STAMPS (2026-10-20, convention-aligned to the
  signal_rules rule_params grammar; re-shaped the same day's
  cell-level-signals-in-portfolio redesign): _stamp_portfolio stamps
  the ORCHESTRATING rows (every composite / pair-native rule row —
  the components' sourced rows carry none) with the two payloads
  run.py peels beside _performance, ONE PORTFOLIO ROW PER PAIR CODE
  (the sub_type axis was dropped — run.py merges the per-sub
  fragments):

    _portfolio_rule_params  the group's CONSTANT construction
      (portfolio_signal_rules.rule_params, the signal_rules GRAMMAR)
      as a PER-SUB FRAGMENT: the shared top-level keys (the
      portfolio-level axis, universe / pair_weighting, the gate bars,
      the confidence CASCADE scale, episode_merge_days) + the sub's
      own spec under "sub" (sub_axis / response / coordination /
      observational) — run.py folds the shared block once per pair
      and nests each fragment's spec under rule_params.subs[<sub
      label>].
    _portfolio_performance  the dated PORTFOLIO AUDIT fragments
      (history_portfolio_signal_performance): WHAT THE PORTFOLIO
      TOOK — benefit kind max_drawdown_reduction with the pair's
      max_dd block (_max_dd_block: the inverse-vol pair value's own
      max drawdown vs each leg's standalone close, as reduction
      percentage points), the gate VERDICT digest {metric, min_pp,
      passed, conf_scale}, the ORCHESTRATING cell's own drawdown
      lens as the ``dd`` fragment (run.py combines the fragments'
      lenses at the portfolio level), and the fragment's
      group_is_valid (its orchestrator's verdict AND the portfolio
      gate; the merged portfolio row ORs them).

  THE GATED CONFIDENCE CASCADE (2026-10-20, user-directed; re-homed
  by the same day's redesign): the portfolio tier's confidence ADDS
  to the member cells' as an INCREASE, gated — the src data TOGETHER
  must pass the PORTFOLIO GATE (the audited drawdown reduction vs
  the MEAN leg >= PF_GATE_MIN_PP — the worst-leg comparison is
  nearly automatic for any blend) AND the orchestrating composite
  cell's own is_valid (the dir_ave verdict on the coordination
  itself). The scale = 1 + PF_CONF_FACTOR x min(benefit_pp /
  PF_CONF_UNIT_PP, PF_CONF_CAP) rides the portfolio snapshots' gate
  digest (conf_scale) and the live tier applies it to the member
  cells it flags (signal_rules.is_in_portfolio via the
  portfolio_signal_rules ids arrays; is_valid groups only) beside
  the starvation sample scale — an INCREASE ONLY (a failed gate
  never discounts a member's own confidence; the retired per-cell
  portfolio_cascade stamp is gone with its column).

  Cells POOL on both axes (the sparse-event options precedent: trend 4
  + regime 4; entries and extremes are rare, per-(trend, regime)
  corners would starve). The OHLCV boost is off — pair composites
  carry no day-0 turnover anatomy.

  THE PORTFOLIO EVENT MEMBERS (2026-10-21, portfolio_history_signals):
  every composite history row is a PORTFOLIO EVENT with its event date
  and a member association — ``_portfolio_event_members``, the list of
  SOURCED COMPONENT CELL EVENTS that triggered it, stamped run.py
  peels into analysis_signals.portfolio_history_signals.member_signals
  (the component cells' own history stays dropped from history_signals
  — these member records are the one place the triggering cell events
  are kept). Each member is the component's LATEST episode-first
  trigger ACROSS ITS TAIL GRID at-or-before the event day — the alive
  capitulation/exhaustion's ACTIVE claim (the composite's witness is
  the RAW tail breach, which stays lit while the component sits in its
  DEEPER tail; those deeper days carve away from the shallower cell's
  episodes, so a per-tail lookup would point at a stale episode) —
  and each member keeps its OWN sub and date, so one event's members
  can be DIFFERENT SUB SIGNALS on DIFFERENT NEARBY DAYS (the trio:
  the market's RSI capitulation recently + leg A yesterday + leg B
  today). Resolution per composite sub: the RSI trio entry → the three
  components' BUY events (market + both legs), the market-high exit →
  the market's SELL event, the catch-up sell → the LAGGARD leg's SELL
  event (sign(ratio_z) same-day), the wash+market trio → the market's
  RSI BUY witness; the PAIR-NATIVE subs (harvest / momentum tilt /
  wash / rho50 / reclaim / stop) coordinate no sourced components —
  empty members.

Portfolio grouping (run.py): the sourced component rows join the
orchestrating portfolio via pair-code tags stamped in-engine AFTER
each composite call names its WINNING tail — the entry composites
claim the winning tail's THREE component BUY ids, the market-high
exit the market's winning-tail SELL id, the catch-up both legs'
winning-tail SELL ids (best-effort: a sourced id joins only when
that component cell shipped — the sells are gated); the pair-native
rows default to their own membership. ONE PORTFOLIO ROW PER PAIR
CODE (2026-10-20): all of a pair's member ids ride the single
signal_rule_ids array, and those member cells' is_in_portfolio flag
is TRUE (the orchestrating composites' is FALSE — the group IS the
composite). Two entry classes run under the ONE registry label
(hedge_pairs): HedgeRsiEntry stamps the hp_rsi orchestration (+ the
sourced type-0 component rows), HedgePairEntry the pair-native
hedge_pairs rules — one shared prepared frame.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from _common.df_utils import (
    column_subset,
    grouped_rolling_agg,
    host_array,
    safe_columns,
)
from _common.labels import SIGNAL_TYPE_CODE

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.analysis_signals.fetch.inputs import (
    fetch_hedge_pair_inputs,
    parse_pair_code,
)
from analysis.trends import REENTRY_EXIT_DAYS

# the mov_rsi family's depth scale (0.5 confidence factor, 10 RSI
# points per unit of normalized breach depth) — the components' and
# composite's gauges ARE the Short-Term-Tech RSI tails
_DEPTH_FACTOR = 0.5
_RSI_DEPTH_SCALE = 10.0
_RSI_COL = f"rsi_{cfg.HP_LEG_RSI_WINDOW}days"
# the component rows are REAL mov_rsi rows: signal_type 0 with the
# mov_rsi sub-type vocabulary (rsi{window}_{tail}pct, both sides)
_RSI_FAMILY_CODE = 0
# the portfolio rule_params grammar's shared values (2026-10-20 — the
# signal_rules rule_params convention: the SAME values the member
# cells' params carry, built from the SAME config constants):
# FAMILY_PORTFOLIO_TYPE's registry value for this family (the axis
# prefix — the portfolio_type label where signal_rules carries the
# family label), the universe/weighting strings verbatim the cells'
# extra_params
_PORTFOLIO_TYPE_LABEL = "neg_corr_hedge_pair"
_UNIVERSE = ("offset-opposite industry pairs "
             "(analysis_composites offsets, 255d)")
_PAIR_WEIGHTING = f"inverse-vol {cfg.HP_VOL_WINDOW}d, lagged one row"

# the component frame hand-off between fetch_frame and run():
# run_family prepares the frame on entry0 but RUNS a fresh instance
# (entries are constructed per run loop), so an instance attribute
# would be None in the runner — keyed by the code scope (one live
# scope per process)
_COMP_CACHE: dict[tuple[str, ...], "pd.DataFrame | None"] = {}


def _rsi_sub(pct: int) -> str:
    """The mov_rsi sub-type label for one tail (rsi14_1pct ...)."""
    return f"rsi{cfg.HP_LEG_RSI_WINDOW}_{pct}pct"


def _max_dd_block(frame: pd.DataFrame) -> dict[str, dict]:
    """The PORTFOLIO CONSTRUCTION's drawdown benefit per pair code —
    the max_drawdown_reduction audit's ``max_dd`` block
    (history_portfolio_signal_performance.benefit, 2026-10-20): the
    inverse-vol pair VALUE path's own max drawdown vs each LEG's
    standalone close path — what the coordination took that an
    undiversified holder of either leg would have eaten. Whole-frame
    grouped running-max (ONE grouped_rolling_agg pass per column over
    the (code, date)-sorted pair frame); the per-code min lands on the
    host as a small {code: block} map (the sanctioned small-result
    boundary)."""
    cols = {"portfolio": "price", "leg_a": "px_a", "leg_b": "px_b"}
    if len(column_subset(frame, list(cols.values()))) < len(cols):
        return {}
    # window = the whole frame: every group's running max (cummax by
    # rolling-max, min_periods 1 so warmup rows count as they come)
    window = len(frame)
    mdd: dict[str, dict[str, float]] = {}
    for name, col in cols.items():
        run_max = grouped_rolling_agg(frame, "code", col, window,
                                      min_periods=1, agg="max")
        dd = frame[col] / run_max - 1.0
        per_code = dd.groupby(frame["code"]).min()
        codes_np = host_array(per_code.index)
        vals_np = host_array(per_code.values).astype("float64")
        for c, v in zip(codes_np.tolist(), vals_np.tolist()):
            if np.isfinite(v):
                mdd.setdefault(str(c), {})[name] = float(v) * 100.0
    out: dict[str, dict] = {}
    for code, d in mdd.items():
        port, a, b = (d.get("portfolio"), d.get("leg_a"), d.get("leg_b"))
        if port is None or a is None or b is None:
            continue
        worst, mean = min(a, b), (a + b) / 2.0
        out[code] = {
            "window": "full_history",
            "portfolio_pct": round(port, 4),
            "leg_a_pct": round(a, 4),
            "leg_b_pct": round(b, 4),
            "worst_leg_pct": round(worst, 4),
            "mean_leg_pct": round(mean, 4),
            # percentage POINTS the portfolio's max drawdown sits
            # SHALLOWER than the legs' (drawdowns are negative pcts,
            # so the depth reduction = portfolio − leg in signed
            # space); positive = the hedge paid (the audited benefit)
            "reduction_vs_worst_pct": round(port - worst, 4),
            "reduction_vs_mean_pct": round(port - mean, 4),
        }
    return out


def _portfolio_params(code: str, sub_label: str, kind: str,
                      coord_params: dict, response: str, k: int,
                      observational: bool = False) -> dict:
    """The CONSTANT PORTFOLIO rule definition's PER-SUB FRAGMENT
    (portfolio_signal_rules.rule_params, 2026-10-20's cell-level
    redesign — run.py MERGES the fragments into ONE portfolio-level
    params per pair code): the shared top-level keys carry the SAME
    values the member cells' params carry (universe / pair_weighting /
    episode_merge_days), the axis is the PORTFOLIO-level label, the
    gate BAR + confidence CASCADE blocks are the signal_rules
    conventions, and the sub's own spec rides the ``sub`` key
    (sub_axis / response / coordination / observational) — run.py
    folds it under rule_params.subs[<sub label>]. Engine-generated on
    the ORCHESTRATING row — the sourced component rows carry none
    (the orchestrator names the portfolio)."""
    a, b = parse_pair_code(code)
    params: dict = {
        "axis": _PORTFOLIO_TYPE_LABEL,
        "universe": _UNIVERSE,
        "pair_weighting": _PAIR_WEIGHTING,
        # the PORTFOLIO GATE bars (the signal_rules gate convention:
        # constants here, the verdict digest on the dated
        # history_portfolio_signal_performance snapshots)
        "gate": {
            "metric": cfg.PF_GATE_METRIC,
            "min_pp": cfg.PF_GATE_MIN_PP,
        },
        # the CONFIDENCE CASCADE scale (the signal_rules confidence
        # convention: the SCALE constants — the member cells' boost is
        # 1 + factor x min(benefit_pp / unit_pp, cap), applied ONLY
        # when the group passes its gate + entry rules)
        "confidence": {
            "factor": cfg.PF_CONF_FACTOR,
            "unit_pp": cfg.PF_CONF_UNIT_PP,
            "cap": cfg.PF_CONF_CAP,
        },
        "portfolio": {
            "legs": [a, b],
            "market": cfg.HP_MARKET_CODE,
            "aim": "max_drawdown_reduction",
        },
        # the sub's OWN spec — run.py nests it under subs[sub_label]
        "sub": {
            "axis": f"{_PORTFOLIO_TYPE_LABEL}:{sub_label}",
            "response": response,
            "coordination": {"kind": kind, **coord_params},
        },
        "episode_merge_days": k,
    }
    if observational:
        params["sub"]["observational"] = True
    return params


def _cascade_gate(max_dd: dict | None) -> tuple[bool, float, float | None]:
    """The PORTFOLIO GATE verdict + the CONFIDENCE CASCADE scale for
    one pair code's audited ``max_dd`` block: passed iff the drawdown
    reduction vs the MEAN leg clears PF_GATE_MIN_PP (the worst-leg
    comparison is nearly automatic for any two-asset blend — the mean
    is the honest bar); the scale is the cell depth-scale formula on
    the benefit depth, an INCREASE only (1.0 when the gate fails —
    the portfolio tier never discounts a member's confidence)."""
    benefit = (max_dd or {}).get("reduction_vs_mean_pct")
    if benefit is None or not np.isfinite(benefit):
        return False, 1.0, None
    if benefit < cfg.PF_GATE_MIN_PP:
        return False, 1.0, float(benefit)
    depth = min(benefit / cfg.PF_CONF_UNIT_PP, cfg.PF_CONF_CAP)
    return True, round(1.0 + cfg.PF_CONF_FACTOR * depth, 4), float(benefit)


def _stamp_portfolio(rules_rows: list[dict], mdd: dict[str, dict],
                     as_of: date, sub_label: str, kind: str,
                     coord_params: dict, response: str, k: int,
                     observational: bool = False) -> None:
    """Stamp the ORCHESTRATING rows' PORTFOLIO payloads (run.py peels
    both): ``_portfolio_rule_params`` — the portfolio construction's
    PER-SUB FRAGMENT (portfolio_signal_rules.rule_params, the
    signal_rules grammar incl. the gate bars + cascade scale; run.py
    merges the fragments per pair code) — and
    ``_portfolio_performance`` — the dated PORTFOLIO AUDIT fragment
    (history_portfolio_signal_performance): benefit kind
    max_drawdown_reduction with the pair's ``max_dd`` block, the
    orchestrating cell's own drawdown lens ``dd`` as the fragment's
    payload key (run.py combines the fragments' lenses at the
    portfolio level), the gate VERDICT digest {metric, min_pp,
    passed, conf_scale} (the bars ride the snapshot so config changes
    never rewrite history), and the fragment's group_is_valid
    (orchestrator is_valid AND the portfolio gate — the ENTRY RULES +
    PORTFOLIO GATE conjunction; the merged portfolio row ORs the
    fragments')."""
    for row in rules_rows:
        code = row["code"]
        row["_portfolio_rule_params"] = _portfolio_params(
            code, sub_label, kind, coord_params, response, k,
            observational)
        passed, scale, benefit = _cascade_gate(mdd.get(code))
        # the GATING conjunction: the portfolio gate (the audited
        # benefit) AND the entry rules (the orchestrating cell's own
        # dir_ave verdict) — the members see the cascade only when
        # BOTH hold; hp_rsi composites pass is_valid=False when
        # ungated
        group_valid = bool(row["is_valid"]) and passed
        row["_portfolio_performance"] = {
            "date": as_of,
            "benefit": {
                "kind": "max_drawdown_reduction",
                "max_dd": mdd.get(code),
            },
            "dd": row["_performance"].get("dd"),
            "gate": {
                "metric": cfg.PF_GATE_METRIC,
                "min_pp": cfg.PF_GATE_MIN_PP,
                "passed": passed,
                "conf_scale": scale,
            },
            "group_is_valid": group_valid,
            # the orchestrating row's own rule_entry column (run_cells
            # stamps it) — the portfolio's writing entry
            "rule_entry": row["rule_entry"],
        }


def _stamp_dd(rules_rows: list[dict], history_rows: list[dict],
              frame: pd.DataFrame, response_col: str) -> None:
    """Stamp the DRAWDOWN LENS evidence block on every rule row's
    _performance payload (the family's coordination aim — max
    drawdown the negative signal; docs/
    hedge_pairs_more_triggers_study_2026-10-19.md): the cell's
    measurable triggers' forward worst-trough within 20d (mean + the
    p10 tail, the CDaR-style reading) vs the response path's all-days
    pooled baseline, from the fetch's ddmin20_{response_col} gauge.
    Stamps None where the gauge or the measurable population is
    absent — the KEY is always set (the run's performance rows share
    one COPY column list). Host boundary: ONE host_array unwrap per
    column, per-code slices through the frame's (code, date) sort."""
    dd_col = f"ddmin20_{response_col}"
    ok_col = RuleEntry._blend_col_names(response_col)[1]
    if dd_col not in safe_columns(frame) or not rules_rows:
        for row in rules_rows:
            row["_performance"]["dd"] = None
        return
    codes_np = host_array(frame["code"]).astype(object)
    dates_np = host_array(frame["date"]).astype("datetime64[D]")
    dd_np = host_array(frame[dd_col]).astype(np.float64)
    ok_np = host_array(frame[ok_col]).astype(bool)
    ok_dd = dd_np[ok_np & np.isfinite(dd_np)]
    baseline = float(ok_dd.mean()) if ok_dd.size else None
    hist_dates: dict[str, list] = {}
    for h in history_rows:
        hist_dates.setdefault(h["code"], []).append(
            np.datetime64(h["date"], "D"))
    for row in rules_rows:
        code = row["code"]
        lo = int(np.searchsorted(codes_np, code, side="left"))
        hi = int(np.searchsorted(codes_np, code, side="right"))
        slice_dates = dates_np[lo:hi]
        vals: list[float] = []
        for d in hist_dates.get(code, []):
            pos = int(np.searchsorted(slice_dates, d))
            if pos < slice_dates.size and slice_dates[pos] == d:
                i = lo + pos
                if ok_np[i] and np.isfinite(dd_np[i]):
                    vals.append(float(dd_np[i]))
        if len(vals) >= 1:
            v = np.asarray(vals, dtype=np.float64)
            row["_performance"]["dd"] = {
                "min20_pct": round(float(v.mean()) * 100.0, 4),
                "p10_pct": round(float(np.percentile(v, 10.0)) * 100.0, 4)
                if v.size >= 3 else None,
                "baseline_pct": round(baseline * 100.0, 4)
                if baseline is not None else None,
                "n": int(v.size),
            }
        else:
            row["_performance"]["dd"] = None


# the PORTFOLIO EVENT members' index shape (2026-10-21): one component
# side's episode-first events ACROSS THE TAIL GRID (the members'
# resolution is per component, the alive claim's own sub rides the
# record), (component code, side) -> ascending (date, history row)
_CompEvents = dict[tuple[str, str], list[tuple[date, dict]]]


def _member_record(h: dict) -> dict:
    """The member_signals payload element for one component history
    row — the cell-level signal record WITH ITS OWN DATE (different
    subs, different nearby days): label codes stay codes (the API
    boundary decodes), the date is the component's own trigger day."""
    return {
        "code": h["code"],
        "signal_type": int(h["signal_type"]),
        "signal_sub_type": int(h["signal_sub_type"]),
        "action": int(h["action"]),
        "date": h["date"].isoformat(),
        "signal": h["signal"],
        "signal_reference": h["signal_reference"],
        "confidence": int(h["confidence"]),
    }


def _latest_member(events: list[tuple[date, dict]], day: date) -> dict | None:
    """The component's LATEST episode-first event at-or-before the
    event day — the alive episode the composite's witness saw; None
    when the component shipped no cell by that day (the sell sides are
    gated — best-effort, the group-join convention). The lists are
    tiny (one component side's episodes); a reverse scan is the
    clearest form."""
    for d, h in reversed(events):
        if d <= day:
            return _member_record(h)
    return None


class _ComponentRsiEntry(RuleEntry):
    """The components' runner SHIM: stamps the per-code rows as REAL
    mov_rsi cells (FAMILY mov_rsi → signal_type 0, the mov_rsi
    sub-type vocabulary — the SOURCE layer's typical Short-Term-Tech
    tier) while reusing the shared run_cells machinery. Never fetched
    or run directly — the hedge entries call its run_cells over the
    component frame (run_cells stamps signal_type / rule_entry from
    the ENTRY instance, which is why the shim exists at all)."""

    FAMILY = "mov_rsi"
    PACKAGE = "mov_rsi"
    SUB_TYPES = ()
    REGIME_FREE = True
    USES_TREND_SPLIT = False
    OHLCV_BOOST = False
    WRITES_TREND_DAYS = False

    async def fetch_frame(self) -> pd.DataFrame:
        raise NotImplementedError(
            "the shim only lends its run_cells stamps — never fetched")

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        raise NotImplementedError(
            "the shim only lends its run_cells stamps — never run")


class HedgeRsiEntry(RuleEntry):
    """The RSI-SOURCED ORCHESTRATION entry (signal_type hp_rsi): the
    sourced type-0 component rows + the three portfolio composites
    (hp_entry_legs_rsi / hp_exit_mkt_high / hp_catchup_rsi_hi) at the
    pair codes, each group referencing the sourced mov_rsi rule ids
    it coordinates. Registry entry0 — owns the fetch and the wipe
    scopes of ALL THREE written types."""

    FAMILY = "hp_rsi"
    PACKAGE = "hedge_pairs"
    SUB_TYPES = (
        "hp_entry_legs_rsi",
        "hp_exit_mkt_high",
        "hp_catchup_rsi_hi",
        "hp_entry_wash120_mkt",
    )
    # sparse-event populations pool on both axes (options precedent)
    REGIME_FREE = True
    USES_TREND_SPLIT = False
    OHLCV_BOOST = False
    # composite 'A~B' pair ids are not securities — keep them out of
    # analysis.trend_days
    WRITES_TREND_DAYS = False
    # the response axes: the inverse-vol pair portfolio (entry/exit),
    # the laggard leg (catch-up) — declared here AND on the sibling
    # entry because prepare_frame attaches every declared column's
    # blend set ONCE for the shared frame
    FORWARD_PRICE_COLS = ("price", "px_strong", "px_lag")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # the COMPONENT frame (market + legs, own calendars) — filled
        # by fetch_frame (instance copy; the runner reads the module
        # cache — see _COMP_CACHE)
        self._comp_frame: pd.DataFrame | None = None

    def rule_wipe_scopes(self) -> list[tuple[int, list[str]]]:
        """This run writes THREE signal_types at two code scopes: the
        hp_rsi orchestration + the pair-native hedge_pairs rules (both
        at the pair codes) AND the sourced mov_rsi component rows
        (signal_type 0, at the market + leg-industry codes) — the
        wipe covers all of them (the sibling entry's rows included:
        run_family wipes through entry0 only)."""
        comp_codes = sorted(
            {cfg.HP_MARKET_CODE}
            | {leg for pair in self.codes for leg in parse_pair_code(pair)}
        )
        return [
            (self.signal_type_code, self.codes),
            (SIGNAL_TYPE_CODE["hedge_pairs"], self.codes),
            (_RSI_FAMILY_CODE, comp_codes),
        ]

    async def fetch_frame(self) -> pd.DataFrame:
        pair_frame, comp = await fetch_hedge_pair_inputs(
            self.conn, self.sec_type, self.codes)
        if comp is not None and not comp.empty:
            # the sourced-signal cells' measurement basis: regimes +
            # the OWN-price forward blend (the mov_rsi response; no
            # trend attach — the family pools, the column is unused)
            comp = await self._attach_regimes(comp)
            self._attach_forward_for(comp, "price")
        self._comp_frame = comp
        _COMP_CACHE[tuple(sorted(self.codes))] = comp
        return pair_frame

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        k = REENTRY_EXIT_DAYS
        common = {"k": k, "depth_factor": _DEPTH_FACTOR}
        # the PORTFOLIO-tier stamps' shared basis: the pair frame's
        # per-code drawdown-benefit blocks + the snapshot as-of date
        # (ONE grouped pass + ONE horizon unwrap per entry run)
        mdd = _max_dd_block(frame)
        as_of = self._performance_as_of(frame)

        # -- THE SOURCE LAYER: the per-code components (REAL mov_rsi
        #    rows — the existing rsi signal rules, signal_type 0,
        #    rsi14_{tail} sub types, BOTH sides with the nested
        #    deepest-claim carving, the mov_rsi bar_def shape).
        #    HISTORY IS DROPPED — per-code info; the recorded events
        #    are the ORCHESTRATED composites'. Group tags: buys join
        #    their pair's entry group AFTER the entry call names its
        #    winning tail; sells join the exit / catch-up groups the
        #    same way (below) -------------------------------------
        comp = self._comp_frame \
            if self._comp_frame is not None \
            else _COMP_CACHE.get(tuple(sorted(self.codes)))
        # (code, sub) -> row — the sourced-row lookups the composite
        # group joins resolve against
        comp_buy: dict[tuple[str, str], dict] = {}
        comp_sell: dict[tuple[str, str], dict] = {}
        # the PORTFOLIO EVENT members' index (2026-10-21): the
        # component cells' OWN episode-first history rows — dropped
        # from history_signals (per-code info) but kept HERE, indexed
        # per (component code, side) across the tail grid for the
        # composite events' member association
        # (portfolio_history_signals)
        comp_events: _CompEvents = {}
        if comp is not None and not comp.empty:
            # the components run through the mov_rsi SHIM so their
            # rows are stamped signal_type 0 (the SOURCE tier), not
            # the orchestration type
            comp_runner = _ComponentRsiEntry(
                self.conn, self.sec_type, self.codes, boost=False,
            )
            rsi = comp[_RSI_COL]
            deeper_buy = pd.Series(False, index=comp.index, dtype="bool")
            deeper_sell = pd.Series(False, index=comp.index, dtype="bool")
            for pct in sorted(cfg.HP_LEG_TAIL_PCTS):
                bar_lo = comp[f"lo_bar{pct}"]
                bar_hi = comp[f"hi_bar{pct}"]
                buy_deep = (rsi < bar_lo).fillna(False)
                sell_deep = (rsi > bar_hi).fillna(False)
                # the mov_rsi bar_def shape verbatim
                bar_def = {
                    "kind": "rolling_quantile",
                    "tail_pct": pct,
                    "window_days": cfg.HP_LEG_BAR_WINDOW_DAYS,
                    "shift": 1,
                }
                r_b, h_b = comp_runner.run_cells(
                    comp, _rsi_sub(pct), "buy",
                    [Candidate(
                        key=f"tail{pct}",
                        mask=buy_deep & ~deeper_buy,
                        reference=bar_lo,
                        depth=(bar_lo - rsi) / _RSI_DEPTH_SCALE,
                        params={
                            "bar_def": bar_def,
                            "response": "own_price",
                            "portfolio_role": "component",
                        },
                    )],
                    value_col=_RSI_COL,
                    # the BUY side is the portfolio's INFO feed —
                    # UNGATED (observational, evidence as-is, the
                    # VRP-twin precedent): every component with
                    # episodes ships its row, so every entry group
                    # references its full three ids; the COMPOSITE
                    # carries the trade validity. The sell side stays
                    # a typical gated cell.
                    ungated=True,
                    extra_params={
                        "window": cfg.HP_LEG_RSI_WINDOW,
                        "universe": "the portfolio components: the broad "
                                    "market + the pair legs (short-term-"
                                    "tech import)",
                    },
                    **common,
                )
                r_s, h_s = comp_runner.run_cells(
                    comp, _rsi_sub(pct), "sell",
                    [Candidate(
                        key=f"tail{pct}",
                        mask=sell_deep & ~deeper_sell,
                        reference=bar_hi,
                        depth=(rsi - bar_hi) / _RSI_DEPTH_SCALE,
                        params={
                            "bar_def": dict(bar_def),
                            "response": "own_price",
                            "portfolio_role": "component",
                        },
                    )],
                    value_col=_RSI_COL,
                    extra_params={
                        "window": cfg.HP_LEG_RSI_WINDOW,
                        "universe": "the portfolio components: the broad "
                                    "market + the pair legs (short-term-"
                                    "tech import)",
                    },
                    **common,
                )
                for row in r_b:
                    row["_portfolio_groups"] = []
                    # the comp frame carries no ddmin20 gauge — the
                    # uniform dd key rides NULL (own-price response);
                    # the cell's membership rides the ids array (the
                    # run's is_in_portfolio stamp)
                    row["_performance"]["dd"] = None
                    comp_buy[(row["code"], _rsi_sub(pct))] = row
                for row in r_s:
                    row["_portfolio_groups"] = []
                    row["_performance"]["dd"] = None
                    comp_sell[(row["code"], _rsi_sub(pct))] = row
                for hb in h_b:
                    comp_events.setdefault((hb["code"], "buy"), []) \
                        .append((hb["date"], hb))
                for hs in h_s:
                    comp_events.setdefault((hs["code"], "sell"), []) \
                        .append((hs["date"], hs))
                rules.extend(r_b)
                rules.extend(r_s)
                deeper_buy = deeper_buy | buy_deep
                deeper_sell = deeper_sell | sell_deep
        for events in comp_events.values():
            events.sort(key=lambda t: t[0])
        # -- THE COMPOSITE ENTRY (the orchestration layer): the three
        #    components' capitulation witnesses ALL alive within the
        #    5d window — the market's LOW vote + both legs' (the
        #    negative-corr hedging core). The trio's reference is the
        #    MEAN of the three lo bars; the composite's own row joins
        #    no group (the group IS the composite) ------------------
        rsi_mean = frame["rsi_mean"]
        entry_cands = [
            Candidate(
                key=f"tail{pct}",
                mask=(frame[f"wit_lo{pct}_a"].fillna(False)
                      & frame[f"wit_lo{pct}_b"].fillna(False)
                      & frame[f"wit_lo{pct}_m"].fillna(False)),
                reference=frame[f"lo_bar{pct}"],
                depth=(frame[f"lo_bar{pct}"] - rsi_mean)
                / _RSI_DEPTH_SCALE,
                params={
                    "bar_def": {
                        "kind": "hp_entry_legs_rsi",
                        "tail_pct": pct,
                        "rsi_window": cfg.HP_LEG_RSI_WINDOW,
                        "bar_window_days": cfg.HP_LEG_BAR_WINDOW_DAYS,
                        "witness_window": cfg.HP_ENTRY_WITNESS_WINDOW,
                    },
                    "response": "inverse_vol_pair_portfolio",
                    "portfolio_role": "composite",
                },
            )
            for pct in sorted(cfg.HP_LEG_TAIL_PCTS)
        ]
        r, h = self.run_cells(
            frame, "hp_entry_legs_rsi", "buy", entry_cands,
            value_col="rsi_mean",
            extra_params={
                "pair_weighting": "inverse-vol 60d, lagged one row",
                "universe": "offset-opposite industry pairs "
                            "(analysis_composites offsets, 255d)",
            },
            **common,
        )
        _stamp_dd(r, h, frame, "price")
        _stamp_portfolio(
            r, mdd, as_of, "hp_entry_legs_rsi", "hp_entry_legs_rsi",
            {"tail_pct": sorted(cfg.HP_LEG_TAIL_PCTS),
             "rsi_window": cfg.HP_LEG_RSI_WINDOW,
             "bar_window_days": cfg.HP_LEG_BAR_WINDOW_DAYS,
             "witness_window": cfg.HP_ENTRY_WITNESS_WINDOW},
            "inverse_vol_pair_portfolio", k)
        for row in r:
            row["_portfolio_groups"] = []
            tail = row["rule_params"]["bar_def"]["tail_pct"]
            sub = _rsi_sub(int(tail))
            a, b = parse_pair_code(row["code"])
            # the sourced members join the ORCHESTRATING portfolio's
            # ids array (the pair-code tag — run.py folds every sub's
            # members into the ONE row per portfolio); best-effort: a
            # sourced id joins only when that component cell shipped
            for comp_code in (cfg.HP_MARKET_CODE, a, b):
                member = comp_buy.get((comp_code, sub))
                if member is not None:
                    member["_portfolio_groups"].append(row["code"])
        # the composite EVENTS' member association: the THREE
        # components' capitulation events (market + both legs), each
        # the component's latest episode-first trigger ACROSS its tail
        # grid at-or-before the event day — the alive claim's own sub
        # and date (different subs / nearby days; the
        # portfolio_history_signals.member_signals payload)
        for ev in h:
            a, b = parse_pair_code(ev["code"])
            members: list[dict] = []
            for comp_code in (cfg.HP_MARKET_CODE, a, b):
                m = _latest_member(
                    comp_events.get((comp_code, "buy"), []), ev["date"])
                if m is not None:
                    members.append(m)
            ev["_portfolio_event_members"] = members
        rules.extend(r)
        history.extend(h)

        # -- THE MARKET-HIGH COMPOSITE EXIT: the broad market's own
        #    exhaustion signal (RSI above its adaptive high tail,
        #    ~5d witness) coordinated at the PORTFOLIO level — the
        #    highs counterpart of the entry's market-lows vote; the
        #    group references the market's sourced SELL row ----------
        rsi_m = frame["rsi_m"]
        exit_cands = [
            Candidate(
                key=f"tail{pct}",
                mask=frame[f"wit_hi{pct}_m"].fillna(False),
                reference=frame[f"hi_bar{pct}_m"],
                depth=(rsi_m - frame[f"hi_bar{pct}_m"])
                / _RSI_DEPTH_SCALE,
                params={
                    "bar_def": {
                        "kind": "hp_exit_mkt_high",
                        "tail_pct": pct,
                        "rsi_window": cfg.HP_LEG_RSI_WINDOW,
                        "bar_window_days": cfg.HP_LEG_BAR_WINDOW_DAYS,
                        "witness_window": cfg.HP_ENTRY_WITNESS_WINDOW,
                    },
                    "response": "inverse_vol_pair_portfolio",
                    "portfolio_role": "composite",
                },
            )
            for pct in sorted(cfg.HP_LEG_TAIL_PCTS)
        ]
        r, h = self.run_cells(
            frame, "hp_exit_mkt_high", "sell", exit_cands,
            value_col="rsi_m",
            extra_params={
                "pair_weighting": "inverse-vol 60d, lagged one row",
                "universe": "offset-opposite industry pairs "
                            "(analysis_composites offsets, 255d)",
            },
            **common,
        )
        _stamp_dd(r, h, frame, "price")
        _stamp_portfolio(
            r, mdd, as_of, "hp_exit_mkt_high", "hp_exit_mkt_high",
            {"tail_pct": sorted(cfg.HP_LEG_TAIL_PCTS),
             "rsi_window": cfg.HP_LEG_RSI_WINDOW,
             "bar_window_days": cfg.HP_LEG_BAR_WINDOW_DAYS,
             "witness_window": cfg.HP_ENTRY_WITNESS_WINDOW},
            "inverse_vol_pair_portfolio", k)
        for row in r:
            row["_portfolio_groups"] = []
            tail = row["rule_params"]["bar_def"]["tail_pct"]
            sub = _rsi_sub(int(tail))
            member = comp_sell.get((cfg.HP_MARKET_CODE, sub))
            if member is not None:
                member["_portfolio_groups"].append(row["code"])
        # the exit events' member: the MARKET's exhaustion cell event
        # (the one witness the composite coordinates — its latest
        # tail-grid trigger, the alive claim's own sub and date)
        for ev in h:
            m = _latest_member(
                comp_events.get((cfg.HP_MARKET_CODE, "sell"), []),
                ev["date"],
            )
            ev["_portfolio_event_members"] = [m] if m is not None else []
        rules.extend(r)
        history.extend(h)

        # -- CATCH-UP: sell the laggard at its OWN Short-Term-Tech
        #    exhaustion (RSI above its per-component adaptive high
        #    tail) while the ratio still favors the other leg; the
        #    group references BOTH legs' sourced SELL rows ----------
        rsi_lag = frame["rsi_lag"]
        catchup_cands = [
            Candidate(
                key=f"tail{pct}",
                mask=frame[f"hi{pct}_lag"].fillna(False),
                reference=frame[f"hi_bar{pct}_lag"],
                depth=(rsi_lag - frame[f"hi_bar{pct}_lag"])
                / _RSI_DEPTH_SCALE,
                params={
                    "bar_def": {
                        "kind": "hp_catchup_rsi_hi",
                        "tail_pct": pct,
                        "rsi_window": cfg.HP_LEG_RSI_WINDOW,
                        "bar_window_days": cfg.HP_LEG_BAR_WINDOW_DAYS,
                    },
                    "response": "laggard_leg",
                },
            )
            for pct in sorted(cfg.HP_LEG_TAIL_PCTS)
        ]
        r, h = self.run_cells(
            frame, "hp_catchup_rsi_hi", "sell", catchup_cands,
            value_col="rsi_lag",
            response_col="px_lag",
            **common,
        )
        _stamp_dd(r, h, frame, "px_lag")
        _stamp_portfolio(
            r, mdd, as_of, "hp_catchup_rsi_hi", "hp_catchup_rsi_hi",
            {"tail_pct": sorted(cfg.HP_LEG_TAIL_PCTS),
             "rsi_window": cfg.HP_LEG_RSI_WINDOW,
             "bar_window_days": cfg.HP_LEG_BAR_WINDOW_DAYS},
            "laggard_leg", k)
        for row in r:
            row["_portfolio_groups"] = []
            tail = row["rule_params"]["bar_def"]["tail_pct"]
            sub = _rsi_sub(int(tail))
            for leg in parse_pair_code(row["code"]):
                member = comp_sell.get((leg, sub))
                if member is not None:
                    member["_portfolio_groups"].append(row["code"])
        # the catch-up EVENTS' member: the LAGGARD leg's exhaustion
        # event — the leg sign(ratio_z) selects at the event day (the
        # trigger's own witness; both legs' sells ride the RULES
        # group, the event's member is the leg that actually fired).
        # ONE host boundary over the pair frame's three columns (the
        # _stamp_dd pattern), the event days resolved host-side.
        if h:
            codes_np = host_array(frame["code"]).astype(object)
            dates_np = host_array(frame["date"]).astype("datetime64[D]")
            rz_np = host_array(frame["ratio_z"]).astype("float64")

            def laggard_at(code: str, day: date) -> str:
                lo = int(np.searchsorted(codes_np, code, side="left"))
                hi = int(np.searchsorted(codes_np, code, side="right"))
                pos = int(np.searchsorted(dates_np[lo:hi],
                                          np.datetime64(day, "D")))
                a, b = parse_pair_code(code)
                rz = float(rz_np[lo + pos]) if pos < hi - lo else 0.0
                return b if rz > 0.0 else a

            for ev in h:
                m = _latest_member(
                    comp_events.get(
                        (laggard_at(ev["code"], ev["date"]), "sell"), []),
                    ev["date"],
                )
                ev["_portfolio_event_members"] = \
                    [m] if m is not None else []
        rules.extend(r)
        history.extend(h)

        # -- THE RF-WASHOUT TRIO ENTRY (the 2026-10-19 graduates'
        #    strongest, docs/hedge_pairs_more_triggers_study_
        #    2026-10-19.md): both legs' HP_WASH_MKT_WINDOW-day
        #    Donchian-floor breakdown witnesses alive within the
        #    pairing window AND the market's RSI-HP_WASH_MKT_TAIL_PCT
        #    capitulation witness — the RSI trio's STRUCTURAL cousin
        #    (a Donchian floor cannot thin the way a trailing
        #    percentile does; measured +2.58% t=8.94, forward
        #    worst-trough -1.79% vs the -3.44% all-days baseline).
        #    The group references the trio's sourced rsi BUY rows at
        #    the market-witness tail (the market's witness is a
        #    trigger leg) --------------------------------------------
        wm = cfg.HP_WASH_MKT_WINDOW
        wm_col = f"washdepth{wm}"
        wm_tail = cfg.HP_WASH_MKT_TAIL_PCT
        wash_mkt_cands = [
            Candidate(
                key=f"wash{wm}_mkt{wm_tail}",
                mask=(frame[f"wit_rffloor{wm}_a"].fillna(False)
                      & frame[f"wit_rffloor{wm}_b"].fillna(False)
                      & frame[f"wit_lo{wm_tail}_m"].fillna(False)),
                reference=0.0,
                depth=np.maximum(-frame[wm_col] * 10.0, 0.0),
                params={
                    "bar_def": {
                        "kind": "hp_entry_wash",
                        "window": wm,
                        "witness_window": cfg.HP_ENTRY_WITNESS_WINDOW,
                        "mkt_tail_pct": wm_tail,
                    },
                    "response": "inverse_vol_pair_portfolio",
                    "portfolio_role": "composite",
                },
            ),
        ]
        r, h = self.run_cells(
            frame, "hp_entry_wash120_mkt", "buy", wash_mkt_cands,
            value_col=wm_col,
            extra_params={
                "pair_weighting": "inverse-vol 60d, lagged one row",
                "universe": "offset-opposite industry pairs "
                            "(analysis_composites offsets, 255d)",
            },
            **common,
        )
        _stamp_dd(r, h, frame, "price")
        _stamp_portfolio(
            r, mdd, as_of, "hp_entry_wash120_mkt", "hp_entry_wash",
            {"window": wm, "mkt_tail_pct": wm_tail,
             "witness_window": cfg.HP_ENTRY_WITNESS_WINDOW},
            "inverse_vol_pair_portfolio", k)
        for row in r:
            row["_portfolio_groups"] = []
            a, b = parse_pair_code(row["code"])
            for comp_code in (cfg.HP_MARKET_CODE, a, b):
                member = comp_buy.get((comp_code, _rsi_sub(wm_tail)))
                if member is not None:
                    member["_portfolio_groups"].append(row["code"])
        # the wash-trio EVENTS' member: the MARKET's RSI capitulation
        # (the one sourced witness in the trigger — the legs'
        # Donchian-floor breakdowns are pair-frame witnesses, not cell
        # events, so they ride no member record); its latest tail-grid
        # trigger, the alive claim's own sub and date
        for ev in h:
            m = _latest_member(
                comp_events.get((cfg.HP_MARKET_CODE, "buy"), []),
                ev["date"],
            )
            ev["_portfolio_event_members"] = [m] if m is not None else []
        rules.extend(r)
        history.extend(h)
        return rules, history


class HedgePairEntry(RuleEntry):
    """The PAIR-NATIVE entry (signal_type hedge_pairs): the exits with
    no existing-family counterpart — the volatility-harvest ratio trim
    (one sub, the z grid the candidate set) and the hedge-regime break
    stop. Runs over the shared prepared frame AFTER HedgeRsiEntry
    (registry order); rows default to their own one-id portfolio
    groups."""

    FAMILY = "hedge_pairs"
    PACKAGE = "hedge_pairs"
    SUB_TYPES = (
        "hp_harvest",
        # the 2026-10-20 momentum tilt (the harvest's opposite pair)
        "hp_mom_strong", "hp_mom_lag",
        "hp_stop_regime",
        # the 2026-10-19 more-triggers graduates (pair-native; the
        # per-window wash trio ONE sub since the 2026-10-21 dedup —
        # the window grid the run_cells candidate set)
        "hp_entry_wash",
        "hp_entry_rho50", "hp_entry_catchup_reclaim60",
    )
    REGIME_FREE = True
    USES_TREND_SPLIT = False
    OHLCV_BOOST = False
    WRITES_TREND_DAYS = False
    FORWARD_PRICE_COLS = ("price", "px_strong", "px_lag")

    async def fetch_frame(self) -> pd.DataFrame:
        raise NotImplementedError(
            "HedgeRsiEntry (registry entry0) owns the ONE shared fetch — "
            "this entry only runs over the prepared pair frame")

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history
        k = REENTRY_EXIT_DAYS
        common = {"k": k, "depth_factor": _DEPTH_FACTOR}
        # the PORTFOLIO-tier stamps' shared basis (the entry0 precedent)
        mdd = _max_dd_block(frame)
        as_of = self._performance_as_of(frame)

        # -- HARVEST: sell the relatively-high leg at |ratio z|
        #    extremes (the study's pair-native exit). ONE sub since
        #    the 2026-10-21 dedup — the z grid is the run_cells
        #    candidate set (the hp_mom precedent: the per-cell winner
        #    picks the bar; the retired z15/z20 subs differed ONLY in
        #    the bar) ----------------------------------------------
        abs_rz = frame["ratio_z"].abs()
        harvest_cands = [
            Candidate(
                key=f"ratio_z{z:g}",
                mask=(abs_rz >= z).fillna(False),
                # +z bar above / -z below — sign arithmetic (a
                # same-index Series, never a host-ctor hybrid)
                reference=np.sign(frame["ratio_z"]) * z,
                depth=abs_rz - z,
                params={
                    "bar_def": {
                        "kind": "hp_harvest",
                        "ratio_z": z,
                        "ratio_window": cfg.HP_RATIO_Z_WINDOW,
                    },
                    "response": "strong_leg",
                },
            )
            for z in sorted(cfg.HP_HARVEST_ZS)
        ]
        r, h = self.run_cells(
            frame, "hp_harvest", "sell", harvest_cands,
            value_col="ratio_z",
            response_col="px_strong",
            **common,
        )
        _stamp_dd(r, h, frame, "px_strong")
        _stamp_portfolio(
            r, mdd, as_of, "hp_harvest", "hp_harvest",
            {"ratio_z": sorted(cfg.HP_HARVEST_ZS),
             "ratio_window": cfg.HP_RATIO_Z_WINDOW},
            "strong_leg", k)
        rules.extend(r)
        history.extend(h)

        # -- THE MOMENTUM TILT (the harvest's OPPOSITE buy/sell pair;
        #    the 2026-10-20 spread-rotation study's portfolio-first
        #    graduate, docs/hedge_pairs_spread_rotation_study_
        #    2026-10-20.md): at the |ratio z| divergence the measured
        #    drift is CONTINUATION, not reversion — the strong leg
        #    keeps outperforming (fwd +0.37..+0.55% across the k grid)
        #    and shifting the book toward it is the only tilt that
        #    improved BOTH lenses (dd +0.16..+0.90pp shallower on
        #    62-67% of events; the proposed mean-reversion direction
        #    measured -1.24% per open, no stop rule rescued it). Two
        #    pair-native cells on the ONE trigger — no sourced
        #    short-term-tech references, no portfolio cascade:
        #    hp_mom_strong BUYs the strong leg (its own gated response:
        #    the buy bar + the pooled tier), hp_mom_lag SELLs the
        #    laggard (the exit-timing bar — sells are never
        #    pooled-gated). The k grid is the run_cells candidate set;
        #    the reference is the SIGNED bar (a +z day buys/sells leg
        #    A, a -z day leg B) ------------------------------------
        abs_rz = frame["ratio_z"].abs()
        mom_ref = np.sign(frame["ratio_z"])
        for mom_sub, mom_action, mom_resp, mom_response_col in (
            ("hp_mom_strong", "buy", "strong_leg", "px_strong"),
            ("hp_mom_lag", "sell", "laggard_leg", "px_lag"),
        ):
            mom_cands = [
                Candidate(
                    key=f"ratio_z{k_:g}",
                    mask=(abs_rz >= k_).fillna(False),
                    reference=mom_ref * k_,
                    depth=abs_rz - k_,
                    params={
                        "bar_def": {
                            "kind": "hp_mom_tilt",
                            "ratio_z": k_,
                            "ratio_window": cfg.HP_RATIO_Z_WINDOW,
                        },
                        "response": mom_resp,
                    },
                )
                for k_ in sorted(cfg.HP_MOM_ZS)
            ]
            r, h = self.run_cells(
                frame, mom_sub, mom_action, mom_cands,
                value_col="ratio_z",
                response_col=mom_response_col,
                extra_params={
                    "laggard_side": "sign(ratio_z), same day (the "
                                    "trigger leg selector)",
                },
                **common,
            )
            _stamp_dd(r, h, frame, mom_response_col)
            _stamp_portfolio(
                r, mdd, as_of, mom_sub, "hp_mom_tilt",
                {"ratio_z": sorted(cfg.HP_MOM_ZS),
                 "ratio_window": cfg.HP_RATIO_Z_WINDOW},
                mom_resp, k)
            rules.extend(r)
            history.extend(h)

        # -- STOP REGIME: the offset-correlation hedge broke ---------
        mask = (frame["rho_off"] >= cfg.HP_REGIME_BROKE_RHO) \
            & (frame["hed_witness"] > 0.0)
        r, h = self.run_cells(
            frame, "hp_stop_regime", "sell",
            [Candidate(
                key="rho_off_break",
                mask=mask,
                reference=cfg.HP_REGIME_BROKE_RHO,
                depth=frame["rho_off"],
                params={
                    "bar_def": {
                        "kind": "hp_stop_regime",
                        "rho_off_break": cfg.HP_REGIME_BROKE_RHO,
                        "hedgeable_rho": cfg.HP_HEDGEABLE_RHO,
                        "witness_window": cfg.HP_REGIME_WITNESS_WINDOW,
                        "corr_window": cfg.HP_VOL_WINDOW,
                    },
                    "response": "inverse_vol_pair_portfolio",
                },
            )],
            value_col="rho_off",
            **common,
        )
        _stamp_dd(r, h, frame, "price")
        _stamp_portfolio(
            r, mdd, as_of, "hp_stop_regime", "hp_stop_regime",
            {"rho_off_break": cfg.HP_REGIME_BROKE_RHO,
             "hedgeable_rho": cfg.HP_HEDGEABLE_RHO,
             "witness_window": cfg.HP_REGIME_WITNESS_WINDOW,
             "corr_window": cfg.HP_VOL_WINDOW},
            "inverse_vol_pair_portfolio", k)
        rules.extend(r)
        history.extend(h)

        # -- REGIME FLIP: retired (the 2026-10-21 dedup) — the
        #    observational shallow twin of the deep anchor below.

        # -- THE RF-WASHOUT ENTRY (2026-10-19 graduate, docs/
        #    hedge_pairs_more_triggers_study_2026-10-19.md). ONE sub
        #    since the 2026-10-21 dedup — the window grid is the
        #    run_cells candidate set (the hp_harvest precedent: the
        #    per-cell winner picks the window; the retired
        #    hp_entry_wash{60,120,255} subs differed ONLY in the
        #    window). Both legs' W-day Donchian-floor breakdown
        #    witnesses alive within the pairing window — the
        #    portfolio's own cheapness event on a STRUCTURAL bar.
        #    Each window its OWN population (the rf_break no-carve
        #    precedent — carving starved the mid windows there); the
        #    _mkt trio twin lives under hp_rsi. The recorded value is
        #    the fetch's combined washdepth gauge (the deepest alive
        #    witness's window) -------------------------------
        wash_cands = [
            Candidate(
                key=f"wash{w}",
                mask=(frame[f"wit_rffloor{w}_a"].fillna(False)
                      & frame[f"wit_rffloor{w}_b"].fillna(False)),
                reference=0.0,
                depth=np.maximum(-frame[f"washdepth{w}"] * 10.0, 0.0),
                params={
                    "bar_def": {
                        "kind": "hp_entry_wash",
                        "window": w,
                        "witness_window":
                            cfg.HP_ENTRY_WITNESS_WINDOW,
                    },
                    "response": "inverse_vol_pair_portfolio",
                },
            )
            for w in sorted(cfg.HP_WASH_WINDOWS)
        ]
        r, h = self.run_cells(
            frame, "hp_entry_wash", "buy", wash_cands,
            value_col="washdepth",
            extra_params={
                "pair_weighting": "inverse-vol 60d, lagged one row",
            },
            **common,
        )
        _stamp_dd(r, h, frame, "price")
        _stamp_portfolio(
            r, mdd, as_of, "hp_entry_wash", "hp_entry_wash",
            {"window": sorted(cfg.HP_WASH_WINDOWS),
             "witness_window": cfg.HP_ENTRY_WITNESS_WINDOW},
            "inverse_vol_pair_portfolio", k)
        rules.extend(r)
        history.extend(h)

        # -- THE DEEP HEDGEABLE TURN-ON: rho_off's first day at/below
        #    HP_RHO_DEEP — buying the pair when the covariance
        #    cancellation works hardest (the turn-on's ONE record
        #    since the 2026-10-21 dedup retired the observational
        #    shallow twin; the grid measured -0.40 flat / -0.50 the
        #    bar / -0.60 weaker) -------------------------------------------
        mask = frame["rho_off"] <= cfg.HP_RHO_DEEP
        r, h = self.run_cells(
            frame, "hp_entry_rho50", "buy",
            [Candidate(
                key="rho_deep",
                mask=mask,
                reference=cfg.HP_RHO_DEEP,
                depth=np.maximum(
                    (cfg.HP_RHO_DEEP - frame["rho_off"]) * 10.0, 0.0),
                params={
                    "bar_def": {
                        "kind": "hp_entry_rho50",
                        "rho_deep": cfg.HP_RHO_DEEP,
                        "corr_window": cfg.HP_VOL_WINDOW,
                    },
                    "response": "inverse_vol_pair_portfolio",
                },
            )],
            value_col="rho_off",
            extra_params={
                "pair_weighting": "inverse-vol 60d, lagged one row",
            },
            **common,
        )
        _stamp_dd(r, h, frame, "price")
        _stamp_portfolio(
            r, mdd, as_of, "hp_entry_rho50", "hp_entry_rho50",
            {"rho_deep": cfg.HP_RHO_DEEP,
             "corr_window": cfg.HP_VOL_WINDOW},
            "inverse_vol_pair_portfolio", k)
        rules.extend(r)
        history.extend(h)

        # -- THE CATCH-UP ENTRY: the WEAK leg's first close back above
        #    its own MA (HP_RECLAIM_MA_WINDOW) — the ma_cross reclaim
        #    in coordination form, the ENTRY mirror of the catch-up
        #    sell (reclaim in, RSI-high out). RESPONSE = the PORTFOLIO
        #    (buy the PAIR at the weak leg's turn): the px_lag
        #    switching path measured systematically NEGATIVE here
        #    (pooled -0.56% t=-6.07 — it abandons the reclaimed leg
        #    exactly at its success, the ratio's zero-cross) while the
        #    portfolio response reproduces the study's held-leg +0.55%
        #    (temp_scripts/_study_reclaim_response_choice.py) --------
        m = cfg.HP_RECLAIM_MA_WINDOW
        rz = frame["ratio_z"]
        mask = ((frame[f"reclaim{m}_a"].fillna(False)
                 & (rz < 0.0))
                | (frame[f"reclaim{m}_b"].fillna(False)
                   & (rz > 0.0)))
        r, h = self.run_cells(
            frame, "hp_entry_catchup_reclaim60", "buy",
            [Candidate(
                key=f"reclaim{m}",
                mask=mask,
                reference=0.0,
                depth=np.maximum(rz.abs(), 0.0),
                params={
                    "bar_def": {
                        "kind": "hp_entry_catchup_reclaim",
                        "ma_window": m,
                        "ratio_z_window": cfg.HP_RATIO_Z_WINDOW,
                    },
                    "response": "inverse_vol_pair_portfolio",
                },
            )],
            value_col=f"lag_gap{m}",
            extra_params={
                "laggard_side": "sign(ratio_z), same day (the trigger "
                                "leg selector)",
                "pair_weighting": "inverse-vol 60d, lagged one row",
            },
            **common,
        )
        _stamp_dd(r, h, frame, "price")
        _stamp_portfolio(
            r, mdd, as_of, "hp_entry_catchup_reclaim60",
            "hp_entry_catchup_reclaim",
            {"ma_window": m, "ratio_z_window": cfg.HP_RATIO_Z_WINDOW},
            "inverse_vol_pair_portfolio", k)
        rules.extend(r)
        history.extend(h)
        return rules, history
