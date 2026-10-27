"""boll_std — the Bollinger fixed-σ family (analysis.analysis_signals.
engines.signal_families.boll_std.engine).

A BUY fires when the close's σ-depth z = (price − SMA_20)/σ_20 pierces
−k (the lower band); a SELL at +k. Bars are FIXED σ multipliers — z
already IS the Gaussian depth score (Φ⁻¹(1−q) == |z|), so a percentile
layer would restate the same axis. Population σ (ddof=0) matches the
charting convention. The ks are the nested carving grid: a deep breach
counts once, at its DEEPEST claim (3σ carves out of 2.5σ, 2.5σ out of
2σ).

The z axis is computed IN-ENGINE from the fetched price (the former
analysis.mov_ave_boll table is gone). Regime splits stay; trend splits
added by the redesign.

THE OHLCV-BOOSTED VARIANT (2026-10-26, user-directed "create shared pe
and ohlcv logic ... use them where possible or relevant"): each side
also runs its TURNOVER-CLIMAX-confirmed population on the
is_ohlcv_boosted PK axis — the band breach ON a heavy/climax turnover
day (amt_climax >= HEAVY, the shared OhlcvBoostGate in
engines/_ohlcv_boost), the volume-confirmation literature on the
family the 2026-10-16 boost study measured its only shipping side on.
A SEPARATE run_cells call per (side, k) — the variant population is
judged independently by the same gate and emits nothing where it
cannot clear the bar (the negative-optimization self-prune); the
plain rows stay the population of record (the boost never gates
them). The variant is history/analysis-only: the live active set
excludes the variant-axis cells (the live breach check has no OHLCV
condition machinery — it would fire the bar without the turnover
confirmation).
"""
from __future__ import annotations

import pandas as pd

from _common.df_utils import grouped_rolling_agg

from analysis.analysis_signals import config as cfg
from analysis.analysis_signals.engines._ohlcv_boost import OhlcvBoostGate
from analysis.analysis_signals.engines.rule_base import Candidate, RuleEntry
from analysis.analysis_signals.fetch.inputs import fetch_boll_inputs
from analysis.trends import REENTRY_EXIT_DAYS

_WINDOW = 20  # the Bollinger classic
_K_LABELS = {2.0: "boll20_2std", 2.5: "boll20_2p5std", 3.0: "boll20_3std"}


class BollBandEntry(RuleEntry):
    """The σ-depth band-breach rule — one entry, the fixed ±kσ bars are
    the sub signal types (boll20_2std / boll20_2p5std / boll20_3std)."""

    FAMILY = "bollinger"
    PACKAGE = "boll_std"
    SUB_TYPES = ("boll20_2std", "boll20_2p5std", "boll20_3std")
    REGIME_FREE = False
    USES_TREND_SPLIT = True
    # the trust-score pass opts in (2026-10-16): the day-0 anatomy now
    # carries the turnover-climax grade — the volume-climax literature
    # as a trust axis widening the boosted population (NEVER a gate;
    # sides failing the ship gates honestly boost nothing)
    OHLCV_BOOST = True

    async def fetch_frame(self) -> pd.DataFrame:
        return await fetch_boll_inputs(self.conn, self.sec_type, self.codes)

    def run(self, frame: pd.DataFrame) -> tuple[list[dict], list[dict]]:
        rules: list[dict] = []
        history: list[dict] = []
        if frame.empty:
            return rules, history

        keys = ["code"]
        sma = grouped_rolling_agg(
            frame, keys, "price", _WINDOW, min_periods=_WINDOW,
        )
        sigma = grouped_rolling_agg(
            frame, keys, "price", _WINDOW, min_periods=_WINDOW,
            agg="std", ddof=0,
        )
        frame["boll_z"] = (frame["price"] - sma) / sigma
        z = frame["boll_z"].astype("float64")

        # the SHARED OHLCV-boost gate (engines._ohlcv_boost — a per-run
        # instance caching the frame's condition): the day's
        # turnover-climax grade at least HEAVY; inactive when the
        # fetch carries no amt axis (the variants would only starve)
        amt_gate = OhlcvBoostGate()
        amt_gate.prepare(frame)

        # nested widest-first carving, deepest claim first: 3σ ⊃ 2.5σ ⊃ 2σ
        deeper_buy = pd.Series(False, index=frame.index, dtype="bool")
        deeper_sell = pd.Series(False, index=frame.index, dtype="bool")
        for k in sorted(cfg.BOLL_KS, reverse=True):  # 3.0, 2.5, 2.0
            sub = _K_LABELS[k]
            buy_deep = (z < -k).fillna(False)
            sell_deep = (z > k).fillna(False)
            buy_carved = (buy_deep & ~deeper_buy).fillna(False)
            sell_carved = (sell_deep & ~deeper_sell).fillna(False)
            common = {
                "k": REENTRY_EXIT_DAYS,
                "value_col": "boll_z",
                "extra_params": {
                    "window": _WINDOW,
                    "bar_def": {"kind": "fixed_sigma", "k": k,
                                "sigma": "population (ddof=0)"},
                },
            }
            r_buy, h_buy = self.run_cells(
                frame, sub, "buy",
                [Candidate(
                    key=f"minus{k:g}sigma",
                    mask=buy_carved,
                    reference=-k,
                    depth=(-k - z),
                )],
                depth_factor=0.25,  # 0.25 confidence per σ of breach depth
                **common,
            )
            r_sell, h_sell = self.run_cells(
                frame, sub, "sell",
                [Candidate(
                    key=f"plus{k:g}sigma",
                    mask=sell_carved,
                    reference=k,
                    depth=(z - k),
                )],
                depth_factor=0.25,
                **common,
            )
            rules.extend(r_buy)
            rules.extend(r_sell)
            history.extend(h_buy)
            history.extend(h_sell)
            # the OHLCV-boosted variant pass (the shared gate — the
            # turnover-confirmed breach population, judged
            # independently; history/analysis-only — the live active
            # set excludes variant cells). The gate fragment rides the
            # call's extra_params.bar_def (extra_params merge AFTER the
            # candidate's params — the caller-side merge is the
            # contract here); the gate's own candidate-level bar_def
            # is overwritten by it, harmlessly.
            if amt_gate.active:
                v_common = {
                    "k": REENTRY_EXIT_DAYS,
                    "value_col": "boll_z",
                    "depth_factor": 0.25,
                    "extra_params": {
                        "window": _WINDOW,
                        "bar_def": {"kind": "fixed_sigma", "k": k,
                                    "sigma": "population (ddof=0)",
                                    "ohlcv_gate": amt_gate.bar_def()},
                    },
                }
                for action, carved, ref, dep, side_key in (
                    ("buy", buy_carved, -k, (-k - z), f"minus{k:g}sigma"),
                    ("sell", sell_carved, k, (z - k), f"plus{k:g}sigma"),
                ):
                    r_v, h_v = amt_gate.run_pass(
                        self, frame, sub, action,
                        [Candidate(
                            key=side_key, mask=carved, reference=ref,
                            depth=dep,
                        )],
                        v_common,
                    )
                    rules.extend(r_v)
                    history.extend(h_v)
            deeper_buy = deeper_buy | buy_deep
            deeper_sell = deeper_sell | sell_deep
        return rules, history
