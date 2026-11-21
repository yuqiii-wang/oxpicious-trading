"""ForecastCellEvaluator — the active rule cells vs the scenario values
(strategy.forecast_next_day.evaluation).

The live tier's breach contract projected onto a synthetic close: each
active ``analysis_signals.signal_rules`` cell of the scenario-evaluable
families resolves its CURRENT value from the ScenarioValues recompute,
its BAR from its own ``rule_params.bar_def`` (fixed_sigma / shifted
rolling_quantile over the REAL axis history / the gap_pct constant /
the macd cross event), passes the DAY-CONTEXT gate (the anchor's own
trend + regime — tomorrow's context approximated by today's, a
documented approximation), and the trigger is the cell's own action
against its own bar (sell breaches above, buy below — the live
ABOVE_ACTION convention).

The projected confidence mirrors the live tier's rules_confidence_bp /
the history writer's identical formula
(``live/live_signals/config/breach.py``):

    confidence_bp = base_pct × (1 + depth_factor × min(depth, cap))
                    × sample_scale × 100, clamped ±10000

with the DEPTH UNIT per family = the engines' own normalizers (RSI
points /10, σ units /1, the momentum + volatility axes' own trailing σ,
the gdepth /0.08, the macd histogram in its trailing σ).

Not-evaluable cells (bar kinds outside the scenario space, the
vol-adaptive momentum axes) are counted and logged by the caller —
never invented. The SELL pickup reading (would_execute) applies the
strategy engine's gates: buys always fill (long-only); sells only with
a position open past min_holding_period bars since the last BUY (the
nightly run's position state).
"""
from __future__ import annotations

import logging
import statistics

from pydantic import BaseModel

from _common.labels import ACTION_CODE, REGIME_CODE, TREND_CODE

from analysis.analysis_signals.config import (
    MOMENTUM_BAR_MIN_PERIODS,
    MOMENTUM_BAR_WINDOW_DAYS,
    RSI_BAR_MIN_PERIODS,
    RSI_BAR_WINDOW_DAYS,
    VOL_BAR_MIN_PERIODS,
    VOL_BAR_WINDOW_DAYS,
)

from strategy.forecast_next_day.values import ScenarioValues

logger = logging.getLogger(__name__)

# the engines' depth normalizers (the confidence formula's per-family
# unit — see the module docstring)
RSI_DEPTH_UNIT = 10.0
GDEPTH_DEPTH_UNIT = 0.08

# the ma_cross gdepth windows that ride the scenario space (the cross /
# retest / breadth sub types are event or market-level semantics, not
# close-derivable bars)
MA_GAP_WINDOWS = (120, 252)

# the momentum FIXED anchors (the vol-adaptive short/mid/long axes are
# upstream-chosen lookbacks — not close-derivable for a synthetic day)
MOMENTUM_ANCHOR_ROWS = {"mom5": 5, "mom20": 20}

# the vol family's direction-gate constants (the engine's own)
VOL_SPIKE_LEG_WINDOW = 5
VOL_CRUSH_HIGH_WINDOW = 60
VOL_CRUSH_HIGH_FRAC = 0.98

# the engine's SELL gate default (STRATEGY_PARAMS.min_holding_period —
# the nightly portfolio's trading layer)
MIN_HOLDING_PERIOD = 5

# the confidence record clamp (the live tier's CONFIDENCE_CAP_BP)
CONFIDENCE_CAP_BP = 10_000


def _linear_quantile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated quantile over a pre-sorted list — pandas'
    default (the engines' grouped_rolling_quantile semantics; the live
    tier's _linear_quantile verbatim)."""
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _tail_bars(
    history: list[float], tail_pcts: tuple[int, ...],
    window: int, min_periods: int, buy_is_bottom: bool,
) -> dict[tuple[int, str], float]:
    """The SHIFTED trailing-window tail bars over one axis's REAL
    history: the newest row dropped (the engine's shift-1 — the
    synthetic day never enters its own bar), then the linear quantile
    over the trailing ``window`` values (None-free), keyed
    (tail_pct, 'buy'|'sell'). ``buy_is_bottom`` selects the side
    convention: mov_rsi/momentum breach the BOTTOM tail on buy (buy
    bar = q, sell bar = 1−q); volatility is INVERTED (the spike BUY
    breaches the TOP tail — buy bar = 1−q)."""
    vals = [v for v in history[:-1] if v is not None]
    if len(vals) < min_periods:
        return {}
    vals = sorted(vals[-window:])
    out: dict[tuple[int, str], float] = {}
    for pct in tail_pcts:
        q = pct / 100.0
        bottom = _linear_quantile(vals, q)
        top = _linear_quantile(vals, 1.0 - q)
        out[(pct, "buy")] = bottom if buy_is_bottom else top
        out[(pct, "sell")] = top if buy_is_bottom else bottom
    return out


def _trailing_sigma(
    history: list[float], window: int, min_periods: int,
) -> float | None:
    """The axis's own trailing σ over the REAL history, shifted one row
    (the momentum/volatility depth normalizer convention)."""
    vals = [v for v in history[:-1] if v is not None]
    if len(vals) < min_periods:
        return None
    return statistics.stdev(vals[-window:])


class ScenarioBars(BaseModel):
    """One code's REAL-history bars + depth normalizers — everything the
    cells' bar_defs resolve against, computed once per run."""

    # mov_rsi: (window, tail_pct, 'buy'|'sell') → the RSI tail bar
    rsi: dict[tuple[int, int, str], float]
    # momentum: (rows, tail_pct, side) → the logret tail bar
    momentum: dict[tuple[int, int, str], float]
    # momentum depth normalizer per anchor rows
    momentum_sigma: dict[int, float]
    # volatility: ('hv20'|'ewma', tail_pct, side) → the vol tail bar
    volatility: dict[tuple[str, int, str], float]
    volatility_sigma: dict[str, float]


class PositionState(BaseModel):
    """One code's pickup preconditions from the nightly run's latest
    decisions (would_execute's sell gate)."""

    position_open: bool = False
    # the last BUY's index in the closes ladder (None = no BUY seen)
    last_buy_idx: int | None = None
    # the closes ladder's length (the synthetic day sits AT this index)
    ladder_len: int = 0


class FiredSignal(BaseModel):
    """One fired cell under one scenario — the register row's signal
    half (joined with the ScenarioSpec constants by the writer)."""

    signal_type: str
    signal_sub_type: str
    action: str
    signal_value: float
    signal_threshold: float
    signal_excess_pct: float | None
    confidence_bp: int
    trend_state: int
    regime_state: int
    position_open: bool
    would_execute: bool


class ForecastCellEvaluator:
    """The scenario evaluation: one code's active cells vs one
    scenario's synthetic close."""

    def __init__(
        self,
        cells: list[dict],
        bars: ScenarioBars,
        trend_state: int | None,
        regime_state: int,
    ) -> None:
        self.cells = cells
        self.bars = bars
        self.trend_state = trend_state
        self.regime_state = regime_state

    # -- the shared projected confidence --------------------------------

    @staticmethod
    def _confidence_bp(cell: dict, depth_norm: float) -> int:
        """The projected breach confidence (the rules_confidence_bp
        formula verbatim — see the module docstring)."""
        conf = cell.get("confidence") or {}
        base_pct = float(conf.get("base_pct") or 0.0)
        factor = float(conf.get("depth_factor") or 0.0)
        cap = float(conf.get("depth_cap") or 2.0)
        scale = float(conf.get("sample_scale") or 1.0)
        depth = min(depth_norm, cap)
        return int(min(max(round(
            base_pct * (1.0 + factor * depth) * scale * 100.0,
        ), -CONFIDENCE_CAP_BP), CONFIDENCE_CAP_BP))

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _sub_type_window(sub_type: str) -> int | None:
        """The FIRST integer run of the sub_type label (rsi14 /
        boll20 / ma120_gap — the live tier's parser)."""
        digits = ""
        for ch in sub_type:
            if ch.isdigit():
                digits += ch
            elif digits:
                break
        return int(digits) if digits else None

    def _fired(
        self, cell: dict, value: float, threshold: float,
        depth_unit: float, position: PositionState,
    ) -> FiredSignal:
        excess = value - threshold
        return FiredSignal(
            signal_type=cell["signal_type"],
            signal_sub_type=cell["signal_sub_type"],
            action=cell["action_label"],
            signal_value=round(value, 6),
            signal_threshold=round(threshold, 6),
            signal_excess_pct=(
                round(excess / abs(threshold) * 100.0, 6)
                if threshold != 0.0 else None
            ),
            confidence_bp=self._confidence_bp(
                cell, abs(excess) / depth_unit,
            ),
            trend_state=self.trend_state \
                if self.trend_state is not None else TREND_CODE["unknown"],
            regime_state=self.regime_state,
            position_open=position.position_open,
            would_execute=self._would_execute(cell, position),
        )

    @staticmethod
    def _would_execute(cell: dict, position: PositionState) -> bool:
        """The strategy pickup reading: a buy event always fills
        (long-only entry); a sell event only with a position open past
        min_holding_period bars since the last BUY."""
        if cell["action"] != ACTION_CODE["sell"]:
            return True
        if not position.position_open \
                or position.last_buy_idx is None:
            return False
        # bars held AT the synthetic day (one row past the ladder end)
        bars_held = position.ladder_len - position.last_buy_idx
        return bars_held >= MIN_HOLDING_PERIOD

    # -- the evaluation ---------------------------------------------------

    def evaluate(
        self, values: ScenarioValues, synth_close: float,
        position: PositionState,
    ) -> list[FiredSignal]:
        """Every cell that fires under the synthetic close, in cell
        order. Cells whose value/bar is underivable, whose bar kind is
        outside the scenario space, or which fail the day-context gate
        contribute nothing (the caller counts them for the log)."""
        # the vol family's direction gates (trigger-time observable —
        # backward windows only), shared by every volatility cell
        closes = values.closes
        n = len(closes)
        fear = (
            n > VOL_SPIKE_LEG_WINDOW
            and synth_close < closes[n - VOL_SPIKE_LEG_WINDOW]
        )
        prior_high = (
            max(closes[n - VOL_CRUSH_HIGH_WINDOW:])
            if n >= VOL_CRUSH_HIGH_WINDOW else None
        )
        out: list[FiredSignal] = []
        for cell in self.cells:
            # THE RULES-ERA GATE — the day-context cell match (the live
            # evaluator's contract; the anchor's own context approximates
            # tomorrow's)
            if self.trend_state is None:
                continue
            if cell["trend"] not in (self.trend_state, TREND_CODE["all"]):
                continue
            if cell["regime_state"] not in (
                self.regime_state, REGIME_CODE["all"],
            ):
                continue
            bar_def = cell.get("bar_def") or {}
            kind = bar_def.get("kind")
            family = cell["signal_type"]
            if family == "mov_rsi" and kind == "rolling_quantile":
                fired = self._eval_rsi(cell, bar_def, values,
                                       synth_close, position)
            elif family == "bollinger" and kind == "fixed_sigma":
                fired = self._eval_boll(cell, bar_def, values,
                                        synth_close, position)
            elif family == "momentum" and kind == "rolling_quantile":
                fired = self._eval_momentum(cell, bar_def, values,
                                            synth_close, position)
            elif family == "volatility" and kind == "rolling_quantile":
                fired = self._eval_volatility(
                    cell, bar_def, values, synth_close, position,
                    fear, prior_high,
                )
            elif family == "ma_cross" and kind == "gap_pct":
                fired = self._eval_gap(cell, bar_def, values,
                                       synth_close, position)
            elif family == "macd" and kind == "macd_cross":
                fired = self._eval_macd(cell, values, synth_close,
                                        position)
            else:
                continue
            if fired is not None:
                out.append(fired)
        return out

    # -- per-family evaluations -------------------------------------------

    def _eval_rsi(
        self, cell: dict, bar_def: dict, values: ScenarioValues,
        synth_close: float, position: PositionState,
    ) -> FiredSignal | None:
        window = cell.get("window") \
            or self._sub_type_window(cell["signal_sub_type"])
        if window is None:
            return None
        value = values.rsi(window, synth_close)
        if value is None:
            return None
        pct = int(bar_def["tail_pct"])
        bar = self.bars.rsi.get((window, pct, cell["action_label"]))
        if bar is None:
            return None
        triggered = (
            value > bar if cell["action"] == ACTION_CODE["sell"]
            else value < bar
        )
        if not triggered:
            return None
        return self._fired(cell, value, bar, RSI_DEPTH_UNIT, position)

    def _eval_boll(
        self, cell: dict, bar_def: dict, values: ScenarioValues,
        synth_close: float, position: PositionState,
    ) -> FiredSignal | None:
        window = self._sub_type_window(cell["signal_sub_type"])
        if window is None:
            return None
        value = values.boll_z(window, synth_close)
        if value is None:
            return None
        k = float(bar_def["k"])
        bar = k if cell["action"] == ACTION_CODE["sell"] else -k
        triggered = (
            value > bar if cell["action"] == ACTION_CODE["sell"]
            else value < bar
        )
        if not triggered:
            return None
        return self._fired(cell, value, bar, 1.0, position)

    def _eval_momentum(
        self, cell: dict, bar_def: dict, values: ScenarioValues,
        synth_close: float, position: PositionState,
    ) -> FiredSignal | None:
        # only the FIXED anchors are close-derivable (mom5 / mom20)
        rows = MOMENTUM_ANCHOR_ROWS.get(
            cell["signal_sub_type"].rsplit("_", 1)[0],
        )
        if rows is None:
            return None
        value = values.momentum_logret(rows, synth_close)
        if value is None:
            return None
        pct = int(bar_def["tail_pct"])
        bar = self.bars.momentum.get((rows, pct, cell["action_label"]))
        sigma = self.bars.momentum_sigma.get(rows)
        if bar is None or sigma is None:
            return None
        triggered = (
            value > bar if cell["action"] == ACTION_CODE["sell"]
            else value < bar
        )
        if not triggered:
            return None
        return self._fired(cell, value, bar, sigma, position)

    def _eval_volatility(
        self, cell: dict, bar_def: dict, values: ScenarioValues,
        synth_close: float, position: PositionState,
        fear: bool, prior_high: float | None,
    ) -> FiredSignal | None:
        prefix = cell["signal_sub_type"].rsplit("_", 1)[0]
        if prefix not in ("hv20", "ewma"):
            return None
        value = (
            values.hv20(synth_close) if prefix == "hv20"
            else values.ewma_vol(synth_close)
        )
        if value is None:
            return None
        pct = int(bar_def["tail_pct"])
        bar = self.bars.volatility.get(
            (prefix, pct, cell["action_label"]),
        )
        sigma = self.bars.volatility_sigma.get(prefix)
        if bar is None or sigma is None:
            return None
        is_sell = cell["action"] == ACTION_CODE["sell"]
        # the DIRECTION GATES (the engine's own): the spike BUY needs
        # the fear leg (price below its 5-rows-ago close); the crush
        # SELL needs the stretch leg (within FRAC of the prior 60-row
        # high — a naked low-vol sell would fire through every calm
        # uptrend)
        if is_sell:
            if prior_high is None \
                    or synth_close < prior_high * VOL_CRUSH_HIGH_FRAC:
                return None
        elif not fear:
            return None
        triggered = value > bar if is_sell else value < bar
        if not triggered:
            return None
        return self._fired(cell, value, bar, sigma, position)

    def _eval_gap(
        self, cell: dict, bar_def: dict, values: ScenarioValues,
        synth_close: float, position: PositionState,
    ) -> FiredSignal | None:
        window = self._sub_type_window(cell["signal_sub_type"])
        if window not in MA_GAP_WINDOWS:
            return None
        value = values.ma_gap(window, synth_close)
        if value is None:
            return None
        bar = float(bar_def["below"])
        # gdepth is BUY-ONLY (deep gaps ABOVE the MA measured negative)
        if cell["action"] != ACTION_CODE["buy"]:
            return None
        if value > bar:
            return None
        return self._fired(cell, value, bar, GDEPTH_DEPTH_UNIT, position)

    def _eval_macd(
        self, cell: dict, values: ScenarioValues, synth_close: float,
        position: PositionState,
    ) -> FiredSignal | None:
        state = values.macd_state(synth_close)
        if state is None:
            return None
        is_sell = cell["action"] == ACTION_CODE["sell"]
        crossed = state.cross_dn if is_sell else state.cross_up
        if not crossed:
            return None
        # the recorded value = the histogram as a pct of close; the
        # reference is 0.0 (the cross itself); the depth unit = the
        # trailing σ of the histogram in the SAME pct-of-close units
        # (the engine's own depth is the 10y σ of the price-unit
        # histogram — identical in ratio terms)
        sigma_pct = state.sigma_hist / synth_close * 100.0
        return self._fired(
            cell, state.hist_pct, 0.0, sigma_pct, position,
        )
