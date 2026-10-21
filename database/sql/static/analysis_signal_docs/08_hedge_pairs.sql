-- hedge_pairs — the family knowledge document (see 00_schema.sql).
-- REWRITTEN 2026-10-18 (the two-layer merge): the RSI-sourced
-- orchestrated rules (the three-component composite entry, the
-- market-high exit, the catch-up) moved to the DEDICATED hp_rsi type
-- (see 09_hp_rsi.sql) — their SOURCE is the components' existing
-- Short-Term-Tech rsi signal rules (REAL mov_rsi rows, signal_type 0
-- at the component codes). THIS type keeps the PAIR-NATIVE rules with
-- no existing-family counterpart: the volatility-harvest ratio trims,
-- the hedge-regime break stop, and the observational hedgeable-regime
-- flip. The universe and the pair mechanics are the family's shared
-- frame (one prepared frame, two entry classes under the one
-- registry label).
INSERT INTO static.analysis_signal_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json$
{
  "name": "Hedge Pairs",
  "description": "The long–long anti-correlated pair family (2026-10-18), built on the online hedge-portfolio study's three curves: two offset-opposite industry composites and the broad-market benchmark. With negative offset correlation, equal-risk (inverse-vol) LONG positions in BOTH legs keep both expected returns while the legs' idiosyncratic fluctuations partially cancel (the study's core result). TWO-LAYER MERGE: the RSI-sourced rules (the components' existing Short-Term-Tech rsi signal rules orchestrated into the composite entry / market-high exit / catch-up) carry the DEDICATED hp_rsi type (see that family's doc); THIS type keeps the PAIR-NATIVE rules with no existing-family counterpart — the volatility-harvest ratio trim (ONE sub; the z bar the run_cells candidate set), the hedge-regime break stop, and (2026-10-20) the momentum-tilt rotation pair, the harvest's OPPOSITE: at the ratio divergence the measured drift is continuation, so the tilt goes TOWARD the strong leg (hp_mom_strong buys it, hp_mom_lag sells the laggard) — under the hedge_pairs sub types. The 2026-10-21 dedup (same-semantics subs collapse to the one most semantic) merged the staged hp_harvest_z15/z20 pair into hp_harvest, merged the per-window hp_entry_wash60/120/255 trio into ONE hp_entry_wash (the W window the run_cells candidate set), and retired the observational hp_regime_flip — hp_entry_rho50 is the hedgeable turn-on's one record. The universe is data-driven: the top offset-opposite industry pairs by the trailing 255d opposite score of analysis_composites.industry_corr_benchmark_offsets (greedy disjoint — each industry in at most one pair) plus the DIV_GENERAL~TECH_GENERAL showcase pair; codes are composite 'A~B' pair ids (lexicographic).",
  "condition": "per pair (code 'A~B', pool-'all' composites) over the shared frame: the pair ratio z = (ln(A/B) − MA60)/σ60 (the relative-value axis the harvest exits trade); the offset correlation rho_off = corr(a−m, b−m) over 60d (rolling-mean identities, daily grain); the inverse-vol portfolio w_A = σ_B/(σ_A+σ_B), weights lagged one row (the primary response price); px_strong = the relatively-high leg's synthetic price. Cells POOL on both axes (trend 4 + regime 4 — the sparse-event options precedent); K=5 episode absorb; the OHLCV boost is off (pair composites carry no day-0 turnover anatomy). Composite pair codes are kept OUT of analysis.trend_days.",
  "sources": [
    "stats.industry_basic_stats.mean_close (pool 'all') — both legs' composite closes",
    "stats.index_basic_stats.close (000300) — the offset-correlation market leg (the rho_off basis — never a directional gate)",
    "analysis_composites.industry_corr_benchmark_offsets.opposite_score_ma255_255d — the pair universe",
    "analysis.pair_pct_change_ratios — the stride-grid hedge-ratio audit this family's daily rho_off mirrors"
  ],
  "principles": {
    "pooled_cells": "Entries and extremes are rare events: per-(trend, regime) corners would starve exactly where the significant days land (the rf_break lesson) — both axes pool; the per-pair cell is the measured unit.",
    "regime_dependence": "Negative correlation is regime-dependent, not structural (the 2022 stocks-bonds lesson): rho_off ≥ 0 within 60d of a hedgeable ≤ −0.30 reading is the regime-break stop; the hedgeable turn-on's record is hp_entry_rho50 (the deep gated trading anchor — the observational shallow twin hp_regime_flip was retired by the 2026-10-21 dedup).",
    "portfolio_tier_audit": "The PORTFOLIO tier (2026-10-20's cell-level redesign: ONE ROW PER PORTFOLIO): the construction rides portfolio_signal_rules.rule_params in the signal_rules grammar (axis / universe / pair_weighting / episode_merge_days + the gate bars + the confidence cascade scale + the portfolio {legs, market, aim} block + the PER-SUB coordination specs under subs), and the dated PORTFOLIO AUDIT rides history_portfolio_signal_performance — kind max_drawdown_reduction (the pair value's own max drawdown vs the legs' standalone, reduction in percentage points) + the members' COMBINED STATS (pooled: the individual in-portfolio signals' measures meta-combined) + the orchestrating cells' combined dd lens + the gate VERDICT digest. THE GATED CONFIDENCE CASCADE: a portfolio's member cells (the sourced component cells + the pair-native self-members, flagged signal_rules.is_in_portfolio) take a confidence INCREASE only when the src data TOGETHER passed the portfolio gate (the audited drawdown reduction vs the MEAN leg >= 0pp) AND an orchestrating composite's own entry-rule verdict — the strongest passing claiming portfolio's gate.conf_scale, applied by the live tier beside the starvation scale; a failed gate grants nothing (the portfolio tier never discounts a member's confidence). The type's knowledge document: static.portfolio_signal_docs (the analysis_signal_docs convention at the portfolio tier).",
    "staged_harvest_exits": "The volatility-harvesting / rebalancing premium (Bouchey et al. 2012): selling the relatively-high leg at |ratio z| extremes is a signal-driven discretization of Shannon's demon — the premium grows with volatility and negative correlation, and requires zig-zag (a one-way trend erodes it; the regime stop bounds that case). Sell correctness is measured on the leg being trimmed (the strong leg's forward blend ≤ 0), not on the portfolio.",
    "raw_vs_offset_correlation": "A pair anti-correlated only in OFFSET (excess over the market) terms does NOT cancel total price moves — both legs still fall with the market. The family treats the long–long as market beta + the market-neutral oscillating residual that generates the harvest exits; the rho_off regime rules guard the residual's sign.",
    "long_long_negative_correlation": "With rho < 0 the minimum-variance solution is long–long; inverse-vol (equal-risk) weights are the robust choice when the rho estimate is noisy. The RSI-sourced entry/exit rules live under hp_rsi — this type's rules trade the pair's own oscillation and regime."
  },
  "sub_signals": {
    "hp_entry_rho50": {
      "action": {
        "buy": "deep hedgeable entry — response: the pair portfolio"
      },
      "params": {
        "rho_deep": -0.5,
        "corr_window": 60
      },
      "meaning": "BUY the pair when the offset correlation first reaches −0.50 — the deep hedgeable turn-on, the hedge cancelling at its hardest (the 2026-10-21 text diet: one sentence per sub in the UI; the study evidence lives in docs/hedge_pairs_more_triggers_study_2026-10-19.md)",
      "trigger": "rho_off <= -0.50 (episode first day)"
    },
    "hp_harvest": {
      "action": {
        "sell": "trim the strong leg — response: the strong leg's forward blend <= 0"
      },
      "params": {
        "ratio_z": [1.5, 2.0],
        "ratio_window": 60
      },
      "meaning": "SELL the relatively-high leg when the pair ratio's z-score pierces an extreme (|z| ≥ k, k the per-cell candidate winner of {1.5, 2.0}) — the volatility-harvest trim.",
      "trigger": "|ratio_z| >= k (episode first day; k the run_cells candidate winner of {1.5, 2.0})"
    },
    "hp_stop_regime": {
      "action": {
        "sell": "exit timing — response: the pair portfolio"
      },
      "params": {
        "corr_window": 60,
        "hedgeable_rho": -0.3,
        "rho_off_break": 0.0,
        "witness_window": 60
      },
      "meaning": "SELL the pair once the hedge regime broke — the offset correlation back at 0 within 60 days of a hedgeable ≤ −0.30 reading (the anti-correlation justifying the long–long is gone).",
      "trigger": "rho_off >= 0 & hed_witness > 0"
    },
    "hp_entry_wash": {
      "action": {
        "buy": "rf washout entry — response: the pair portfolio"
      },
      "params": {
        "window": [60, 120, 255],
        "witness_window": 5
      },
      "meaning": "BUY the pair at the co-drawdown: both legs' W-day Donchian-floor breakdowns alive within 5 pair-days — the structural cheapness event (the 2026-10-21 dedup: the per-window trio merged, W the run_cells candidate winner of {60, 120, 255}; the recorded value the deepest alive witness's washdepth).",
      "trigger": "wit_rffloorW_a & wit_rffloorW_b (each window its OWN population — the rf_break no-carve precedent)"
    },
    "hp_entry_catchup_reclaim60": {
      "action": {
        "buy": "catch-up entry — response: the pair portfolio (the px_lag switching path measured negative: it abandons the reclaimed leg at the ratio's zero-cross; the portfolio response reproduces the held-leg +0.55% t=5.68)"
      },
      "params": {
        "ma_window": 60,
        "ratio_z_window": 60
      },
      "meaning": "BUY the pair when the weak leg closes back above its own MA60 — the laggard's turn (the reclaim mirror of the catch-up sell).",
      "trigger": "reclaim60_a & ratio_z < 0 | reclaim60_b & ratio_z > 0 (the first close back above the MA after a below-episode)"
    },
    "hp_mom_strong": {
      "action": {
        "buy": "momentum-tilt buy — response: the strong leg's own forward blend (the buy bar + the pooled tier)"
      },
      "params": {
        "ratio_window": 60,
        "ratio_z": [1.5, 2.0, 2.5]
      },
      "meaning": "BUY the strong leg at the |ratio z| divergence — divergence measured CONTINUATION, not reversion, so the tilt goes toward the strong side (the harvest's opposite; docs/hedge_pairs_spread_rotation_study_2026-10-20.md).",
      "trigger": "|ratio_z| >= k (episode first day; k the run_cells candidate winner of {1.5, 2.0, 2.5}; the leg = sign(ratio_z) same day)"
    },
    "hp_mom_lag": {
      "action": {
        "sell": "momentum-tilt sell — response: the laggard leg's forward blend (the exit-timing bar; sells are never pooled-gated)"
      },
      "params": {
        "ratio_window": 60,
        "ratio_z": [1.5, 2.0, 2.5]
      },
      "meaning": "SELL the laggard at the same divergence — the tilt's other half, completing the rotation toward the strong side.",
      "trigger": "|ratio_z| >= k (episode first day; same trigger as hp_mom_strong, the opposite leg)"
    }
  }
}
$json$::jsonb,
       '2026-10-21'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'hedge_pairs'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
