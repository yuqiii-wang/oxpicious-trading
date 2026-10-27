-- hedge_pairs — the family knowledge document (see 00_schema.sql).
INSERT INTO static.signal_rules_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "sub_signals": {
    "hp_entry_trio": {
      "meaning": "All three curves (both legs + the market) in their own RSI capitulation tails the same day — the co-wash; BUY the inverse-vol pair",
      "trigger": "all three curves (both legs + the market) close with RSI(10 or 14) below their own bottom-tail bars the same day (the self-picked window x tail grid {10, 14} x {1, 2, 3, 5, 10, 15, 20}% — at-least populations; the frequency floor's window-major pick, RSI(10) preferred, under the loose gate)"
    },
    "hp_entry_spread": {
      "meaning": "The pair's long-short SPREAD curve (w_a*r_a - w_b*r_b compounded, leg A long / leg B short, no market curve) in its own RSI bottom tail — the classic pairs CONVERGENCE trade; the response axis is the spread value's forward blend (the long-A/short-B PnL)",
      "trigger": "the spread's Wilder RSI(10 or 14) below its own bottom-tail bar (the self-picked window x tail grid {10, 14} x {1, 2, 3, 5, 10, 15, 20}% — at-least populations; the same frequency-floor + loose-gate winner policy as the trio)"
    },
    "hp_exit_trio": {
      "meaning": "The blow-off mirror — all three curves at their RSI exhaustion tails; the MEASURED asymmetry check, never shipped (thin exit timing; tops continue)",
      "trigger": "rsi_a > bar_a(w, p) AND rsi_b > bar_b(w, p) AND rsi_mkt > bar_mkt(w, p), at-least populations over w in {10, 14} x p in {1,2,3,5,10,15,20}"
    },
    "hp_exit_spread": {
      "meaning": "The spread's TOP-TAIL EXIT — the same long-short spread curve at its RSI EXHAUSTION tail (the rotation blow-off): SELL the leader leg A / BUY the laggard leg B, the one-sell-one-buy mirror of the entry (shipped 2026-10-25 evening on the 2024-04-18 diagnosis — the showcase pair's dividend-over-tech blow-off the bottom-only era could never fire)",
      "trigger": "the spread's Wilder RSI(10 or 14) above its own top-tail bar, at-least populations over w in {10, 14} x p in {1,2,3,5,10,15,20} under the per-year coverage floor + loose gate (measured: 44 winners / 850 events, pooled +0.20% t=3.41 6/6 years; the raw-grid negative stands as the honest background — relative strength persists, the edge lives in the per-pair winners)"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'hedge_pairs'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
