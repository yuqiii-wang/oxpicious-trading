"""strategy.forecast_next_day — the ±kσ next-day scenario forecast.

The nightly companion of the singleton strategy (strategy_daily step 2,
after ``strategy.singleton_trading``): per (anchor bar date, code) the
trailing-20d population σ of daily returns projects SIX next-day closes
(±1σ/±2σ/±3σ) and each scenario registers WHICH singleton-strategy
signal would trigger under that close — the active
``analysis_signals.signal_rules`` cells of the CLOSE-DERIVABLE families
evaluated against the synthetic close (the live tier's breach contract,
projected onto a scenario). Results land in ``strategy.forecast_next_day``
(the Strategy → Forecast page's table).

Module layout (class-first; pydantic payloads at the boundaries):

  scenarios.py     the σ ladder — the six ScenarioSpec projections
  values.py        ScenarioValues — the synthetic-day value recompute
                   (Wilder RSI / boll z / momentum anchors / MA gaps /
                   hv20 / ewma / macd cross) from the REAL histories
  evaluation.py    ForecastCellEvaluator — the active cells vs the
                   scenario values: bar resolution, day-context gate,
                   trigger, projected confidence, pickup reading
  fetch.py         ForecastFetch — the sql_views reads (one class, one
                   method per read)
  strategy_link.py StrategyFreshness — the auto-link: when the nightly
                   portfolio run is behind the bar horizon, spawn the
                   strategy CLI first (the trainer's post-training run
                   precedent)
  writer.py        ForecastWriter — the upsert / purge boundary with
                   the ForecastRow contract

Scope (the repo's mandatory study scope): index sec_type, the 10 named
study indices only. The SCENARIO-EVALUABLE families are the six whose
trigger values derive from closes (mov_rsi, bollinger, momentum FIXED
anchors, volatility, ma_cross GAP variants, macd); rf_break (3-day
persistence), rebound (episode machinery), crowdedness / options_wall /
options_vix (non-close upstream data) are honestly skipped and logged —
never invented.

Cudf note: this module is NOT a frame pipeline — per code it walks a
few thousand scalar closes (the sanctioned per-group host boundary);
cudf.pandas is still activated by the entry point per house convention
(harmless here, mandatory shape for every pipeline ``__main__``).
"""
from __future__ import annotations

# the forecast's own register
FORECAST_TABLE = "strategy.forecast_next_day"

# the mandatory study scope (oxpicious-dev-conventions): the 10 named
# indices, index sec_type only
STUDY_CODES: tuple[str, ...] = (
    "000001", "931743", "931160", "931071", "000015", "930708",
    "931994", "931594", "000683", "000991",
)

# the σ ladder: ±kσ for k in SIGMA_MULTS, both directions
SIGMA_MULTS: tuple[int, ...] = (1, 2, 3)
DIRECTIONS: tuple[str, ...] = ("down", "up")

# the trailing window of daily close-to-close returns the σ ladder
# measures (population std, ddof=0 — the bollinger σ convention)
RETURNS_WINDOW = 20

# the scenario-evaluable families (close-derivable trigger values) —
# the active_cells read filters to these signal_type codes; per-cell
# evaluability is still decided by the bar_def kind in evaluation.py
SCENARIO_FAMILIES: tuple[str, ...] = (
    "bollinger", "ma_cross", "macd", "momentum", "mov_rsi", "volatility",
)

# the nightly mixed portfolio's families that are NOT scenario-
# evaluable (logged per run, never invented): rf_break needs 3-day
# persistence beyond the band, rebound is episode machinery, and
# crowdedness / options_wall / options_vix read upstream builds whose
# synthetic-day values cannot be derived from a close
SKIPPED_FAMILIES: tuple[str, ...] = (
    "crowdedness", "options_vix", "options_wall", "rebound", "rf_break",
)

# the per-code trailing close rows fetched (covers the 2520d bar windows
# + the 252d MA gap windows + the Wilder/EMA recursion warmup)
CLOSE_ROW_LIMIT = 2600

# the single source of the nightly portfolio's CLI args — the
# orchestration tier file is canonical (the self-spawn reuses step 1's
# arguments verbatim; no duplicated family list)
NIGHTLY_TIER_JSON = "scripts/orchestration/strategy_daily.json"

__all__ = [
    "FORECAST_TABLE",
    "STUDY_CODES",
    "SIGMA_MULTS",
    "DIRECTIONS",
    "RETURNS_WINDOW",
    "SCENARIO_FAMILIES",
    "SKIPPED_FAMILIES",
    "CLOSE_ROW_LIMIT",
    "NIGHTLY_TIER_JSON",
]
