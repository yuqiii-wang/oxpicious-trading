-- hp_rsi — the family knowledge document (see 00_schema.sql).
-- NEW 2026-10-18 (the two-layer merge): the hedge_pairs family's
-- RSI-SOURCED ORCHESTRATION layer. The SOURCE is the components'
-- existing Short-Term-Tech rsi signal rules (REAL mov_rsi rows,
-- signal_type 0, at the market + leg-industry codes — the per-code
-- typical tier); the hedge pairs layer orchestrates them into the
-- portfolio composites stamped under THIS type at the pair codes
-- with the hp_* sub types, and every row's portfolio_signal_rules
-- group REFERENCES the sourced mov_rsi rule ids it coordinates.
INSERT INTO static.analysis_signal_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "name": "Hedge Pairs RSI",
  "description": "The hedge_pairs family's RSI-sourced ORCHESTRATION layer (2026-10-18 two-layer merge), stamped signal_type hp_rsi at the composite pair codes. The SOURCE is the components' existing Short-Term-Tech rsi signal rules — the broad market (000300) + every leg industry each carry their own REAL mov_rsi rule rows (signal_type 0, rsi14_{1,2,3}pct, both sides, the per-code typical grammar, measured on the component's own response); the hedge pairs layer then ORCHESTRATES those sourced signals into the portfolio rules: the three-component capitulation composite entry, the market-high exhaustion exit, and the laggard's catch-up. No detection is duplicated: the orchestrated rows coordinate the sourced ids (each row's portfolio_signal_rules group holds the sourced mov_rsi rule ids of its winning tail), while the composite carries the trade validity and the pair-code history. The pair-native exits (ratio-z harvest, regime stop, observational flip) stay under hedge_pairs (7).",
  "condition": "per pair (code 'A~B', pool-'all' composites), THREE components — the broad market (000300) + the two legs — each with its own Wilder RSI(14) via the mov_ave kernel and its OWN adaptive percentile bars over its trailing 10y (one-row-shifted — no look-ahead); the nested {1,2,3}% tail grid is the candidate set, the per-cell winner self-selects; the pairing witness = a component's signal alive within the last 5 of its own traded days; the pair ratio z = (ln(A/B) − MA60)/σ60 picks the laggard side (sign, same day); the inverse-vol portfolio w_A = σ_B/(σ_A+σ_B), weights lagged one row. Composite cells POOL on both axes (trend 4 + regime 4); K=5 episode absorb; the OHLCV boost is off.",
  "sources": [
    "analysis_signals.signal_rules (signal_type 0) — the SOURCE: the components' REAL mov_rsi rsi14_* rule rows at the market + leg codes",
    "stats.industry_basic_stats.mean_close (pool 'all') — both legs' composite closes (the RSI/bars run on each leg's OWN full calendar)",
    "stats.index_basic_stats.close (000300) — the market component (peer vote + exit basis) and the offset-corr basis"
  ],
  "principles": {
    "sourced_then_orchestrated": "The two-layer contract: the per-code rsi signals are detected ONCE by the existing signal rules (the source layer — no re-implementation under a second vocabulary); the hedge pairs layer only ORCHESTRATES them — the laggard-side selection, the ~5d pairing window, the portfolio/leg response measurement — and stamps the result under hp_rsi with the hp_* sub types. Each orchestrated row's group references the sourced rule ids it coordinates, so the UI shows exactly which existing signals the portfolio combines.",
    "paired_legs_not_the_market": "The market rides as a PEER vote (one of three ids in the entry group), never a gate — the legs carry the hedging, the market adds the beta context; the pairing is ROUGHLY NEARBY (within ~5 of the components' own traded days), never exact-date.",
    "staged_exits": "The market-high exit coordinates the market's own exhaustion (the highs counterpart of the entry's market-lows vote); the catch-up sells the laggard at its OWN exhaustion while the ratio still favors the other leg — 'the other one, on its rise, got sold as well'. The composites gate on the response they trim (the portfolio / the laggard leg), the sourced component cells on their own price.",
    "pooled_cells": "Entries and extremes are rare events: per-(trend, regime) corners would starve exactly where the significant days land — both axes pool; the per-pair cell is the measured unit."
  },
  "sub_signals": {
    "hp_entry_legs_rsi": { "meaning": "BUY the pair when all three capitulation witnesses — the market's and both legs' RSI low-tail breaches — are alive within ~5 traded days (the trio composite; the group references the winning tail's three sourced mov_rsi BUY ids)", "trigger": "wit_lo{tail}_m & wit_lo{tail}_a & wit_lo{tail}_b (the three witnesses overlap; the tail grid is the candidate set, K=5)", "action": { "buy": "the three-component composite entry — response: the pair portfolio" }, "params": { "tail_pct": [1, 2, 3], "rsi_window": 14, "bar_window_days": 2550, "witness_window": 5, "components": ["broad_market", "leg_a", "leg_b"] } },
    "hp_exit_mkt_high": { "meaning": "SELL the portfolio when the broad market's own RSI breaches its adaptive high tail — the coordinated exhaustion exit (the group references the market's sourced SELL id).", "trigger": "wit_hi{tail}_m (the market's high-tail witness alive; the tail grid is the candidate set)", "action": { "sell": "the market-exhaustion composite exit — response: the pair portfolio" }, "params": { "tail_pct": [1, 2, 3], "rsi_window": 14, "bar_window_days": 2550, "witness_window": 5 } },
    "hp_catchup_rsi_hi": { "meaning": "SELL the laggard at its own RSI high-tail exhaustion while the ratio still favors the other leg (the group references both legs' sourced SELL ids).", "trigger": "hi{tail}_lag (the laggard's RSI high-tail breach; the tail grid is the candidate set)", "action": { "sell": "harvest exit, leg 2 — response: the laggard leg's forward blend <= 0" }, "params": { "tail_pct": [1, 2, 3], "rsi_window": 14, "bar_window_days": 2550 } },
    "hp_entry_wash120_mkt": { "meaning": "BUY the pair at the 120-day co-drawdown with the market's RSI-3% capitulation as the third witness — the RSI trio's structural cousin (the group references the trio's sourced rsi14_3pct BUY ids).", "trigger": "wit_rffloor120_a & wit_rffloor120_b & wit_lo3_m", "action": { "buy": "the rf+market composite entry — response: the pair portfolio" }, "params": { "window": 120, "mkt_tail_pct": 3, "witness_window": 5 } }
  }
}$json$::jsonb,
       '2026-10-19'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'hp_rsi'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
