-- neg_corr_hedge_pair — the portfolio type's knowledge document (see
-- 00_schema.sql). NEW 2026-10-20 (the portfolio tier's docs layer,
-- convention-aligned to the signal docs): the CONSTRUCTION knowledge
-- shared by every 'A~B' group — what the portfolio IS, how it is
-- built, and WHAT BENEFIT it is run for (the audited claim the
-- history_portfolio_signal_performance snapshots record; one row
-- per PORTFOLIO since the 2026-10-20 cell-level redesign). The
-- per-sub coordination specs live in
-- portfolio_signal_rules.rule_params.subs; the
-- families' own rule knowledge in static.analysis_signal_docs
-- (08_hedge_pairs / 09_hp_rsi).
INSERT INTO static.portfolio_signal_docs (portfolio_type, document, updated_at)
SELECT ld.code,
       $json${
  "name": "Negative-Corr Hedge Pair",
  "description": "The two-leg long–long negatively-correlated hedge pair (the 2026-10-18 portfolio tier's first type): a composite 'A~B' code pairing two offset-opposite industries (trailing 255d opposite score > 0.5, greedy disjoint — each industry in at most one pair) held LONG BOTH legs at inverse-vol (equal-risk) weights, weights lagged one row. The broad market (000300) rides as the offset-correlation leg (rho_off basis) and a peer vote in the orchestrated entries — never a directional gate. The portfolio's rule groups coordinate the legs' own signal rules (the hp_rsi orchestration referencing the sourced mov_rsi ids) plus the pair-native hedge_pairs rules; each group's constant construction rides its portfolio_signal_rules.rule_params row (the signal_rules grammar: axis / response / universe / pair_weighting / episode_merge_days + the portfolio {legs, market, aim} and coordination blocks).",
  "condition": "per pair (code 'A~B', pool-'all' industry composites): the inverse-vol portfolio value w_A = sigma_B/(sigma_A+sigma_B) over the 60d window, weights lagged one row (the primary response axis); the offset correlation rho_off = corr(a-m, b-m) over 60d (rolling-mean identities, daily grain); the pair ratio z = (ln(A/B) - MA60)/sigma_60 (the laggard-side selector). The benefit audit runs on the prepared pair frame: the portfolio value path's and each leg's standalone close path's own max drawdowns (running-max troughs, full history), plus each group's coordinated-event forward-trough lens (ddmin20, the history_signal_performance.dd convention).",
  "sources": [
    "analysis_signals.portfolio_signal_rules — the portfolio rows (the whole member signal_rule_ids array + the constant rule_params construction, the per-sub coordination specs under subs)",
    "analysis_signals.history_portfolio_signal_performance — the dated PORTFOLIO AUDIT snapshots this document's `benefit` block documents (the members' combined stats + the structure benefit)",
    "analysis_composites.industry_corr_benchmark_offsets.opposite_score_ma255_255d — the pair universe",
    "stats.industry_basic_stats.mean_close (pool 'all') + stats.index_basic_stats.close (000300) — the legs' and the market's closes"
  ],
  "principles": {
    "long_long_negative_correlation": "With rho < 0 the minimum-variance solution is long-long; inverse-vol (equal-risk) weights are the robust choice when the rho estimate is noisy — both legs keep their expected returns while the idiosyncratic fluctuations partially cancel.",
    "raw_vs_offset_correlation": "A pair anti-correlated only in OFFSET (excess over the market) terms does NOT cancel total price moves — both legs still fall with the market. The audited drawdown benefit is the STRUCTURE's claim (the inverse-vol blend of the two legs), measured on the realized paths, never an assumption.",
    "benefit_not_profit": "The portfolio tier's verdict axis is the BENEFIT it takes (max drawdown reduction), NOT directional profit: the member cells carry the dir_ave gates (the signal tier's job); this tier audits WHY the cells are coordinated as a portfolio instead of traded as standalone codes.",
    "gated_confidence_cascade": "The portfolio's confidence ADDS to the member cells' as an INCREASE, gated: the src data TOGETHER must pass the PORTFOLIO GATE (the audited drawdown reduction vs the MEAN leg >= 0pp — the worst-leg comparison is nearly automatic for any blend) AND the ENTRY RULES (the orchestrating composite cell's own dir_ave verdict); only then do the members see the cell-level increase (scale = 1 + 0.25 x min(benefit_pp / 5, 2), capped x1.5 — the cell depth-scale formula on the benefit depth, the strongest passing membership under max-scale aggregation). A failed gate stamps NOTHING on the members: the portfolio tier only ever increases a member's confidence, never discounts it.",
    "dated_never_purged_snapshots": "The audit is MEASURED and dated (one snapshot per run, upserted on the group PK + the frame's data horizon, never purged): the benefit's evolution — the day the hedge stopped paying, the day the reduction widened — is the point (the history_signal_performance lifecycle)."
  },
  "benefit": {
    "kind": "max_drawdown_reduction",
    "meaning": "WHAT THE PORTFOLIO TOOK: the inverse-vol pair value's own max drawdown sitting SHALLOWER than its legs' standalone — the negative-covariance cancellation realized on the drawdown path.",
    "max_dd": {
      "window": "full_history — the prepared pair frame's whole history",
      "portfolio_pct": "the pair portfolio value path's own max drawdown (pct, <= 0)",
      "leg_a_pct / leg_b_pct / worst_leg_pct / mean_leg_pct": "the same gauge on each leg's standalone composite close — what an undiversified holder of that leg ate",
      "reduction_vs_worst_pct / reduction_vs_mean_pct": "the BENEFIT TAKEN, percentage points shallower than the worst / mean leg's drawdown (drawdowns are negative pcts, so the depth reduction = portfolio - leg in signed space; >= 0 = the coordination paid)"
    },
    "gate": "the PORTFOLIO GATE VERDICT digest on every dated snapshot: {metric max_dd_reduction_vs_mean, min_pp 0, passed, conf_scale} — conf_scale is the member CONFIDENCE CASCADE the passing group grants (1.0 when failed); portfolio_signal_rules.is_valid denormalizes passed AND at least one orchestrating cell's own verdict (the gating conjunction)",
    "cascade": "the member cells' increase, riding the portfolio snapshots' gate.conf_scale: the live tier applies the strongest passing claiming portfolio's scale to its is_in_portfolio member cells (portfolio_signal_rules.signal_rule_ids), beside the starvation sample_scale; no passing portfolio = no increase (never a discount). The per-cell portfolio_cascade stamp was RETIRED with its column (2026-10-20)",
    "dd": "the portfolio's coordinated-event drawdown lens {min20_pct, p10_pct, baseline_pct, n} — the orchestrating cells' own lenses combined at the portfolio level: the replayed trigger days' forward worst-trough within 20d vs the response path's all-days baseline"
  }
}$json$::jsonb,
       '2026-10-20'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'portfolio_type' AND ld.label = 'neg_corr_hedge_pair'
ON CONFLICT (portfolio_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
