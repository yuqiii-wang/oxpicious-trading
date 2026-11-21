"""ScenarioValues — the synthetic-day value recompute
(strategy.forecast_next_day.values).

Per code, the REAL trailing histories (closes; the macd family's
precomputed ema12/ema26; the vol panel's last real ewma_vol level) plus
one synthetic next-day close reproduce, AT THE SYNTHETIC DAY, the exact
value conventions the analysis engines measure on real days:

  rsi(w)        Wilder RSI — EWM alpha=1/W, adjust=False, min_periods=W
                over the close-diff gain/loss series (the
                analysis.mov_ave_rsi build's convention, the pandas
                seed y_0 = x_0 — NOT the classic SMA seed; over the
                fetched 2600-row window the two are numerically
                identical, but the recursion mirrors the build's).
  boll_z(w)     the close's σ-depth z = (C − SMA_W)/σ_W over the
                trailing W closes INCLUDING the day itself, population
                σ (the live tier's on-the-fly convention).
  momentum(w)   ln(C / close_{w rows before}) — the fixed anchors'
                log-return axis ln(1.0 + momentum_{w}days).
  ma_gap(w)     (C − SMA_W)/SMA_W, SMA over the trailing W closes
                including the day (the mov_ave_spreads_detail
                price_vs_ma{W} convention).
  hv20()        rolling std (ddof=1) of the daily LOG returns over the
                trailing 20 returns, ×√252×100 (the vol panel's
                close-to-close realized vol).
  ewma_vol()    RiskMetrics recursion σ² = λσ²_prev + (1−λ)r² on the
                log return, seeded from the panel's last REAL
                ewma_vol, ×√252×100.
  macd_state()  ema12/ema26 extended one day (α = 2/(span+1)), the
                MACD line rebuilt over the fetched window, the
                signal line = EMA9 (adjust=False, y_0 = x_0 seed —
                compute_emas's convention) over it; the cross state +
                the K=5 episode-first-day check + the histogram as a
                pct of close (the family's recorded value).

Plain host float math per code (the sanctioned per-group boundary —
this is a per-code scalar walk, never a frame pipeline). The price
space is the raw daily close (index scope — the mov_ave_spread
PRICE_SOURCE for index IS b.close; an ETF widening needs the adjusted
close threaded through first).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from analysis.analysis_signals.config import (
    MACD_EMA_LONG,
    MACD_EMA_SHORT,
    MACD_EMA_SIGNAL,
)
from analysis.volatility import config as vol_cfg

# the bollinger family's degenerate-σ guard (the live tier's floor)
BOLL_SIGMA_FLOOR = 1e-12

# the K=5 episode absorb window (analysis.trends.REENTRY_EXIT_DAYS) —
# a re-cross inside it is the SAME episode, so the synthetic day only
# fires when no same-side cross sits in the prior 5 real days
MACD_REENTRY_DAYS = 5


@dataclass(frozen=True)
class MacdCrossState:
    """The synthetic day's macd cross evaluation.

    ``cross_up``/``cross_dn`` are the EPISODE-FIRST-DAY states (the
    cross happens AND no same-side cross in the prior K real days);
    ``hist_pct`` is the family's recorded value (the histogram as a
    pct of close); ``sigma_hist`` the trailing σ of |histogram| over
    the fetched window (the depth normalizer — the engine's own is the
    trailing-10y σ; the fetched EMA window is its converged
    approximation)."""

    cross_up: bool
    cross_dn: bool
    hist_pct: float
    sigma_hist: float


class ScenarioValues:
    """One code's synthetic-day value space.

    Built once per code from the REAL histories; every scenario close
    reuses the instance (only the synthetic close varies)."""

    def __init__(
        self,
        closes: list[float],
        ema12: list[float] | None = None,
        ema26: list[float] | None = None,
        ewma_vol_anchor: float | None = None,
    ) -> None:
        self.closes = closes
        self.ema12 = ema12 or []
        self.ema26 = ema26 or []
        self.ewma_vol_anchor = ewma_vol_anchor

    # -- mov_rsi -------------------------------------------------------

    def rsi(self, window: int, synth_close: float) -> float | None:
        """Wilder RSI(window) at the synthetic day (the build's EWM
        convention — see the module docstring). None when the history
        is shorter than the window."""
        closes = self.closes + [synth_close]
        if len(closes) < window + 1:
            return None
        gains: list[float] = []
        losses: list[float] = []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i - 1]
            gains.append(d if d > 0.0 else 0.0)
            losses.append(-d if d < 0.0 else 0.0)
        alpha = 1.0 / window
        ag = gains[0]
        al = losses[0]
        for i in range(1, len(gains)):
            ag += alpha * (gains[i] - ag)
            al += alpha * (losses[i] - al)
        if ag + al <= 0.0:
            return None
        return 100.0 * ag / (ag + al)

    # -- bollinger -----------------------------------------------------

    def boll_z(self, window: int, synth_close: float) -> float | None:
        """The synthetic close's σ-depth z over the trailing W closes
        INCLUDING itself (population σ — the live tier's on-the-fly
        convention)."""
        if len(self.closes) < window - 1:
            return None
        window_prices = self.closes[-(window - 1):] + [synth_close]
        if len(window_prices) < window:
            return None
        sigma = statistics.pstdev(window_prices)
        if sigma <= BOLL_SIGMA_FLOOR:
            return None
        mean = statistics.fmean(window_prices)
        return (synth_close - mean) / sigma

    # -- momentum (fixed anchors only) ----------------------------------

    def momentum_logret(self, rows: int, synth_close: float) -> float | None:
        """ln(synth / close_{rows before}) — the fixed anchor's
        log-return axis ln(1.0 + momentum_{w}days) at the synthetic
        day (the synthetic day sits one row past the last real close,
        so the base is the real close `rows` rows before IT)."""
        idx = len(self.closes) - rows
        if idx < 0:
            return None
        base = self.closes[idx]
        if base <= 0.0:
            return None
        return math.log(synth_close / base)

    # -- ma_cross gaps ---------------------------------------------------

    def ma_gap(self, window: int, synth_close: float) -> float | None:
        """(C − SMA_W)/SMA_W with the SMA over the trailing W closes
        INCLUDING the synthetic day (the price_vs_ma{W} convention)."""
        if len(self.closes) < window - 1:
            return None
        window_prices = self.closes[-(window - 1):] + [synth_close]
        ma = statistics.fmean(window_prices)
        if abs(ma) < 1e-12:
            return None
        return (synth_close - ma) / ma

    # -- volatility ------------------------------------------------------

    def _log_ret(self, synth_close: float) -> float | None:
        if not self.closes or self.closes[-1] <= 0.0 or synth_close <= 0.0:
            return None
        return math.log(synth_close / self.closes[-1])

    def hv20(self, synth_close: float) -> float | None:
        """The vol panel's hv_20 at the synthetic day: rolling std
        (ddof=1) of the last 20 daily log returns (the synthetic
        return included), annualized ×√252×100."""
        ret = self._log_ret(synth_close)
        if ret is None:
            return None
        if len(self.closes) < 20:
            return None
        log_rets = [
            math.log(self.closes[i] / self.closes[i - 1])
            for i in range(len(self.closes) - 19, len(self.closes))
        ] + [ret]
        std = statistics.stdev(log_rets)
        return std * math.sqrt(vol_cfg.ANN_DAYS) * 100.0

    def ewma_vol(self, synth_close: float) -> float | None:
        """The RiskMetrics fast vol at the synthetic day: σ² = λσ²_prev
        + (1−λ)r² on the synthetic log return, seeded from the panel's
        last REAL ewma_vol (de-annualized), ×√252×100."""
        ret = self._log_ret(synth_close)
        if ret is None or self.ewma_vol_anchor is None:
            return None
        lam = vol_cfg.EWMA_LAMBDA
        var_prev = (self.ewma_vol_anchor / 100.0) ** 2 / vol_cfg.ANN_DAYS
        var = lam * var_prev + (1.0 - lam) * ret * ret
        return math.sqrt(var * vol_cfg.ANN_DAYS) * 100.0

    # -- macd ------------------------------------------------------------

    def macd_state(self, synth_close: float) -> MacdCrossState | None:
        """The synthetic day's macd cross evaluation (episode-first-day
        state + the family's recorded value) — None when the EMA
        history is too short to carry the signal-line recursion."""
        if len(self.ema12) < 2 or len(self.ema12) != len(self.ema26):
            return None
        alpha12 = 2.0 / (MACD_EMA_SHORT + 1.0)
        alpha26 = 2.0 / (MACD_EMA_LONG + 1.0)
        # the fetched window's LAST row is the anchor's own EMA row —
        # the synthetic day extends from it
        e12 = self.ema12[-1] + alpha12 * (synth_close - self.ema12[-1])
        e26 = self.ema26[-1] + alpha26 * (synth_close - self.ema26[-1])
        macd = [a - b for a, b in zip(self.ema12, self.ema26)]
        macd.append(e12 - e26)
        # the signal line: EMA9 of the MACD line (adjust=False,
        # y_0 = x_0 seed — compute_emas's convention)
        alpha9 = 2.0 / (MACD_EMA_SIGNAL + 1.0)
        sig = macd[0]
        signal = [sig]
        for m in macd[1:]:
            sig += alpha9 * (m - sig)
            signal.append(sig)
        n = len(macd)
        cross_up = macd[-1] > signal[-1] and macd[-2] <= signal[-2]
        cross_dn = macd[-1] < signal[-1] and macd[-2] >= signal[-2]
        # the K=5 absorb: no same-side cross in the prior 5 real days —
        # a re-cross inside the window is the SAME episode (its first
        # day already fired on the real history)
        lo = max(1, n - 1 - MACD_REENTRY_DAYS)
        prior_up = any(
            macd[i] > signal[i] and macd[i - 1] <= signal[i - 1]
            for i in range(lo, n - 1)
        )
        prior_dn = any(
            macd[i] < signal[i] and macd[i - 1] >= signal[i - 1]
            for i in range(lo, n - 1)
        )
        hist = macd[-1] - signal[-1]
        sigma_hist = statistics.pstdev(
            [abs(m - s) for m, s in zip(macd, signal)],
        )
        return MacdCrossState(
            cross_up=cross_up and not prior_up,
            cross_dn=cross_dn and not prior_dn,
            hist_pct=hist / synth_close * 100.0,
            sigma_hist=max(sigma_hist, 1e-6),
        )
