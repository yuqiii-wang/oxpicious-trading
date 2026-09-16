"""opp_pair_state bucket monthly aggregation (analysis_forecasts) —
industry opposite-pair trend forecasts.

The PAIR buckets of analysis_composites.industry_corr_benchmark_offsets
(see database/sql/analysis/analysis_forecasts/07_opp_pair_state.sql):
by industry pair, when ONE side's benchmark-offset MA trend is dropping
the forecast RESULT is the future trend of the OTHER side industry.

All trend legs live on the OFFSET space the composites analysis defines.
With MA_W = the trailing-W-row rolling mean of the industry composite
mean_close (pool 'all') and MA_M = the benchmark's (000300) MA_W, the
W-day offset trend change of industry X ending at t — k rebased at the
lookback start, exactly the composites' window math (the adjusted trend
adj = MA_X − k·MA_M is identically 0 at the rebasing point) — is

    (MA_X[t] − k·MA_M[t]) − (MA_X[t−W] − k·MA_M[t−W]),
    k = MA_X[t−W] / MA_M[t−W]

which, normalized by the industry's own MA level, reduces to the
RELATIVE MA RETURN

    rel_X(t) = MA_X[t]/MA_X[t−W] − MA_M[t]/MA_M[t−W].

TRIGGER ("industry A is dropping"): rel_A(t) < 0 — A's W-day MA-trend
return is below the benchmark's (an industry whose trend grows while
the benchmark grows MORE is DROPPING after the offset). FORWARD TARGET:
the other side industry B's normalized offset change over [t, t+n],

    fwd_B(t,n) = MA_B[t+n]/MA_B[t] − MA_M[t+n]/MA_M[t].

Per (stat_month, W) the trigger cells are aggregated with the shared
sparse horizon machinery against the TARGET industry's adaptive
reversal bar (k_n·σ of B's window forward offset changes). side =
'bottom' reverses on change > +thr, so forecast_results.reverse_prob =
P(B rises beyond the bar) — the pair forecast's CONFIRMATION
probability, not a reversal. One bucket per directional pair; no hype
split, no cooldown (state buckets — industries have no hype source).

Yields (stat_month, rows) so __main__ can split each row into the
opp_pair_state motivation dicts and the forecast_results result dicts
and write month-major.
"""