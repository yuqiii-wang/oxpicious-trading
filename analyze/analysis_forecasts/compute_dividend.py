"""dividend_state bucket annual-snapshot aggregation (analysis_forecasts) —
extreme-percentile engine.

The valuation extreme-PERCENTILE buckets over the dividend-yield
series of analysis.dividends (see
database/sql/analysis/analysis_forecasts/12_dividend_state.sql): per
stat month's trailing 5-year window [lo, hi) of the (T, C) wide grid,
a (code, date) joins a bucket when its trailing-12m D/P (fractional)
sits in the top pct% (bucket extreme 'top' — the
linearly-interpolated quantile of the window's non-NULL yield values
at q = 1 - pct/100) or bottom pct% (extreme 'bottom', q = pct/100) of
the window, per the code's OWN distribution (the fetch layer's raw
`dividend_yield` column; NaN here means "no bucket" — non-payer
days). Only EXTREME days form buckets — the mov_rsi pct convention
(the 2026-09 refactor of the former z-STATE buckets; the mid/flat
central bulk forms no bucket).

The family's defining semantics: the yield is HIGHER-the-better — a
high-yield day is a cheap, well-supported valuation → the top-pct%
extremes are bullish (side 'bottom'), the bottom-pct% (low-yield)
extremes bearish (side 'top'). The mapping REVERSES the pe sibling's
(compute_pe) — the engine flips the bucket extreme → family side map.

Everything else — the (code chunk × stat month) partition, the
streak-merge (consecutive qualifying days → ONE signal with
incremental anchor triggers at delays 0..TRIGGER_DELAY_MAX;
the bucket's mean run length recorded on
forecast_identities.streak_signal_days), the market-hype split, the
forward-change aggregation, the blended mixed row and the row
emission — is inherited from ``_dfengine.WideDfEngine``. Yields
(stat_date, rows) snapshot-major. Each anchor's trigger excess (the
anchor day's yield minus the bucket's quantile bar, value − bar) rides
forecast_results.trigger_excess — the family now has a scalar
qualifying bar (the quantile), unlike the former band membership.
"""


from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from analyze.analysis_forecasts.compute_pe import ValPctEngine


class _DividendValPctEngine(ValPctEngine):
    src_col = "dividend_yield"
    # The yield HIGHER-the-better — the REVERSE of the pe mapping: the
    # top-pct% (cheap / well-supported) extremes are the bullish
    # 'bottom' side, the bottom-pct% (low-yield) extremes 'top'.
    side_of_bucket = {"top": "bottom", "bottom": "top"}


def compute_dividend_results(
    *, df, first_dates, regimes, codes, sec_type, specs,
) -> Iterator[tuple[date, list[dict]]]:
    """Yield (stat_date, dividend_state bucket rows) per month."""
    engine = _DividendValPctEngine(
        df=df, first_dates=first_dates, regimes=regimes, codes=codes,
        sec_type=sec_type, specs=specs,
    )
    return engine.run()
