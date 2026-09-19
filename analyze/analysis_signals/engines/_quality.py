"""SignalQuality — the FINAL quality gate of analyze.analysis_signals
(analyze.analysis_signals.engines._quality).

ONE strategy must pass this gate to register as a signal strategy
(engines._base emit_month applies it right after the plain mixed-row
gate, BEFORE the strategy/history split — history events only ever
come from quality-passing strategies).

The composite quality score of one bucket has TWO components:

    score = (1 - RISK_WEIGHT_TOTAL) · hard score   (breach coherence +
             mean alignment, blended by PERIOD WEIGHTS — short
             horizons carry the decision: 5d 50% · next 30% ·
             20d 15% · 60d 5%)
          + RISK_WEIGHT_TOTAL · risk-cap verdict    (ONE weight-blended
             bit over the 5d/20d/60d periods)

The gate holds at MIN_SCORE — 1.0 strict, so the hard score must be
perfect: breach coherence and mean alignment pass on EVERY weighted
period or the bucket fails (a failed bit caps that period's
contribution at half weight, which cannot reach 1.0). The risk cap is
the ONE soft component: its per-period bits blend by PERIOD_WEIGHTS
into a single verdict that fails only when the weighted sum drops
under RISK_WEIGHT_MIN. With the current weights the achievable sums
are {0, .05, .15, .20, .50, .55, .65, .70}: the 5d bar carries the
decision — 5d failing can never reach the bar, while 20d/60d failures
(alone or together) stay at or above it. Long-horizon adverse
extremes alone can no longer kill a strategy whose 5d risk profile is
sound.

Per-period risk-cap bit (RISK_CAP): proper reward/risk semantics —
the ADVERSE extreme of the period's forward endpoint changes must
stay under RISK_CAP of the FAVORABLE extreme (reward must dominate
risk by more than 1/RISK_CAP ≈ 1.33×):
    buy  → if min_change < 0 then |min_change| < 0.75·|max_change|
    sell → if max_change > 0 then |max_change| < 0.75·|min_change|
(min >= 0 for a buy / max <= 0 for a sell is trivially fine — no
adverse extreme exists; and a period entirely against the side —
max <= 0 for a buy, min >= 0 for a sell — can never pass, since
|adverse| >= |favorable| there). NOT APPLICABLE to 'next', whose
max/min are NULL (no close-based high/low at the 1-day horizon) —
non-applicable periods are excluded from the weighted sum, not
failed.

NaN convention: hard-check inputs compare False, so a bucket missing
a period's breach/mean inputs still fails (the repo's NaN
convention); a NaN risk period contributes 0 to the weighted sum —
it degrades the verdict like a failed period, and a bucket with no
risk inputs at all stays under the bar.

All checks are fully vectorized (native cudf ops, no row loops, no
if/else over rows).
"""
from __future__ import annotations

import pandas as pd

from analyze.analysis_signals.config import SELL_SIDES

# The per-period weights of the composite quality score (sum = 1) —
# they blend BOTH the hard score and the risk-cap verdict.
PERIOD_WEIGHTS: dict[str, float] = {
    "5d": 0.50,
    "next": 0.30,
    "20d": 0.15,
    "60d": 0.05,
}

# The periods whose close-based max/min forward change exists — the
# risk cap is NOT APPLICABLE to 'next' (NULL at the 1-day horizon);
# non-applicable periods are excluded from the weighted sum rather
# than failed.
MAXMIN_PERIODS: tuple[str, ...] = ("5d", "20d", "60d")

# The adverse extreme must stay under this fraction of the favorable
# extreme in EVERY risk period — the reward must dominate the risk by
# strictly more than 1/RISK_CAP.
RISK_CAP: float = 0.75

# The total weight the risk-cap verdict carries in the composite
# score (= the PERIOD_WEIGHTS mass of the risk periods).
RISK_WEIGHT_TOTAL: float = sum(
    PERIOD_WEIGHTS[p] for p in MAXMIN_PERIODS
)

# The passing bar of the weight-blended risk-cap verdict: the check
# fails only when Σ_p weight_p · risk_bit_p drops under this. With
# the current weights the achievable sums are {0, .05, .15, .20,
# .50, .55, .65, .70} — 5d must pass; 20d/60d failures alone (or
# together) stay at or above the bar.
RISK_WEIGHT_MIN: float = 0.50

# The passing bar of the composite score — 1.0 strict: the hard score
# (breach coherence + mean alignment) must be perfect AND the
# risk-cap verdict must pass.
MIN_SCORE: float = 1.0


class SignalQuality:
    """The final quality gate (see module docstring): score() blends
    the hard per-period check bits and the weighted risk-cap verdict;
    gate() keeps the MIN_SCORE-passing strategies of the plain gate's
    output."""

    def score(self, bucket: pd.DataFrame) -> pd.Series:
        """The composite quality score of bucket-level strategy rows —
        (1 - RISK_WEIGHT_TOTAL) · Σ_p weight_p · mean(hard bits of p)
        + RISK_WEIGHT_TOTAL · risk_bit, in [0, 1]. Bits cast to float
        BEFORE the arithmetic — cudf has no bool+bool ops (the
        native-dtype frame contract)."""
        total = pd.Series(0.0, index=bucket.index)
        for period, weight in PERIOD_WEIGHTS.items():
            hard = (
                self._breach_bit(bucket).astype("float64")
                + self._mean_bit(bucket, period).astype("float64")
            ) / 2.0
            total = total + (1.0 - RISK_WEIGHT_TOTAL) * weight * hard
        return total + RISK_WEIGHT_TOTAL * self._risk_bit(bucket).astype("float64")

    def gate(self, passing: pd.DataFrame) -> pd.DataFrame:
        """The FINAL gate: keep the strategy rows whose composite score
        reaches MIN_SCORE (the strategies that finally register)."""
        if passing.empty:
            return passing
        keep = self.score(passing) >= MIN_SCORE
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
        verdict."""
        sell = bucket["side"].isin(SELL_SIDES)
        max_chg = self._num(bucket["max_" + period])
        min_chg = self._num(bucket["min_" + period])
        mx, mn = max_chg.abs(), min_chg.abs()
        # buy: no drawdown (min >= 0) is trivially fine; a drawdown
        # must stay under RISK_CAP of the upside (max <= 0 forces
        # mx <= mn, so an all-down period never passes).
        buy_bit = (min_chg >= 0) | (mn < RISK_CAP * mx)
        # sell: no run-up (max <= 0) is trivially fine; a run-up must
        # stay under RISK_CAP of the downside (min >= 0 forces
        # mn <= mx, so an all-up period never passes).
        sell_bit = (max_chg <= 0) | (mx < RISK_CAP * mn)
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
    "RISK_CAP",
    "RISK_WEIGHT_TOTAL",
    "RISK_WEIGHT_MIN",
    "MIN_SCORE",
]
