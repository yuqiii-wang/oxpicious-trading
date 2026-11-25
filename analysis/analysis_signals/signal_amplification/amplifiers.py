"""The amplifier registry + day detection (analysis.analysis_signals.
signal_amplification.amplifiers).

AMPLIFIER — a recurring day-level anatomy that AMPLIFIES (positive
amplify_bp) or DISCOUNTS (negative) the bound signal families' claims
on that day. The TWO-LEVEL AXIS (2026-11-20): the PARENT TYPE
(static.label_dim 'signal_amplifier_type' — the data domain the
anatomy comes from) + the per-type SUB TYPE vocabulary. The registry
is the config AMP_BINDINGS matrix (nested parent type -> sub type ->
bindings) joined to the label mirrors:

  ohlcv (0) — the K-line day anatomy sub types ('signal_amplifier'):

    flush_washout    (0, 探底洗出, BUY reversion): the lower wick >= its
                     trailing-252d q90 (探底回升 flush) on a DOWN close
                     with heavy participation — the capitulation washout.
    gap_reclaim      (1, 低开收复, BUY reversion): down open (<= -0.5%)
                     reclaimed by the close (close >= prev close).
    breakout_confirm (2, 放量突破确认, BUY continuation): close above the
                     prior valid day's high + heavy participation + a
                     strong close (close_pos >= 0.7).
    spike_blowoff    (3, 冲高回落放量, SELL exit): the upper wick >= its
                     trailing-252d q90 with the close fading into the
                     bottom 30% of the range on heavy participation.
    exhaustion_gap   (4, 高开低走竭尽, SELL exit): up open (>= +0.5%) on
                     a red body — the gap that failed intraday.
    dry_rebound      (5, 缩量弱反弹, BUY DISCOUNT): an up close on
                     shrinking participation (amt_ratio_20 <= 0.8) — the
                     fade-prone rebound; the bound families' BUY is
                     DISCOUNTED, deeper dryness discounts harder.

  margin (1) — the 融资融券 margin-balance sub types
  ('signal_amplifier_margin'; rz_balance, indices read the
  margin_index_series weighted aggregate):

    margin_washout   (0, 融资余额低位, BUY reversion): the balance at or
                     below its own trailing-252d q10 bar — leverage
                     washed out; LOW margin = LOW risk and a LOWER
                     price, the de-risked balance strengthens the
                     reversion families' BUY.
    margin_crowding  (1, 融资余额高位, SELL exit): the balance at or above
                     its own trailing-252d q90 bar — leverage crowding,
                     the fragile side; strengthens the exit-timing
                     families' SELL.

  news (2) — the daily news-text MARKET-state sub types
  ('signal_amplifier_news'; text.news_text_daily scope market_all,
  merged per DATE onto the anatomy frame + the per-code weekly
  aggregates and drop leg — news_anatomy.NewsAnatomy; migrated from
  the deleted news_wash detection family 2026-11-22: the news states
  AMPLIFY the existing families' signals, never create their own.
  The DAILY states ride the code's own drop leg — the news extreme
  DURING the drop is the washout's confirmation; the weekly
  aggregates fire un-gated — the aggregate carries the persistence
  the leg confirms daily):

    tone_disagree    (11, 负面声浪分歧, BUY reversion): the keyword-tone
                     panic confirmed by cross-origin disagreement —
                     lex_tone_z <= -1.5 & disp_z >= 1.0 (+1.90%,
                     t=7.09, hit 73% — the measured strongest).
    wave_disagree    (10, 情绪冲击分歧, BUY reversion): the sentiment
                     shock confirmed by the negative-article wave AND
                     the disagreement — sent_z <= -1.5 & flow_neg >= 3
                     & disp_z >= 1.0 (+1.74%, t=6.87).
    neg_wave         (2, 负面报道浪潮, BUY reversion): flow_neg >= 5 —
                     the strongly-negative article wave (+1.87%,
                     excess +1.60pp over drift, t=6.70).
    tone_panic       (1, 关键词情绪恐慌, BUY reversion): lex_tone_z
                     <= -1.5 — the down-family mention balance
                     surging (+1.59%, t=4.65).
    shock_wave       (9, 情绪冲击浪潮, BUY reversion): sent_z <= -1.5
                     confirmed by flow_neg >= 3 (+1.34%, t=5.35).
    sent_shock       (0, 情绪冲击, BUY reversion): sent_z <= -2.0 —
                     the deep sentiment-shock tail (+0.77%, t=3.80).
    quiet_cov        (3, 报道异常冷清, BUY reversion): flow_af <= -1.0 —
                     abnormally quiet coverage (the honest marginal
                     candidate, +0.26pp).
    week_tone        (7, 周度情绪恐慌, BUY reversion): mean(lex_tone_z,5)
                     <= -1.0 — the week's tone panic (stress +5.90%
                     hit 78%, fwd60 +10.13%).
    week_shock       (8, 周度情绪冲击, BUY reversion): mean(sent_z,5)
                     <= -1.0 — the persistent weekly shock (stress
                     +1.85%).
    week_neg_wave    (6, 周度负面浪潮, BUY reversion): sum(flow_neg,5)
                     >= 10 — the week's negative wave (+0.77%, t=3.21).
    sent_euphoria    (4, 情绪亢奋背离, BUY divergence): sent_z >= +1.5 on
                     the drop leg — "good news can't lift a falling
                     market" (the divergence pair, faithful migration;
                     the evidence block judges).
    tone_euphoria    (5, 关键词亢奋背离, BUY divergence): lex_tone_z
                     >= +1.5 on the drop leg (the pair's tone leg).
    week_tone_recovery (12, 周度情绪修复, SELL exit): the weekly tone
                     crosses UP through 0 after a deep wt episode
                     within the lookback — the ONE sell side that
                     cleared the bar (exit timing).

DEPTH — the per-amplifier confidence-scale input (the CellGate
convention: base * (1 + factor * min(depth, cap))), in each
amplifier's own normalizer units: wick amplifiers scale in
trailing-q90 bar units (lw/uw over its own bar), gap/breakout
amplifiers in the code's trailing return-sigma units, dry_rebound in
dryness fraction (bar - ratio)/bar, margin amplifiers in the balance's
fraction beyond its own quantile bar, news amplifiers in the state
metric's own units past its bar (z units; the fraction beyond for the
count bars; the wt value for the recovery).

Layout: whole-frame elementwise ops only — every mask is a fillna'd
Series comparison chain (the cudf dtype contract: pure bool after
filling nulls); one REAL concat materializes the per-amplifier day
frames at the end.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field

from _common.df_utils import (
    grouped_rolling_agg,
    grouped_shift,
    safe_columns,
)
from _common.labels import (
    ACTION_CODE,
    SIGNAL_AMPLIFIER_CODE,
    SIGNAL_AMPLIFIER_MARGIN_CODE,
    SIGNAL_AMPLIFIER_NEWS_CODE,
    SIGNAL_AMPLIFIER_TYPE_CODE,
    SIGNAL_TYPE_CODE,
)

from analysis.analysis_signals import config as cfg

__all__ = [
    "AmplifierBinding",
    "AmplifierDef",
    "AMPLIFIER_DEFS",
    "AMPLIFIER_BAR_DEFS",
    "AmplifierEngine",
    "AMPLIFY_DAY_COLUMNS",
]


class AmplifierBinding(BaseModel):
    """One (family, side, strength) binding — the amplifier amplifies
    that signal family's action at that base strength (negative =
    discount)."""

    signal_type: int      # the static.label_dim 'signal_type' code
    signal_label: str
    action: int           # the static.label_dim 'action' code (0 buy / 1 sell)
    action_label: str
    base_bp: int          # negative = discount (dry_rebound)


class AmplifierDef(BaseModel):
    """One amplifier: its two-level identity (parent type + sub type)
    + the bound (family, side, strength) set from the config matrix."""

    amplifier_type: int = Field(ge=0)  # the PARENT TYPE, 'signal_amplifier_type' code
    sub_type: int = Field(ge=0)        # the pattern: 'signal_amplifier' code when ohlcv, 'signal_amplifier_margin' code when margin
    label: str                         # the sub-type label (the config matrix key)
    parent_label: str                  # the parent type label ("ohlcv" / "margin")
    bindings: list[AmplifierBinding]


def _sub_vocabulary(parent_label: str) -> dict[str, int]:
    """The parent type's sub-type label vocabulary (a parent missing
    from the type vocabulary is a startup error)."""
    if parent_label not in SIGNAL_AMPLIFIER_TYPE_CODE:
        raise ValueError(
            f"amplifier parent type '{parent_label}' in config "
            f"AMP_BINDINGS is missing from the signal_amplifier_type "
            f"label vocabulary")
    if parent_label == "ohlcv":
        return SIGNAL_AMPLIFIER_CODE
    if parent_label == "margin":
        return SIGNAL_AMPLIFIER_MARGIN_CODE
    if parent_label == "news":
        return SIGNAL_AMPLIFIER_NEWS_CODE
    raise ValueError(
        f"amplifier parent type '{parent_label}' has no sub-type "
        f"vocabulary wired in amplifiers._sub_vocabulary")


def _build_defs() -> list[AmplifierDef]:
    """The registry from the nested config binding matrix + the label
    mirrors (a matrix key missing from its label vocabulary is a
    startup error, not a silent skip)."""
    defs: list[AmplifierDef] = []
    for parent_label, subs in cfg.AMP_BINDINGS.items():
        sub_vocab = _sub_vocabulary(parent_label)
        for label, bindings in subs.items():
            if label not in sub_vocab:
                raise ValueError(
                    f"amplifier '{label}' (parent '{parent_label}') in "
                    f"config AMP_BINDINGS is missing from its sub-type "
                    f"label vocabulary")
            pairs: list[AmplifierBinding] = []
            for fam, action, base_bp in bindings:
                if fam not in SIGNAL_TYPE_CODE:
                    raise ValueError(
                        f"binding family '{fam}' is missing from the "
                        f"signal_type label vocabulary")
                if action not in ACTION_CODE or action == ACTION_CODE["wait"]:
                    raise ValueError(
                        f"binding action '{action}' must be buy or sell")
                pairs.append(AmplifierBinding(
                    signal_type=SIGNAL_TYPE_CODE[fam],
                    signal_label=fam,
                    action=ACTION_CODE[action],
                    action_label=action,
                    base_bp=base_bp,
                ))
            defs.append(AmplifierDef(
                amplifier_type=SIGNAL_AMPLIFIER_TYPE_CODE[parent_label],
                sub_type=sub_vocab[label],
                label=label,
                parent_label=parent_label,
                bindings=pairs,
            ))
    return sorted(defs, key=lambda d: (d.amplifier_type, d.sub_type))


AMPLIFIER_DEFS: list[AmplifierDef] = _build_defs()

_AMP_T = SIGNAL_AMPLIFIER_TYPE_CODE
_AMP_OHLCV = (_AMP_T["ohlcv"],)
_AMP_MARGIN = (_AMP_T["margin"],)
_AMP_NEWS = (_AMP_T["news"],)

# the constant bar definitions (stored on every rules row's amplifiers[]
# bar_def and every history row's amplifier_params.bar_def — the UI
# renders the bar words from these), keyed (parent type code, sub code)
AMPLIFIER_BAR_DEFS: dict[tuple[int, int], dict] = {
    (_AMP_T["ohlcv"], SIGNAL_AMPLIFIER_CODE["flush_washout"]): {
        "axis": "lw", "wick_q": cfg.AMP_WICK_Q,
        "wick_window": cfg.AMP_WICK_WINDOW,
        "wick_min_periods": cfg.AMP_WICK_MIN_PERIODS,
        "body": "down", "amt_min": cfg.AMP_AMT_HEAVY_RATIO,
        "amt_window": 20,
    },
    (_AMP_T["ohlcv"], SIGNAL_AMPLIFIER_CODE["gap_reclaim"]): {
        "open_bar": -cfg.AMP_GAP_OPEN_BAR, "close_vs": "prev_close",
        "sigma_window": cfg.AMP_RET_SIGMA_WINDOW,
    },
    (_AMP_T["ohlcv"], SIGNAL_AMPLIFIER_CODE["breakout_confirm"]): {
        "vs": "prev_high", "amt_min": cfg.AMP_AMT_HEAVY_RATIO,
        "amt_window": 20, "close_pos_min": cfg.AMP_BREAKOUT_CLOSE_POS,
        "sigma_window": cfg.AMP_RET_SIGMA_WINDOW,
    },
    (_AMP_T["ohlcv"], SIGNAL_AMPLIFIER_CODE["spike_blowoff"]): {
        "axis": "uw", "wick_q": cfg.AMP_WICK_Q,
        "wick_window": cfg.AMP_WICK_WINDOW,
        "wick_min_periods": cfg.AMP_WICK_MIN_PERIODS,
        "close_pos_max": cfg.AMP_BLOWOFF_CLOSE_POS,
        "amt_min": cfg.AMP_AMT_HEAVY_RATIO, "amt_window": 20,
    },
    (_AMP_T["ohlcv"], SIGNAL_AMPLIFIER_CODE["exhaustion_gap"]): {
        "open_bar": cfg.AMP_GAP_OPEN_BAR, "body": "red",
        "sigma_window": cfg.AMP_RET_SIGMA_WINDOW,
    },
    (_AMP_T["ohlcv"], SIGNAL_AMPLIFIER_CODE["dry_rebound"]): {
        "ret": "up", "amt_max": cfg.AMP_AMT_DRY_RATIO, "amt_window": 20,
    },
    (_AMP_T["margin"], SIGNAL_AMPLIFIER_MARGIN_CODE["margin_washout"]): {
        "axis": "margin_balance", "balance_q": cfg.AMP_MARGIN_LOW_Q,
        "window": cfg.AMP_MARGIN_WINDOW,
        "min_periods": cfg.AMP_MARGIN_MIN_PERIODS,
    },
    (_AMP_T["margin"], SIGNAL_AMPLIFIER_MARGIN_CODE["margin_crowding"]): {
        "axis": "margin_balance", "balance_q": cfg.AMP_MARGIN_HIGH_Q,
        "window": cfg.AMP_MARGIN_WINDOW,
        "min_periods": cfg.AMP_MARGIN_MIN_PERIODS,
    },
    # the NEWS amplifier bars (the migrated news_wash rules — the daily
    # states carry the drop-leg window; the weekly ones the window;
    # count bars carry op "above", z bars "below")
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["sent_shock"],)): {
        "kind": "news_state", "metric": "sent_z", "op": "below",
        "bar": cfg.AMP_NEWS_Z_BAR,
        "drop_leg_window": cfg.AMP_NEWS_DROP_LEG_WINDOW,
    },
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["tone_panic"],)): {
        "kind": "news_state", "metric": "lex_tone_z", "op": "below",
        "bar": cfg.AMP_NEWS_TONE_BAR,
        "drop_leg_window": cfg.AMP_NEWS_DROP_LEG_WINDOW,
    },
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["neg_wave"],)): {
        "kind": "news_state", "metric": "flow_neg", "op": "above",
        "bar": cfg.AMP_NEWS_FLOWNEG_BAR,
        "drop_leg_window": cfg.AMP_NEWS_DROP_LEG_WINDOW,
    },
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["quiet_cov"],)): {
        "kind": "news_state", "metric": "flow_af", "op": "below",
        "bar": cfg.AMP_NEWS_AF_CALM_BAR,
        "drop_leg_window": cfg.AMP_NEWS_DROP_LEG_WINDOW,
    },
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["shock_wave"],)): {
        "kind": "news_interaction", "metric": "sent_z", "op": "below",
        "bar": cfg.AMP_NEWS_ZFLOW_Z_BAR,
        "legs": {"flow_neg": cfg.AMP_NEWS_ZFLOW_NEG_BAR},
        "drop_leg_window": cfg.AMP_NEWS_DROP_LEG_WINDOW,
    },
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["wave_disagree"],)): {
        "kind": "news_interaction", "metric": "sent_z", "op": "below",
        "bar": cfg.AMP_NEWS_ZFLOW_Z_BAR,
        "legs": {"flow_neg": cfg.AMP_NEWS_ZFLOW_NEG_BAR,
                 "disp_z": cfg.AMP_NEWS_INTER_DISP_BAR},
        "drop_leg_window": cfg.AMP_NEWS_DROP_LEG_WINDOW,
    },
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["tone_disagree"],)): {
        "kind": "news_interaction", "metric": "lex_tone_z", "op": "below",
        "bar": cfg.AMP_NEWS_TONE_INTER_BAR,
        "legs": {"disp_z": cfg.AMP_NEWS_INTER_DISP_BAR},
        "drop_leg_window": cfg.AMP_NEWS_DROP_LEG_WINDOW,
    },
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["week_tone"],)): {
        "kind": "news_state", "metric": "wt", "op": "below",
        "bar": cfg.AMP_NEWS_WEEKLY_TONE_BAR,
        "weekly_window": cfg.AMP_NEWS_WEEKLY_WINDOW,
    },
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["week_shock"],)): {
        "kind": "news_state", "metric": "wz", "op": "below",
        "bar": cfg.AMP_NEWS_WEEKLY_Z_BAR,
        "weekly_window": cfg.AMP_NEWS_WEEKLY_WINDOW,
    },
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["week_neg_wave"],)): {
        "kind": "news_state", "metric": "wfn", "op": "above",
        "bar": cfg.AMP_NEWS_WEEKLY_FLOWNEG_BAR,
        "weekly_window": cfg.AMP_NEWS_WEEKLY_WINDOW,
    },
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["sent_euphoria"],)): {
        "kind": "news_state", "metric": "sent_z", "op": "above",
        "bar": cfg.AMP_NEWS_POS_Z_BAR,
        "drop_leg_window": cfg.AMP_NEWS_DROP_LEG_WINDOW,
    },
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["tone_euphoria"],)): {
        "kind": "news_state", "metric": "lex_tone_z", "op": "above",
        "bar": cfg.AMP_NEWS_POS_TONE_BAR,
        "drop_leg_window": cfg.AMP_NEWS_DROP_LEG_WINDOW,
    },
    (_AMP_NEWS + (SIGNAL_AMPLIFIER_NEWS_CODE["week_tone_recovery"],)): {
        "kind": "news_state", "metric": "wt", "op": "above",
        "bar": 0.0,
        "weekly_window": cfg.AMP_NEWS_WEEKLY_WINDOW,
        "recovery_lookback": cfg.AMP_NEWS_WEEKLY_TONE_REC_LOOKBACK,
    },
}

# the history-day frame's column vocabulary
AMPLIFY_DAY_COLUMNS = [
    "sec_type", "code", "date", "amplifier_type", "sub_type",
    "gap_pct", "wick_pct", "amt_ratio", "close_pos", "margin_vs_bar",
    "depth",
]


class AmplifierEngine:
    """Detect the amplifier days over a computed anatomy frame (the
    OhlcvAnatomy output with the MarginAnatomy columns attached — the
    ohlcv aids uw_top10/lw_top10/uw_bar/lw_bar/ret_sigma_252 and the
    margin margin_balance/margin_bar_low/margin_bar_high must be
    present; absent margin columns mean no margin amplifier days).

    ``run`` returns one row per (code, date, amplifier_type, sub_type)
    with the day's fact columns (AMPLIFY_DAY_COLUMNS). A day can carry
    several amplifiers (one row each — the PK keeps them separate).
    """

    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    # -- per-amplifier masks (pure bool after the NaN-safe comparisons;
    #    NaN > bar is False so missing facts never qualify a day) -------
    def _masks(self) -> dict[tuple[int, int], pd.Series]:
        f = self.frame
        cols = safe_columns(f)
        heavy = f["amt_ratio_20"] >= cfg.AMP_AMT_HEAVY_RATIO
        down_body = f["gap_oc"] < 0
        up_body = f["gap_oc"] > 0
        up_close = f["ret_1d"] > 0
        lw_top = f["lw_top10"].fillna(0.0) == 1.0
        uw_top = f["uw_top10"].fillna(0.0) == 1.0
        amp = SIGNAL_AMPLIFIER_CODE
        mam = SIGNAL_AMPLIFIER_MARGIN_CODE
        masks: dict[tuple[int, int], pd.Series] = {
            _AMP_OHLCV + (amp["flush_washout"],): (
                lw_top & down_body & heavy
            ),
            _AMP_OHLCV + (amp["gap_reclaim"],): (
                (f["prev_close_gap"] <= -cfg.AMP_GAP_OPEN_BAR)
                & up_body & up_close
            ),
            _AMP_OHLCV + (amp["breakout_confirm"],): (
                (f["close_vs_prev_high"] > 0)
                & heavy
                & (f["close_pos"] >= cfg.AMP_BREAKOUT_CLOSE_POS)
            ),
            _AMP_OHLCV + (amp["spike_blowoff"],): (
                uw_top
                & (f["close_pos"] <= cfg.AMP_BLOWOFF_CLOSE_POS)
                & heavy
            ),
            _AMP_OHLCV + (amp["exhaustion_gap"],): (
                (f["prev_close_gap"] >= cfg.AMP_GAP_OPEN_BAR) & down_body
            ),
            _AMP_OHLCV + (amp["dry_rebound"],): (
                up_close & (f["amt_ratio_20"] <= cfg.AMP_AMT_DRY_RATIO)
            ),
        }
        if "margin_balance" in cols:
            # NaN balance / NaN bars never qualify a day (the codes
            # without margin data carry NULL columns)
            masks[_AMP_MARGIN + (mam["margin_washout"],)] = (
                f["margin_balance"] <= f["margin_bar_low"]
            )
            masks[_AMP_MARGIN + (mam["margin_crowding"],)] = (
                f["margin_balance"] >= f["margin_bar_high"]
            )
        if "sent_z" in cols:
            masks.update(self._news_masks())
        return masks

    # -- the news amplifier masks (NaN-safe state comparisons AND the
    #    bool drop-leg column — a NULL state day never qualifies) -----
    def _news_masks(self) -> dict[tuple[int, int], pd.Series]:
        f = self.frame
        nws = SIGNAL_AMPLIFIER_NEWS_CODE
        leg = f["news_drop_leg"]
        zflow = ((f["sent_z"] <= cfg.AMP_NEWS_ZFLOW_Z_BAR)
                 & (f["flow_neg"] >= cfg.AMP_NEWS_ZFLOW_NEG_BAR))
        return {
            _AMP_NEWS + (nws["sent_shock"],): (
                (f["sent_z"] <= cfg.AMP_NEWS_Z_BAR) & leg
            ),
            _AMP_NEWS + (nws["tone_panic"],): (
                (f["lex_tone_z"] <= cfg.AMP_NEWS_TONE_BAR) & leg
            ),
            _AMP_NEWS + (nws["neg_wave"],): (
                (f["flow_neg"] >= cfg.AMP_NEWS_FLOWNEG_BAR) & leg
            ),
            _AMP_NEWS + (nws["quiet_cov"],): (
                (f["flow_af"] <= cfg.AMP_NEWS_AF_CALM_BAR) & leg
            ),
            _AMP_NEWS + (nws["shock_wave"],): (zflow & leg),
            _AMP_NEWS + (nws["wave_disagree"],): (
                zflow & (f["disp_z"] >= cfg.AMP_NEWS_INTER_DISP_BAR) & leg
            ),
            _AMP_NEWS + (nws["tone_disagree"],): (
                (f["lex_tone_z"] <= cfg.AMP_NEWS_TONE_INTER_BAR)
                & (f["disp_z"] >= cfg.AMP_NEWS_INTER_DISP_BAR) & leg
            ),
            _AMP_NEWS + (nws["week_tone"],): (
                f["wt"] <= cfg.AMP_NEWS_WEEKLY_TONE_BAR
            ),
            _AMP_NEWS + (nws["week_shock"],): (
                f["wz"] <= cfg.AMP_NEWS_WEEKLY_Z_BAR
            ),
            _AMP_NEWS + (nws["week_neg_wave"],): (
                f["wfn"] >= cfg.AMP_NEWS_WEEKLY_FLOWNEG_BAR
            ),
            _AMP_NEWS + (nws["sent_euphoria"],): (
                (f["sent_z"] >= cfg.AMP_NEWS_POS_Z_BAR) & leg
            ),
            _AMP_NEWS + (nws["tone_euphoria"],): (
                (f["lex_tone_z"] >= cfg.AMP_NEWS_POS_TONE_BAR) & leg
            ),
            _AMP_NEWS + (nws["week_tone_recovery"],):
                self._news_recovery_mask(),
        }

    # -- the weekly tone recovery: wt crosses UP through 0 with a deep
    #    wt episode within the lookback rows STRICTLY BEFORE today
    #    (shifted — trigger-time observable), the family's exit rule
    #    verbatim -------------------------------------------------------
    def _news_recovery_mask(self) -> pd.Series:
        f = self.frame
        deep = (f["wt"] <= cfg.AMP_NEWS_WEEKLY_TONE_BAR).fillna(False)
        had_deep = grouped_rolling_agg(
            f.assign(_deep=deep.astype("float64")), ["code"], "_deep",
            cfg.AMP_NEWS_WEEKLY_TONE_REC_LOOKBACK, agg="max",
            min_periods=1,
        )
        wt_prev = grouped_shift(f, ["code"], ["wt"],
                                out_names=["_wt_prev"])["_wt_prev"]
        return ((f["wt"] > 0.0) & (wt_prev <= 0.0)
                & (had_deep.shift(1).fillna(0.0) > 0)).fillna(False)

    # -- per-amplifier depth (the amplifier's own normalizer units) ----
    def _depth(self, label: str) -> pd.Series:
        f = self.frame
        if label == "flush_washout":
            return f["lw"] / f["lw_bar"].where(f["lw_bar"] > 1e-12)
        if label == "gap_reclaim":
            return f["prev_close_gap"].abs() / f["ret_sigma_252"].where(
                f["ret_sigma_252"] > 1e-12)
        if label == "breakout_confirm":
            return f["close_vs_prev_high"] / f["ret_sigma_252"].where(
                f["ret_sigma_252"] > 1e-12)
        if label == "spike_blowoff":
            return f["uw"] / f["uw_bar"].where(f["uw_bar"] > 1e-12)
        if label == "exhaustion_gap":
            return f["prev_close_gap"] / f["ret_sigma_252"].where(
                f["ret_sigma_252"] > 1e-12)
        if label == "dry_rebound":
            # dryness fraction: how far UNDER the dry bar (>= 0 on the
            # mask; the deeper the shrink, the harder the discount)
            return (cfg.AMP_AMT_DRY_RATIO
                    - f["amt_ratio_20"]) / cfg.AMP_AMT_DRY_RATIO
        if label == "margin_washout":
            # the balance's fraction BELOW its q10 bar (>= 0 on the
            # mask; the deeper the washout, the stronger the amplify)
            bar = f["margin_bar_low"]
            return (bar - f["margin_balance"]) / bar.where(bar > 1e-12)
        if label == "margin_crowding":
            # the balance's fraction ABOVE its q90 bar (>= 0 on the mask)
            bar = f["margin_bar_high"]
            return (f["margin_balance"] - bar) / bar.where(bar > 1e-12)
        # the news amplifiers: the state metric's breach past its bar
        # in its OWN units (z units below/above; the fraction beyond
        # for the count bars; the wt value for the recovery). Depth
        # rides the SENTIMENT leg's breach on the interaction pair —
        # the direction-carrying member (the family's convention)
        if label == "sent_shock":
            return cfg.AMP_NEWS_Z_BAR - f["sent_z"]
        if label == "tone_panic":
            return cfg.AMP_NEWS_TONE_BAR - f["lex_tone_z"]
        if label == "neg_wave":
            bar = float(cfg.AMP_NEWS_FLOWNEG_BAR)
            return (f["flow_neg"] - bar) / bar
        if label == "quiet_cov":
            return cfg.AMP_NEWS_AF_CALM_BAR - f["flow_af"]
        if label in ("shock_wave", "wave_disagree"):
            return cfg.AMP_NEWS_ZFLOW_Z_BAR - f["sent_z"]
        if label == "tone_disagree":
            return cfg.AMP_NEWS_TONE_INTER_BAR - f["lex_tone_z"]
        if label == "week_tone":
            return cfg.AMP_NEWS_WEEKLY_TONE_BAR - f["wt"]
        if label == "week_shock":
            return cfg.AMP_NEWS_WEEKLY_Z_BAR - f["wz"]
        if label == "week_neg_wave":
            bar = float(cfg.AMP_NEWS_WEEKLY_FLOWNEG_BAR)
            return (f["wfn"] - bar) / bar
        if label == "sent_euphoria":
            return f["sent_z"] - cfg.AMP_NEWS_POS_Z_BAR
        if label == "tone_euphoria":
            return f["lex_tone_z"] - cfg.AMP_NEWS_POS_TONE_BAR
        if label == "week_tone_recovery":
            return f["wt"].fillna(0.0)
        raise KeyError(label)

    # -- the margin amplifiers' qualifying fact: the balance vs the sub
    #    type's own bar ((balance - bar) / bar — negative below the low
    #    bar, positive above the high bar); NaN where either is NaN ---
    def _margin_vs_bar(self, label: str) -> pd.Series:
        f = self.frame
        if label == "margin_washout":
            bar = f["margin_bar_low"]
        elif label == "margin_crowding":
            bar = f["margin_bar_high"]
        else:
            return None
        return (f["margin_balance"] - bar) / bar.where(bar > 1e-12)

    def run(self) -> pd.DataFrame:
        """Detect the amplifier days (see the class docstring)."""
        if self.frame.empty:
            return pd.DataFrame(columns=AMPLIFY_DAY_COLUMNS)
        by_key = {
            (d.amplifier_type, d.sub_type): d for d in AMPLIFIER_DEFS
        }
        parts: list[pd.DataFrame] = []
        for (a_type, s_type), mask in self._masks().items():
            if (a_type, s_type) not in by_key:
                continue
            label = by_key[(a_type, s_type)].label
            hit = self.frame[mask]
            if hit.empty:
                continue
            gap_pct = (
                hit["prev_close_gap"]
                if label in ("gap_reclaim", "exhaustion_gap") else None
            )
            wick_pct = {
                "flush_washout": hit["lw"],
                "spike_blowoff": hit["uw"],
            }.get(label)
            margin_vs_bar = self._margin_vs_bar(label)
            parts.append(pd.DataFrame({
                "sec_type": hit["sec_type"],
                "code": hit["code"],
                "date": hit["date"],
                "amplifier_type": np.int16(a_type),
                "sub_type": np.int16(s_type),
                "gap_pct": gap_pct if gap_pct is not None else np.nan,
                "wick_pct": wick_pct if wick_pct is not None else np.nan,
                "amt_ratio": hit["amt_ratio_20"],
                "close_pos": hit["close_pos"],
                "margin_vs_bar": (margin_vs_bar[mask]
                                  if margin_vs_bar is not None else np.nan),
                "depth": self._depth(label)[mask],
            }))
        if not parts:
            return pd.DataFrame(columns=AMPLIFY_DAY_COLUMNS)
        out = pd.concat(parts, ignore_index=True)
        return out.sort_values(["code", "date", "amplifier_type",
                                "sub_type"],
                               kind="stable", ignore_index=True)
