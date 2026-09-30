"""SignalQuality — the FINAL quality gate of analyze.analysis_signals
(analyze.analysis_signals.engines._quality).

ONE strategy must pass this gate to register as a signal strategy
(engines._base emit_snapshot applies it right after the plain mixed-row
gate, BEFORE the strategy/history split — history events only ever
come from quality-passing strategies).

The 2026-09 claim-profile gate (docs/signal_quality_v2_proposal.md)
replaced the former one-size composite (whose perfect-hard bar let any
single period's misalignment — e.g. the 10%-weight 20d leg — veto a
clean 5d claim). The checks now follow each bucket's OWN claim
horizon:

    keep = breach coherence
         & mean alignment at the 5d claim            (every family)
         & mean alignment at the 20d claim           (SLOW claims only)
         & the risk-cap verdict                      (unchanged)

The slow split comes from the ENGINE's ``slow_claim`` spec —
(window column, minimum window): rows whose window reaches the minimum
claim the 20d horizon and must align on it; fast rows (rsi W <= 10)
claim only the 5d reversal; the one-day pair-cross families carry no
slow claim at all. ``next`` never gates (a top-1% rsi3 day routinely
drifts up the following day while the 5d reversal is real).

Per-period risk-cap bit (per-side caps): the ADVERSE extreme of
the period's forward endpoint changes must stay under the side's cap
fraction of the FAVORABLE extreme:
    buy  → if min_change < 0 then |min_change| < 0.75·|max_change|
    sell → if max_change > 0 then |max_change| < 0.90·|min_change|
(min >= 0 for a buy / max <= 0 for a sell is trivially fine — no
adverse extreme exists; and a period entirely against the side —
max <= 0 for a buy, min >= 0 for a sell — can never pass, since
|adverse| >= |favorable| there). NOT APPLICABLE to 'next', whose
max/min are NULL (no close-based high/low at the 1-day horizon) —
non-applicable periods are excluded from the weighted sum, not failed.

The verdict blends the risk periods by PERIOD_WEIGHTS (the mixed row's
own 5d 0.65 / next 0.25 / 20d 0.10) and fails only when the weighted
sum drops under RISK_WEIGHT_MIN. With the current weights the
achievable sums are {0, .10, .65, .75}: the 5d bar carries the
decision — 5d failing can never reach the bar, while a 20d failure
alone stays above it.

NaN convention: hard-check inputs compare False, so a bucket missing a
period's breach/mean inputs still fails (the repo's NaN convention); a
NaN risk period contributes 0 to the weighted sum — it degrades the
verdict like a failed period, and a bucket with no risk inputs at all
stays under the bar.

All checks are fully vectorized (native cudf ops, no row loops, no
if/else over rows).
"""
from __future__ import annotations

import pandas as pd

from analyze.analysis_signals.config import SELL_SIDES

# The per-period weights of the risk-cap verdict (the non-applicable
# 'next' is excluded from the sum). Mirrors the forecast config's
# MIXED_HORIZON_WEIGHTS (5d 0.65 / next 0.25 / 20d 0.10 — the
# 2026-09-20 rebalance that retired the 60d leg).
PERIOD_WEIGHTS: dict[str, float] = {
    "5d": 0.65,
    "next": 0.25,
    "20d": 0.10,
}

# The periods whose close-based max/min forward change exists — the
# risk cap is NOT APPLICABLE to 'next' (NULL at the 1-day horizon);
# non-applicable periods are excluded from the weighted sum rather
# than failed.
MAXMIN_PERIODS: tuple[str, ...] = ("5d", "20d")

# The adverse extreme must stay under this fraction of the favorable
# extreme in EVERY risk period — the reward must dominate the risk by
# strictly more than 1/cap. Per SIDE (2026-09 v2.1 fine-tune): a sell's
# adverse leg is a continued run-UP — the leg that positive drift
# inflates — so sells tolerate a run-up up to 0.90 of the drop while
# buys keep the stricter 0.75 drawdown bound
# (temp_scripts/study_sell_floors.py: the relaxation admits ~2x sells
# at an undiluted mean edge).
BUY_RISK_CAP: float = 0.75
SELL_RISK_CAP: float = 0.90

# The total weight the risk-cap verdict carries (= the PERIOD_WEIGHTS
# mass of the risk periods).
RISK_WEIGHT_TOTAL: float = sum(
    PERIOD_WEIGHTS[p] for p in MAXMIN_PERIODS
)

# The passing bar of the weight-blended risk-cap verdict: the check
# fails only when Σ_p weight_p · risk_bit_p drops under this. With the
# current weights the achievable sums are {0, .10, .65, .75} — 5d must
# pass; a 20d failure alone stays at or above the bar.
RISK_WEIGHT_MIN: float = 0.50

# The 5d claim every family's mean alignment must satisfy (the
# required-period profile's constant leg).
FAST_REQUIRED_PERIODS: tuple[str, ...] = ("5d",)


class SignalQuality:
    """The final quality gate (see module docstring): gate() keeps the
    buckets whose claim-profile checks and risk-cap verdict pass."""

    def gate(
        self,
        passing: pd.DataFrame,
        slow_claim: tuple[str, int] | None = None,
    ) -> pd.DataFrame:
        """The FINAL gate: keep the strategy rows whose breach
        coherence, required-period mean alignment and risk-cap verdict
        pass. ``slow_claim`` = (window column, minimum window) — rows
        at or beyond the minimum carry the 20d claim too; None = no
        row carries a slow claim."""
        if passing.empty:
            return passing
        keep = self._breach_bit(passing) & self._mean_bit(passing, "5d")
        if slow_claim is not None:
            col, min_window = slow_claim
            slow = self._num(passing[col]) >= min_window
            keep = keep & (~slow | self._mean_bit(passing, "20d"))
        keep = keep & self._risk_bit(passing)
        return passing[keep].reset_index(drop=True)

    # ---- checks (vectorized 0/1 bits; NaN inputs → False) ---------------------

    def _num(self, s: pd.Series) -> pd.Series:
        """The column as native float64 (asyncpg NULLs land as None and
        leave a column object-dtyped — the frame contract demands the
        native numeric dtype; None → NaN, whose comparisons stay
        False)."""
        return s.astype("float64")

    def _breach_bit(self, bucket: pd.DataFrame) -> pd.Series:
        """Breach coherence on the WINDOW-END trigger: an up breach
        (excess > 0) must sit on a sell side (top/upper), a down breach
        (excess < 0) on a buy side."""
        sell = bucket["side"].isin(SELL_SIDES)
        excess = self._num(bucket["trig_excess"])
        return (sell & (excess > 0)) | (~sell & (excess < 0))

    def _mean_bit(self, bucket: pd.DataFrame, period: str) -> pd.Series:
        """Mean alignment with the reversal: after a sell the period's
        forward mean must be negative; after a buy, positive."""
        sell = bucket["side"].isin(SELL_SIDES)
        ave = self._num(bucket["ave_" + period])
        return (sell & (ave < 0)) | (~sell & (ave > 0))

    def _maxmin_bit(self, bucket: pd.DataFrame, period: str) -> pd.Series:
        """Per-period risk cap (see module docstring for the buy/sell
        conditional forms) — ONE bit blended into the weighted
        verdict. The adverse/favorable ratio bound is per side
        (BUY_RISK_CAP / SELL_RISK_CAP)."""
        sell = bucket["side"].isin(SELL_SIDES)
        max_chg = self._num(bucket["max_" + period])
        min_chg = self._num(bucket["min_" + period])
        mx, mn = max_chg.abs(), min_chg.abs()
        cap = pd.Series(BUY_RISK_CAP, index=bucket.index)
        cap = cap.mask(sell, SELL_RISK_CAP)
        # buy: no drawdown (min >= 0) is trivially fine; a drawdown
        # must stay under the cap fraction of the upside (max <= 0
        # forces mx <= mn, so an all-down period never passes).
        buy_bit = (min_chg >= 0) | (mn < cap * mx)
        # sell: no run-up (max <= 0) is trivially fine; a run-up must
        # stay under the cap fraction of the downside (min >= 0 forces
        # mn <= mx, so an all-up period never passes).
        sell_bit = (max_chg <= 0) | (mx < cap * mn)
        return sell_bit.where(sell, buy_bit)

    def _risk_bit(self, bucket: pd.DataFrame) -> pd.Series:
        """The weight-blended risk-cap verdict: Σ_p weight_p ·
        maxmin_bit_p over the risk periods, under RISK_WEIGHT_MIN →
        fail (see module docstring — the 5d bar carries the
        decision)."""
        weighted = pd.Series(0.0, index=bucket.index)
        for period in MAXMIN_PERIODS:
            weighted = weighted + (
                PERIOD_WEIGHTS[period]
                * self._maxmin_bit(bucket, period).astype("float64")
            )
        return weighted >= RISK_WEIGHT_MIN


__all__ = [
    "SignalQuality",
    "PERIOD_WEIGHTS",
    "MAXMIN_PERIODS",
    "BUY_RISK_CAP",
    "SELL_RISK_CAP",
    "RISK_WEIGHT_TOTAL",
    "RISK_WEIGHT_MIN",
    "FAST_REQUIRED_PERIODS",
]
