"""llm_agents.deep_agents.tools.stats_tools.calcs — the standard
calculations behind the stats tools, plain host numpy (the deep_agents
package carries NO pandas/cudf — harness layering rule; these arrays
are ≤ ~1700 floats, one security's bars).

Every convention mirrors the repo's established semantics exactly:

- EMA is the pandas ``ewm(span, adjust=False)`` recursion
  (``y0 = x0``; ``y_t = (1-a) y_{t-1} + a x_t``) — the semantics of
  ``stats.index_tech_stats``'s ema columns and study_kit.studies;
- RSI is Wilder smoothing (``alpha = 1/w``, min_periods = w) over the
  house windows 3/6/10/14/20/60 — ``analysis.mov_ave_rsi``;
- anchoring is ALWAYS the security's own trailing window
  (``trailing_pct_rank`` / ``zscore`` tails, 252 trading days) — never
  absolute levels; verdict bands: >=90th pct 'stretched-high',
  <=10th 'stretched-low', else 'neutral' (study_kit.spread_report);
- momentum is ``price[t]/price[t-n] - 1`` (analysis.mov_ave_momentum);
- alignment: strictly ``close > ma(w1) > ma(w2) > …`` = bullish
  (多头排列), the reverse = bearish (空头排列).

The scalar recursions (EMA/RSI) are structural — Wilder smoothing has
no vectorized form; over ~1700 floats a python loop is the honest
shape (NOT dataframe code — the cudf layout preference does not apply
in the harness).
"""
from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple

import numpy as np

# The trailing anchor: ~one trading year.
TRAILING = 252
# Minimum realized points before a percentile/z is meaningful.
_TAIL_FLOOR = 30


def sma_series(x: np.ndarray, w: int) -> np.ndarray:
    """Simple moving average over w points (NaN until w-1)."""
    out = np.full(x.shape, np.nan, dtype=np.float64)
    if x.size >= w:
        cs = np.concatenate(([0.0], np.cumsum(x)))
        out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


def ema_series(x: np.ndarray, span: int) -> np.ndarray:
    """Exponential moving average, pandas ``ewm(span, adjust=False)``
    semantics (never NaN once started)."""
    a = 2.0 / (span + 1.0)
    out = np.empty(x.shape, dtype=np.float64)
    out[0] = x[0]
    for i in range(1, x.size):
        out[i] = (1.0 - a) * out[i - 1] + a * x[i]
    return out


def wilder_rsi_series(close: np.ndarray, w: int) -> np.ndarray:
    """Wilder RSI over window w (alpha = 1/w, min_periods = w; NaN
    until w observations — the pandas ewm emit point). avg_loss 0 with
    gains → RSI 100; a flat stretch (0/0) → NaN."""
    delta = np.diff(close)
    gain = np.where(delta > 0.0, delta, 0.0)
    loss = np.where(delta < 0.0, -delta, 0.0)
    a = 1.0 / w
    avg_gain = np.empty(delta.shape, dtype=np.float64)
    avg_loss = np.empty(delta.shape, dtype=np.float64)
    avg_gain[0] = gain[0]
    avg_loss[0] = loss[0]
    for i in range(1, delta.size):
        avg_gain[i] = (1.0 - a) * avg_gain[i - 1] + a * gain[i]
        avg_loss[i] = (1.0 - a) * avg_loss[i - 1] + a * loss[i]
    out = np.full(close.shape, np.nan, dtype=np.float64)
    if close.size > w:
        g = avg_gain[w - 1: close.size - 1]
        l = avg_loss[w - 1: close.size - 1]
        with np.errstate(divide="ignore", invalid="ignore"):
            rs = g / l
            raw = 100.0 - 100.0 / (1.0 + rs)
        out[w:] = np.where(l > 0.0, raw,
                           np.where(g > 0.0, 100.0, np.nan))
    return out


def macd_series(close: np.ndarray, fast: int = 12, slow: int = 26,
                signal: int = 9) -> Tuple[np.ndarray, np.ndarray,
                                          np.ndarray]:
    """The classic MACD: (line, signal, hist) — line = EMA(fast) −
    EMA(slow), signal = EMA(signal-span) of the line."""
    line = ema_series(close, fast) - ema_series(close, slow)
    sig = ema_series(line, signal)
    return line, sig, line - sig


def roc_series(close: np.ndarray, n: int) -> np.ndarray:
    """n-period rate of change, in percent (NaN until n)."""
    out = np.full(close.shape, np.nan, dtype=np.float64)
    if close.size > n:
        out[n:] = (close[n:] / close[:-n] - 1.0) * 100.0
    return out


def _tail(x: np.ndarray, w: int) -> np.ndarray:
    """The last w VALID points of a series (computed windows lead with
    NaN; those are warmup, not data)."""
    return x[~np.isnan(x)][-w:]


def pct_rank_last(x: np.ndarray, w: int = TRAILING) -> Optional[float]:
    """The last value's percentile rank (0..1, average-rank ties)
    within its own trailing w valid points — None under the floor."""
    valid = _tail(x, w)
    if valid.size < _TAIL_FLOOR:
        return None
    v = valid[-1]
    less = int(np.count_nonzero(valid < v))
    eq = int(np.count_nonzero(valid == v))
    return (less + (eq + 1) / 2.0) / valid.size


def zscore_last(x: np.ndarray, w: int = TRAILING) -> Optional[float]:
    """The last value's z-score against its own trailing w valid
    points (ddof = 1, the pandas rolling-std convention) — None under
    the floor or on a zero-σ window."""
    valid = _tail(x, w)
    if valid.size < _TAIL_FLOOR:
        return None
    std = float(np.std(valid, ddof=1))
    if std == 0.0:
        return None
    return (float(valid[-1]) - float(np.mean(valid))) / std


def stretch_verdict(pct_rank: Optional[float]) -> Optional[str]:
    """The house band: top/bottom decile of the trailing window is
    STRETCHED (extension invites profit-taking; the deep-discount tail
    is where snapbacks live), the middle band neutral."""
    if pct_rank is None:
        return None
    if pct_rank >= 0.90:
        return "stretched-high"
    if pct_rank <= 0.10:
        return "stretched-low"
    return "neutral"


def alignment_verdict(levels: Sequence[Optional[float]]) -> Optional[str]:
    """Strict stack check over [close, ma(w1), ma(w2), …] (windows
    ascending): every '>' = bullish 多头排列, every '<' = bearish
    空头排列, anything else mixed; None when a level is missing."""
    if any(v is None for v in levels) or len(levels) < 2:
        return None
    floats = [float(v) for v in levels]
    if all(a > b for a, b in zip(floats, floats[1:])):
        return "bullish"
    if all(a < b for a, b in zip(floats, floats[1:])):
        return "bearish"
    return "mixed"


def ma_series_for(bars_close: np.ndarray,
                  pre: Dict[str, np.ndarray], method: str,
                  window: int) -> Tuple[np.ndarray, bool]:
    """The MA series at one window — the view's PRECOMPUTED column
    when it covers the window (ma5/20/60/120/255 SMA,
    ema6/12/20/26/60/120/255), else computed here. Returns
    (series, computed_flag)."""
    if method == "sma" and f"ma{window}" in pre:
        return pre[f"ma{window}"], False
    if method == "ema" and f"ema{window}" in pre:
        return pre[f"ema{window}"], False
    if method == "sma":
        return sma_series(bars_close, window), True
    return ema_series(bars_close, window), True
