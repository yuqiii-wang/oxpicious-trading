"""study_kit.studies — the finished, often-used study calculations.

Plain pandas over the fetched Series (no cudf in the sandbox — the
container runs stock pandas 2.3). Every function takes/returns pandas
objects and bakes in the house conventions:

- percentiles/z-scores anchor against the SECURITY'S OWN trailing
  window (``trailing_pct_rank`` / ``zscore``) — never absolute levels;
- forward returns are the simple ``close.shift(-h)/close - 1`` family,
  plus ``forward_blend`` with the repo's measured dir_ave weights
  (0/3/5/10/20d ← 0.10/0.15/0.50/0.15/0.10) — the blend every signal
  cell is judged by;
- MA semantics: read the precomputed columns ``study_kit.db``
  already returns (ma5/20/60/120/255, ema12/26) when they cover the
  window; these helpers compute their own only for other windows.

Verdicts (``spread_report``) speak the house language: a spread in the
trailing-window tails is STRETCHED (mean-reversion pressure builds with
the stretch; sharp falls revert — snapbacks live at the deep-discount
tail), the middle band is NEUTRAL/COMPRESSED.
"""
from __future__ import annotations

from typing import Iterable, Tuple

import numpy as np
import pandas as pd

# The repo's measured forward blend (dir_ave): horizon → weight.
FORWARD_BLEND_WEIGHTS: dict[int, float] = {
    0: 0.10, 3: 0.15, 5: 0.50, 10: 0.15, 20: 0.10,
}


def sma(s: pd.Series, w: int) -> pd.Series:
    """Simple moving average over window w."""
    return s.rolling(w).mean()


def ema(s: pd.Series, span: int) -> pd.Series:
    """Exponential moving average (pandas convention, adjust=False)."""
    return s.ewm(span=span, adjust=False).mean()


def wilder_rsi(close: pd.Series, w: int = 14) -> pd.Series:
    """Wilder RSI over window w (the repo's RSI convention: Wilder
    smoothing, alpha = 1/w). Zones: >70 overbought, <30 oversold —
    read as exhaustion/capitulation context, not bare signals."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / w, adjust=False,
                        min_periods=w).mean()
    avg_loss = loss.ewm(alpha=1.0 / w, adjust=False,
                        min_periods=w).mean()
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """The classic MACD (EMA(fast) − EMA(slow), its EMA(signal), the
    histogram) as a {macd, signal, hist} DataFrame."""
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig,
                         "hist": line - sig})


def roc(close: pd.Series, n: int = 20) -> pd.Series:
    """n-period rate of change, in percent."""
    return close.pct_change(n) * 100.0


def zscore(s: pd.Series, w: int) -> pd.Series:
    """Each point's z-score against its own trailing w-point window."""
    mean = s.rolling(w).mean()
    std = s.rolling(w).std()
    return (s - mean) / std


def trailing_pct_rank(s: pd.Series, w: int) -> pd.Series:
    """Each point's percentile rank (0..1) within its own trailing
    w-point window — the house anchoring convention: 'how unusual is
    today vs this security's own past year', never an absolute bar."""
    return s.rolling(w).rank(pct=True)


def drawdown(close: pd.Series) -> pd.Series:
    """Drawdown from the running maximum (≤ 0, as a fraction)."""
    return close / close.cummax() - 1.0


def forward_return(
    close: pd.Series,
    horizons: Iterable[int] = (1, 3, 5, 10, 20),
) -> pd.DataFrame:
    """Forward close-to-close returns per horizon, as fractions — the
    realized future, so only rows with the FULL horizon realized are
    measurable (the tail rows are NaN by construction, not missing
    data)."""
    out = {f"fwd{h}": close.shift(-h) / close - 1.0
           for h in horizons}
    return pd.DataFrame(out, index=close.index)


def forward_blend(
    close: pd.Series,
    weights: dict[int, float] | None = None,
) -> pd.Series:
    """The repo's measured forward blend (dir_ave): the weighted mix of
    the 0/3/5/10/20d forward returns — what every analysis_signals cell
    is measured against. Rows without the full 20d future are NaN."""
    w = FORWARD_BLEND_WEIGHTS if weights is None else weights
    blended = pd.Series(0.0, index=close.index)
    for horizon, weight in w.items():
        blended = blended + weight * (close.shift(-horizon) / close - 1.0)
    realized = close.shift(-max(w)).notna()
    return blended.where(realized)


def ma_alignment(
    close: pd.Series,
    windows: Tuple[int, ...] = (20, 60, 120),
) -> pd.Series:
    """Per-row alignment verdict over close + SMA(windows): strictly
    close > ma(1st) > ma(2nd) > … = 'bullish' (多头排列 — pullbacks into
    the stack are the buyable zones), the reverse = 'bearish'
    (rallies sold into), anything else = 'mixed'."""
    cols = {"close": close}
    for w in windows:
        cols[f"ma{w}"] = sma(close, w)
    frame = pd.DataFrame(cols)
    ordered = ["close", *(f"ma{w}" for w in windows)]
    bullish = frame[ordered[0]].notna()
    bearish = bullish.copy()
    for prev, nxt in zip(ordered, ordered[1:]):
        bullish &= frame[prev] > frame[nxt]
        bearish &= frame[prev] < frame[nxt]
    return pd.Series(
        np.select([bullish, bearish], ["bullish", "bearish"],
                  default="mixed"),
        index=close.index,
    )


def spread_report(
    close: pd.Series,
    code: str = "",
    windows: Tuple[int, ...] = (20, 60, 120, 255),
    trailing: int = 252,
) -> pd.DataFrame:
    """The MA-spread regime read, one row per window — the standard
    opening move of an MA study (value + own-history percentile + z +
    verdict per price−MA spread).

    verdict: 'stretched-high' / 'stretched-low' when the spread sits in
    the trailing window's top/bottom decile (extension: premium invites
    profit-taking, deep discount is the oversold stretch snapbacks come
    from), 'neutral' otherwise. Rows need the window AND the trailing
    year realized — a shorter history returns fewer rows.
    """
    rows = []
    for w in windows:
        spread = close / sma(close, w) - 1.0
        rank = trailing_pct_rank(spread, trailing)
        z = zscore(spread, trailing)
        verdict = pd.Series(
            np.select(
                [rank >= 0.90, rank <= 0.10],
                ["stretched-high", "stretched-low"],
                default="neutral",
            ),
            index=close.index,
        )
        last = spread.last_valid_index()
        rows.append({
            "code": code,
            "window": w,
            "spread_pct": None if last is None else round(
                float(spread.loc[last]) * 100.0, 3),
            "pct_rank": None if last is None else round(
                float(rank.loc[last]), 3),
            "zscore": None if last is None else round(
                float(z.loc[last]), 2),
            "verdict": None if last is None else verdict.loc[last],
        })
    return pd.DataFrame(rows).dropna(subset=["verdict"]).set_index("window")
