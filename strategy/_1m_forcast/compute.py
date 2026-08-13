"""Pure forecast computations (no DB access).

Given the last 20 trading days of OHLC + trading_amount + the current RSI +
the position carried into the horizon, produce the 9 scenario schedules
(8 display curves + 1 computed mean) that get written to
``strategy.forecast_1m`` + ``strategy.forecast_1m_stats``.

8 curves:
  - 6 mirror/flip at 3 scale ratios (255d/20d, 0.5*ratio, 1:1)
  - 2 random walks (0.5σ random + opposite trend)

The computed "mean" (average of all 8 per day) drives the sell schedule
persisted to trade_decision + strategy_daily.
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional, Tuple

from strategy._1m_forcast.constants import (
    HORIZON_DAYS,
    LONG_TERM_DAYS,
    SCENARIOS,
    RANDOM_SCENARIOS,
    MEAN_SCENARIO,
    ALL_SCENARIOS,
    DISPLAY_SCENARIOS,
    SELL_SIGNAL_BASELINE,
    CONFIDENCE_SCALE,
    RSI_DRIFT_SCALE,
    MEAN_RANDOM_SCALE,
    MEAN_SEED_BASE,
)


# ---------------------------------------------------------------------------
# Historical stats — pure functions over the OHLC + amt history
# ---------------------------------------------------------------------------
def daily_log_return_std(closes: List[float]) -> float:
    """Population std of daily log returns over the supplied close series."""
    n = len(closes)
    if n < 2:
        return 0.0
    rets: List[float] = []
    for i in range(1, n):
        prev, cur = closes[i - 1], closes[i]
        if prev > 0 and cur > 0:
            rets.append(math.log(cur / prev))
    if len(rets) < 2:
        return 0.0
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / len(rets)
    return math.sqrt(var)


def rolling_log_return_std_max(
    closes: List[float], window: int,
) -> float:
    """Max daily log-return std over rolling windows of ``window`` closes.

    For each position ``i`` from ``window-1`` to ``len(closes)-1``, computes
    the population std of log returns over closes[i-window+1 .. i] and
    returns the maximum. Used to find the peak 255d volatility over the
    past year.
    """
    n = len(closes)
    if n < window:
        # Not enough data for even one full window — fall back to the
        # overall std.
        return daily_log_return_std(closes)
    max_std = 0.0
    for i in range(window - 1, n):
        chunk = closes[i - window + 1 : i + 1]
        s = daily_log_return_std(chunk)
        if s > max_std:
            max_std = s
    return max_std


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _pop_std(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mu = _mean(xs)
    var = sum((x - mu) ** 2 for x in xs) / len(xs)
    return math.sqrt(var)


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = min(len(xs), len(ys))
    if n < 2:
        return None
    mx, my = _mean(xs[:n]), _mean(ys[:n])
    sxx = sum((x - mx) ** 2 for x in xs[:n])
    syy = sum((y - my) ** 2 for y in ys[:n])
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs[:n], ys[:n]))
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _finite(xs: List[float]) -> List[float]:
    return [x for x in xs if x is not None and math.isfinite(x)]


def compute_history_stats(
    ohlc: List[Dict[str, float]],
    ohlc_255d: Optional[List[Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """Compute the 20d + 255d historical stats driving the OHLC/amt/RSI simulation."""
    closes = [r["close"] for r in ohlc]
    sigma_20d = daily_log_return_std(closes)

    if ohlc_255d and len(ohlc_255d) >= 2:
        closes_255d = [r["close"] for r in ohlc_255d]
        sigma_255d = daily_log_return_std(closes_255d)
        # Rolling max 255d std over the past year (peak long-term volatility).
        # Uses the same closes_255d series which has ~507+ rows (fetched by
        # fetch_255d_ohlc with ROLLING_MAX_LOOKBACK). Falls back to sigma_255d
        # when not enough data for a rolling window.
        sigma_255d_max = rolling_log_return_std_max(closes_255d, LONG_TERM_DAYS + 1)
    else:
        sigma_255d = sigma_20d
        sigma_255d_max = sigma_20d

    if sigma_20d <= 0:
        sigma_20d = 0.0001
    if sigma_255d <= 0:
        sigma_255d = sigma_20d
    if sigma_255d_max <= 0:
        sigma_255d_max = sigma_255d

    oc_gaps = _finite([
        (r["close"] - r["open"]) / r["open"]
        for r in ohlc if r["open"] > 0
    ])
    hl_gaps = _finite([
        (r["high"] - r["low"]) / r["low"]
        for r in ohlc if r["low"] > 0
    ])
    amts = _finite([
        float(r["trading_amount"]) for r in ohlc
        if r.get("trading_amount") is not None
    ])

    stats: Dict[str, Any] = {
        "sigma_daily": sigma_20d,
        "sigma_255d": sigma_255d,
        "sigma_255d_max": sigma_255d_max,
        "oc_gap_mean": _mean(oc_gaps) if oc_gaps else 0.0,
        "oc_gap_std": _pop_std(oc_gaps) if oc_gaps else 0.0,
        "hl_gap_mean": _mean(hl_gaps) if hl_gaps else 0.0,
        "hl_gap_std": _pop_std(hl_gaps) if hl_gaps else 0.0,
        "amt_mean": _mean(amts) if amts else None,
        "amt_std": _pop_std(amts) if amts else None,
        "amt_hl_corr": (
            _pearson(hl_gaps[:len(amts)], amts)
            if amts and len(amts) >= 2 else None
        ),
    }
    for k, v in list(stats.items()):
        if v is not None and not math.isfinite(v):
            stats[k] = None
    return stats


# ---------------------------------------------------------------------------
# Std ratio scales — 255d/20d ratio + 0.5*ratio + 1.0
# ---------------------------------------------------------------------------
def compute_scale_ratios(
    sigma_20d: float,
    sigma_255d: float,
    sigma_255d_max: float = 0.0,
) -> Dict[str, float]:
    """Compute the four scale ratios used by mirror/flip scenarios.

    Returns:
      - "255d_std_scale":       sigma_255d / sigma_20d  (current long-term / recent)
      - "255d_std_half_scale":  0.5 * ratio
      - "20d_std_scale":        1.0
      - "255d_max_std_scale":   sigma_255d_max / sigma_20d  (peak 1y rolling 255d std / recent)
    The "255d_max_std_scale" ratio captures the worst-case long-term volatility
    observed over the past year, which may be significantly larger than the
    current 255d std.
    """
    if sigma_20d <= 0 or sigma_255d <= 0:
        return {
            "255d_std_scale": 1.0,
            "255d_std_half_scale": 0.5,
            "20d_std_scale": 1.0,
            "255d_max_std_scale": 1.0,
        }
    ratio = sigma_255d / sigma_20d
    maxstd_ratio = (
        sigma_255d_max / sigma_20d
        if sigma_255d_max > 0 else ratio
    )
    return {
        "255d_std_scale": ratio,
        "255d_std_half_scale": 0.5 * ratio,
        "20d_std_scale": 1.0,
        "255d_max_std_scale": maxstd_ratio,
    }


# ---------------------------------------------------------------------------
# Mirror / Flip OHLC generation from 20d history
# ---------------------------------------------------------------------------
def mirror_flip_ohlc(
    history: List[Dict[str, float]],
    scale: float,
    flip: bool,
) -> List[Dict[str, float]]:
    """Generate 20 forecast OHLC bars by mirroring/flipping the 20d history.

    The 20d history (21 points, 20 returns) is time-reversed and its
    deviation from the anchor close is scaled by ``scale``.

    - **Mirror** (flip=False): future deviation = +scale * (hist_dev reversed)
    - **Flip** (flip=True): future deviation = -scale * (hist_dev reversed)
      (high/low are swapped to maintain OHLC constraints)

    All prices are in forecast-norm (base=100 at the anchor close).
    """
    n = HORIZON_DAYS
    anchor = history[-1]["close"]
    sign = -1.0 if flip else 1.0

    bars: List[Dict[str, float]] = []
    for t in range(1, n + 1):
        hist_idx = len(history) - 1 - t
        if hist_idx < 0:
            hist_idx = 0
        hist = history[hist_idx]

        close_dev = sign * scale * (hist["close"] / anchor - 1.0)
        close_dev = max(-0.95, min(0.95, close_dev))
        close = 100.0 * (1.0 + close_dev)

        if hist_idx + 1 < len(history):
            hist_prev_close = history[hist_idx + 1]["close"]
        else:
            hist_prev_close = anchor
        oc_gap = (hist["open"] - hist_prev_close) / hist_prev_close if hist_prev_close > 0 else 0.0
        oc_gap = max(-0.95, min(0.95, sign * scale * oc_gap))
        prev_fc_close = 100.0 if t == 1 else bars[-1]["close"]
        open_p = prev_fc_close * (1.0 + oc_gap)
        open_p = max(0.01, open_p)

        if flip:
            high_dev = max(-0.95, min(0.95, sign * scale * (hist["low"] / anchor - 1.0)))
            low_dev = max(-0.95, min(0.95, sign * scale * (hist["high"] / anchor - 1.0)))
            high_from_hist = 100.0 * (1.0 + high_dev)
            low_from_hist = 100.0 * (1.0 + low_dev)
        else:
            high_dev = max(-0.95, min(0.95, scale * (hist["high"] / anchor - 1.0)))
            low_dev = max(-0.95, min(0.95, scale * (hist["low"] / anchor - 1.0)))
            high_from_hist = 100.0 * (1.0 + high_dev)
            low_from_hist = 100.0 * (1.0 + low_dev)

        high = max(open_p, close, high_from_hist, 0.01)
        low = max(0.01, min(open_p, close, low_from_hist))
        if high < low:
            high, low = low, high

        bars.append({
            "open": open_p, "high": high, "low": low, "close": close,
        })

    return bars


# ---------------------------------------------------------------------------
# Random walk OHLC: 0.5σ steps on all four OHLC values.
# rand_opp = opposite trend (negate the random step).
# ---------------------------------------------------------------------------
def random_walk_ohlc(
    sigma_daily: float,
    history: List[Dict[str, float]],
    seed: int,
    opposite: bool = False,
) -> List[Dict[str, float]]:
    """Generate 20 forecast OHLC bars via a 0.5σ random walk.

    When ``opposite=True``, the random steps are negated (opposite trend).
    A fixed seed ensures rand and rand_opp use the SAME random sequence
    but with opposite signs, so they diverge over time.
    """
    rng = random.Random(seed)
    step = MEAN_RANDOM_SCALE * sigma_daily
    sign = -1.0 if opposite else 1.0

    hl_ratios = [
        (r["high"] - r["low"]) / r["close"]
        for r in history if r["close"] > 0
    ]
    avg_hl = _mean(hl_ratios) if hl_ratios else 0.02

    bars: List[Dict[str, float]] = []
    close = 100.0
    for t in range(HORIZON_DAYS):
        ret = sign * rng.gauss(0, 1) * step
        new_close = close * (1.0 + ret)

        open_ret = sign * rng.gauss(0, 1) * step
        open_p = close * (1.0 + open_ret)

        center = (open_p + new_close) / 2.0
        hl_half = center * avg_hl * (0.5 + abs(rng.gauss(0, 1)) * 0.5)
        high = max(open_p, new_close) + hl_half
        low = max(0.0, min(open_p, new_close) - hl_half)

        bars.append({
            "open": open_p, "high": high, "low": low, "close": new_close,
        })
        close = new_close

    return bars


# ---------------------------------------------------------------------------
# Trading amount: proportional to |Δclose|, scaled to 20d historical average
# ---------------------------------------------------------------------------
def trading_amt_from_slope(
    bars: List[Dict[str, float]],
    hist_amt_mean: Optional[float],
    hist_closes: List[float],
) -> List[Optional[float]]:
    """Compute trading_amt proportional to the absolute daily close slope."""
    if hist_amt_mean is None or hist_amt_mean <= 0:
        return [None] * len(bars)

    hist_abs_rets = []
    for i in range(1, len(hist_closes)):
        if hist_closes[i - 1] > 0:
            hist_abs_rets.append(abs(hist_closes[i] - hist_closes[i - 1]) / hist_closes[i - 1])
    hist_avg_slope = _mean(hist_abs_rets) if hist_abs_rets else 0.01
    if hist_avg_slope <= 0:
        hist_avg_slope = 0.01

    amts: List[Optional[float]] = []
    for t, bar in enumerate(bars):
        close = bar["close"]
        prev_close = bars[t - 1]["close"] if t > 0 else 100.0
        if prev_close > 0:
            slope = abs(close - prev_close) / prev_close
        else:
            slope = 0
        amt = hist_amt_mean * (slope / hist_avg_slope)
        amt = max(0.1 * hist_amt_mean, amt)
        amts.append(amt)

    return amts


# ---------------------------------------------------------------------------
# Simulated RSI
# ---------------------------------------------------------------------------
def synthetic_rsi_path(
    total_return: float,
    rsi_start: Optional[float],
) -> List[float]:
    """RSI path for one scenario. Drifts proportionally to total return direction."""
    if rsi_start is None:
        rsi_start = 50.0
    drift_sign = max(-1.0, min(1.0, total_return * 5.0))
    out: List[float] = []
    for t in range(HORIZON_DAYS):
        rsi = rsi_start + drift_sign * ((t + 1) / HORIZON_DAYS) * RSI_DRIFT_SCALE
        out.append(max(0.0, min(100.0, rsi)))
    return out


# ---------------------------------------------------------------------------
# SELL schedule (take-profit + baseline; day 20 = 100)
# ---------------------------------------------------------------------------
def sell_schedule(prices: List[float]) -> Dict[str, List[float]]:
    """Convert a scenario price path into a 20-day SELL schedule."""
    h = len(prices)
    raw = [max(0.0, p - 100.0) + SELL_SIGNAL_BASELINE for p in prices]
    total = sum(raw)
    if total <= 0:
        fraction = [1.0 / h] * h
    else:
        fraction = [r / total for r in raw]

    confidence: List[float] = []
    cum_remaining = 1.0
    for f in fraction:
        if cum_remaining <= 1e-12:
            confidence.append(0.0)
            continue
        conf = min(CONFIDENCE_SCALE, f / cum_remaining * CONFIDENCE_SCALE)
        confidence.append(conf)
        cum_remaining -= f
    confidence[-1] = CONFIDENCE_SCALE
    return {"sell_fraction": fraction, "sell_confidence": confidence}


# ---------------------------------------------------------------------------
# P&L forecast — cumulative realized P&L, offset by last actual total_pnl
# ---------------------------------------------------------------------------
def realized_pnl_forecast(
    closes: List[float],
    sell_fraction: List[float],
    total_qty: float,
    cost_basis_norm: float,
    anchor_close: float,
    first_buy_fill_price: Optional[float],
    last_total_pnl: float,
) -> List[float]:
    """Cumulative realized P&L per forecast day, starting at last_total_pnl."""
    if first_buy_fill_price is None or first_buy_fill_price <= 0:
        return [last_total_pnl] * len(closes)
    conv = anchor_close / first_buy_fill_price
    cum = last_total_pnl
    out: List[float] = []
    for t, c in enumerate(closes):
        qty_sold = sell_fraction[t] * total_qty
        bt_norm_close = c * conv
        daily = (qty_sold / 100.0) * (bt_norm_close - cost_basis_norm)
        cum += daily
        out.append(cum)
    return out


# ---------------------------------------------------------------------------
# Top-level: build all 9 scenario schedules (8 curves + 1 mean)
# ---------------------------------------------------------------------------
def compute_forecast(
    stats: Dict[str, Any],
    history_20d: List[Dict[str, float]],
    total_qty: float,
    cost_basis_norm: float,
    anchor_close: float,
    first_buy_fill_price: Optional[float],
    rsi_14: Optional[float],
    last_total_pnl: float,
    seq_id: int,
    forecast_date,
) -> List[Dict[str, Any]]:
    """Build all 9 scenario schedules (8 display curves + 1 computed mean).

    Returns a list of row dicts (one per (scenario, forecast_day)) ready
    for ``upsert.py``.
    """
    sigma_20d = stats["sigma_daily"]
    sigma_255d = stats.get("sigma_255d", sigma_20d)
    sigma_255d_max = stats.get("sigma_255d_max", sigma_255d)
    scales = compute_scale_ratios(sigma_20d, sigma_255d, sigma_255d_max)
    hist_amt_mean = stats.get("amt_mean")
    hist_closes = [r["close"] for r in history_20d]

    # Collect all 8 scenario close paths + OHLC bars for mean computation.
    all_bars: Dict[str, List[Dict[str, float]]] = {}
    all_closes: Dict[str, List[float]] = {}

    # ---- 6 mirror/flip scenarios ----
    for label, scale_key, flip in SCENARIOS:
        scale = scales[scale_key]
        bars = mirror_flip_ohlc(history_20d, scale, flip)
        all_bars[label] = bars
        all_closes[label] = [b["close"] for b in bars]

    # ---- 2 random-walk scenarios (rand + rand_opp with same seed) ----
    fc_epoch = forecast_date.toordinal() if hasattr(forecast_date, "toordinal") else 0
    seed = MEAN_SEED_BASE + seq_id * 1000 + fc_epoch
    rand_bars = random_walk_ohlc(sigma_20d, history_20d, seed, opposite=False)
    rand_opp_bars = random_walk_ohlc(sigma_20d, history_20d, seed, opposite=True)
    all_bars["rand"] = rand_bars
    all_bars["rand_opp"] = rand_opp_bars
    all_closes["rand"] = [b["close"] for b in rand_bars]
    all_closes["rand_opp"] = [b["close"] for b in rand_opp_bars]

    # ---- Compute mean close path (average of all 8 per day) ----
    mean_closes: List[float] = []
    mean_bars: List[Dict[str, float]] = []
    for t in range(HORIZON_DAYS):
        avg_close = _mean([all_closes[sc][t] for sc in DISPLAY_SCENARIOS])
        avg_open = _mean([all_bars[sc][t]["open"] for sc in DISPLAY_SCENARIOS])
        avg_high = max(avg_open, avg_close, _mean([all_bars[sc][t]["high"] for sc in DISPLAY_SCENARIOS]))
        avg_low = max(0.01, min(avg_open, avg_close, _mean([all_bars[sc][t]["low"] for sc in DISPLAY_SCENARIOS])))
        mean_closes.append(avg_close)
        mean_bars.append({"open": avg_open, "high": avg_high, "low": avg_low, "close": avg_close})

    # ---- Build rows for each scenario ----
    rows: List[Dict[str, Any]] = []

    def _build_scenario_rows(
        label: str, bars: List[Dict[str, float]], closes: List[float],
    ) -> None:
        amts = trading_amt_from_slope(bars, hist_amt_mean, hist_closes)
        total_ret = (closes[-1] / 100.0 - 1.0) if closes else 0.0
        rsis = synthetic_rsi_path(total_ret, rsi_14)
        sched = sell_schedule(closes)
        pnls = realized_pnl_forecast(
            closes, sched["sell_fraction"], total_qty,
            cost_basis_norm, anchor_close, first_buy_fill_price,
            last_total_pnl,
        )
        for t in range(HORIZON_DAYS):
            b = bars[t]
            prev_close = closes[t - 1] if t > 0 else 100.0
            daily_ret = (closes[t] / prev_close - 1.0) if prev_close > 0 else 0.0
            rows.append({
                "scenario": label,
                "forecast_day": t + 1,
                "open_price": round(b["open"], 6),
                "high_price": round(b["high"], 6),
                "low_price": round(b["low"], 6),
                "close_price": round(b["close"], 6),
                "daily_return": round(daily_ret, 6),
                "trading_amt": round(amts[t], 4) if amts[t] is not None else None,
                "rsi": round(rsis[t], 6),
                "sell_fraction": round(sched["sell_fraction"][t], 6),
                "sell_confidence": round(sched["sell_confidence"][t], 4),
                "realized_pnl_forecast": round(pnls[t], 6),
                "scenario_weight": None,
                "total_qty": round(total_qty, 4),
                "cost_basis_norm": round(cost_basis_norm, 6),
            })

    # Build rows for all 8 display scenarios.
    for label in DISPLAY_SCENARIOS:
        _build_scenario_rows(label, all_bars[label], all_closes[label])

    # Build rows for the computed mean.
    _build_scenario_rows(MEAN_SCENARIO, mean_bars, mean_closes)

    return rows
