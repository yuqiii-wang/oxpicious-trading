-- neg_corr_hedge_pair — the portfolio-rule knowledge document (see
-- 00_schema.sql). 2026-10-27 seed, re-authored after the
-- static.portfolio_signal_docs drop: the two PORTFOLIO RULES of the
-- offset-hedge pair tier, texts grounded in the engine-written
-- rule_params.subs.<sub>.condition lines (the per-pair winner tails
-- the conditions record are examples — each pair self-picks its own).
INSERT INTO static.portfolio_rules_docs (portfolio_type, document, updated_at)
SELECT ld.code,
       $json${
  "rules": {
    "trio_wash": {
      "meaning": "The trio wash — both offset-opposite legs and the broad market (000300) capitulate in their own RSI tails on the same day; the coordinated three-curve wash is the long entry claim, hedged by construction (a market-wide wash drags every leg together).",
      "trigger": "leg A + leg B + the broad market each below their own adaptive RSI(10/14) capitulation tail on the same day — each pair self-picks the deepest per-curve tail meeting the once-per-year frequency floor (the winner rides rule_params.bar_def, e.g. the showcase pair: tails 10%)"
    },
    "spread": {
      "meaning": "The spread rotation — the pair traded as a spread (long leg A / short leg B, inverse-vol weights); the spread's own washes are entered on its RSI bottom tail and exited on its top tail, the legs traded leader-vs-laggard.",
      "trigger": "entry: the pair spread in its own RSI(10) bottom tail — BUY the laggard leg, SELL the leader leg; exit: the spread in its own RSI(14) top tail — SELL the leader, BUY the laggard back (per-pair winner tails ride rule_params.bar_def, e.g. tails 5% / 15%)"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'portfolio_type' AND ld.label = 'neg_corr_hedge_pair'
ON CONFLICT (portfolio_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
