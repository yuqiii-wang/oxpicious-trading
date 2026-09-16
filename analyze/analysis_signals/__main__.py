"""Entry point for analyze.analysis_signals.

Run via ``python -m analyze.analysis_signals``.

Per-DAY trading signals in the ``analysis_signals`` schema (see
database/sql/analysis/analysis_signals/): one row per
(code, sec_type, signal_type, signal_sub_type, date) with the crossed
threshold (signal_threshold), a human-readable reason, the full
detection params (JSON) and the action:

  - mov_rsi (sub_type rsi{W}): rsi_{W}days in the top 1% (action=sell)
    or bottom 1% (action=buy) of the trailing 5-year window ending at
    the snapshot month; W mirrors analysis.mov_ave_rsi.
  - mov_std (sub_type std{W}): price beyond the 2σ Bollinger band
    ma_{W} ± 2.0·std_{W}days (upper → sell, lower → buy); W in 20/60 —
    W=5 is not emitted (its ~1-day buckets carry no base-rate lift:
    pure detection noise).

  - high_low_streaks (sub_type p{band_period}_{pct_type}): the MEAN-MID
    anchor day of every MA-Spread band-break excursion streak — the
    analysis_forecasts.high_low_streaks buckets' trigger days 1:1 (the
    forecasts engine's own anchor machinery reused verbatim). side top
    an ABOVE-band excursion → sell, bottom BELOW-band → buy (the
    mean-reversion reading the forecasts study measured from the mid
    anchor). The anchor is EX-POST: target months carry a
    HL_STREAKS_RESOLVE_LAG_MONTHS resolve lag and still-open streaks'
    anchors are dropped, so a write-once month never carries a
    provisional mid.

  - mov_pairs (sub_type pair{W}) / mov_pairs_ema (sub_type
    emapair{W}): the CROSS days of the EXISTING relative spreads
    ma5_vs_ma{W} / ema6_vs_ema{W} (fetched as pair_{W} / ema_pair_{W})
    — side top a CROSS UP / golden cross → sell, bottom a CROSS DOWN /
    death cross → buy; W in 60/120/255, cooldown 5 (the forecast event
    buckets' own machinery via build_pairs_matrices).

  (The former opp_pair industry-pair family was removed — its buckets
  average ~600 trigger days yet ~0 pooled mean forward offset change;
  the OOS study showed zero mean confirmation content even for
  statistically-strengthened buckets. Forecasts keep computing the
  opp_pair_state buckets; see analysis_signals.config.)

Cooperation with analyze.analysis_forecasts (the gates are read, never
recomputed here):
  1. Target stat_months = the months ALREADY PRESENT in
     analysis_forecasts.mov_rsi (at pct = 1) / mov_std (at k = 2.0)
     for the sec_type — the forecasts' start month sets the first
     signal date.
  2. Incremental: a target month is computed only when
     analysis_signals.signals has no rows for it yet (month-level
     DISTINCT check per signal_type; months are written atomically in
     ONE transaction, so a crash can never leave a half-written
     month). ``--force`` deletes the sec_type's signal rows and
     recomputes every target month.
  3. Detection reuses the forecast machinery: the same trailing 5y
     window (M - 5y, M], linear-interpolated window percentile
     thresholds (RSI) / band levels (std), cooldown suppression and
     full-5y-history gate (first data strictly before the window
     start). Each date is emitted only within its own snapshot month.
  4. Forecast-confirmation gate (gate.fetch_confirm): a detected day
     is RECORDED only when the matching forecast bucket (same
     code/sec_type/stat_month/window/side/pct|k/cooldown config)
     qualifies on its MIXED forecast_results row — the weight-blended
     forward profile (5d 0.50 / next 0.30 / 20d 0.15 / 60d 0.05,
     materialized by analysis_forecasts) whose reverse_prob exceeds
     GATE_RP_MIN (reverse P > 1% — a material reversal probability)
     AND whose blended mean forward change is a REVERSAL (dir_ave > 0
     — the bucket's average outcome reverses, so the signal holds)
     AND whose reverse_prob beats the unconditional blended base rate
     (base_rates period='mixed', per side — probability lift; the
     conjunct falls back to TRUE when the code has no base_rates row)
     AND whose mean forward change beats the base drift (magnitude
     lift — the mean reversal must be bigger than the window's own;
     same fallback), read from analysis_forecasts
     via the bucket's forecast_id. occurrence / t-stat bars were
     removed 2026-09 (the streak-merge leaves pct-width buckets only
     ~3-6 merged signals — the bars dropped ~96% of the strongest-edge
     pct=1 buckets); occ / std_change still feed the confidence's
     evidence / efficiency factors. Reading the gate on the blended row
     is what makes EVERY forecast horizon of the same signal trigger
     contribute to the signal. The row's confidence is NOT the reverse
     probability (it saturates at long horizons) but the DRIVING-FACTOR
     COMPOSITE on that mixed row: a weighted blend of evidence
     (t-stat), efficiency (sharpe), consistency (probability lift over
     the base rate) and the code's prior mean composite — all computed
     in the signal's direction (buy = upward reversal, sell =
     downward), all horizon-free; the period ('mixed') + factor
     breakdown ride in the params JSON (conf_period /
     confidence_factors); see analysis_signals.config.
     Detection stays identical to the buckets; the gate only filters
     which days get written. NOTE: months written by an earlier
     (ungated) build keep their rows — ``--force`` rebuilds them
     under the gate.

``--live`` additionally runs the day-close mirror
(live_close.mirror_live_close): every signal row not yet recorded gets
one live.live_signals observation at the session close (time 15:00:00,
is_day_close_trigger = TRUE) — mov_std close vs band level, mov_rsi
day RSI vs threshold; PK-checked, so re-running backfills exactly the
missing rows. The signal pipeline itself stays incremental either way.

Pipeline per sec_type (index / etf / stock):
  1. Fetch active-universe codes + true first-data dates.
  2. Resolve target months (forecast presence − signal presence).
  3. Fetch the joined long input frame (price / ma / rsi / std; date >=
     earliest needed window start) and scatter to (date × code) wide
     matrices.
  4. Fetch the reverse-confirmed code sets from forecast_results.
  5. Run the vectorized signal engines (compute_rsi_signals /
     compute_std_signals), writing month-major, one transaction per
     month.
  6. Upsert analysis.analysis_identity; with --live, mirror day-close
     observations into live.live_signals.
"""