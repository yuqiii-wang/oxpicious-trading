-- ma_cross — the family knowledge document (see 00_schema.sql).
INSERT INTO static.analysis_signal_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "name": "ma_cross",
  "description": "The price-vs-MA event family. NOT a golden cross / death cross (published MA-over-MA backtests run mixed-to-negative: whipsaw, lag, marginal after costs) — here 'cross' is PRICE crossing back over a SINGLE MA, and every kind is conditioned and gated so the published criticisms are answered by construction. The engine picks the per-(code, trend) breach bar from a candidate grid: the SELF-ADAPTIVE rule_params (the same window measures asymmetric populations in different trends).",
  "condition": "price crossing back over its own MA (reclaim, conditioned on the cross excess), the failed-rally breakdown (retest), or the raw-gap band breach below the MA (gdepth)",
  "sources": [
    "analysis.mov_ave_spreads_detail.price_vs_ma{20,60,120,255}",
    "analysis.mov_ave_spreads_detail_ema.price_vs_ema{20,60,120,255}"
  ],
  "principles": {
    "asymmetry": "gdepth is BUY-ONLY — deep gaps ABOVE the MA measured uniformly negative (overbought extremes are MOMENTUM for drift-up names); the retest sell is EXIT TIMING for longs, never a short entry.",
    "episodes": "One signal per episode at the FIRST day, K=5 out-of-band traded-day absorb on the code's own calendar.",
    "trigger_time_only": "All conditioning uses trigger-time-observable state (the cross excess at the last below-day, the episode gap) — episode FINAL sizes are hindsight and never condition anything.",
    "regime_free": "Per-regime cells measured thin and sign-flippy: every row carries the pooled regime 4; trend remains a split (the per-trend bar asymmetry is the family's self-adaptive axis)."
  },
  "sub_signals": {
    "ma20_cross": {
      "meaning": "The MA20 reclaim — the first close back above MA20, conditioned on the cross excess (the signed discount at the last below-day); the engine picks the excess bar per (code, trend) from {1, 2, 3}%",
      "trigger": "gap20 >= 0 today, gap20 < 0 yesterday, yesterday's gap <= the cell's excess bar",
      "action": {
        "buy": "reversal up"
      },
      "params": {
        "window": 20,
        "excess_candidates_pct": [
          1,
          2,
          3
        ],
        "rule_entry": "MaCrossReclaimEntry"
      }
    },
    "ma60_cross": {
      "meaning": "The MA60 reclaim — the same conditioned reclaim on the intermediate window",
      "trigger": "gap60 >= 0 today, gap60 < 0 yesterday, yesterday's gap <= the cell's excess bar",
      "action": {
        "buy": "reversal up"
      },
      "params": {
        "window": 60,
        "excess_candidates_pct": [
          1,
          2,
          3
        ],
        "rule_entry": "MaCrossReclaimEntry"
      }
    },
    "ma20_retest": {
      "meaning": "The failed rally / double top — an MA20 breakdown that closes a re-test rally whose above-episode started within 20 traded days of the previous episode's end",
      "trigger": "first close below MA20 after a short-gap above-run (episode gap <= 20 traded days)",
      "action": {
        "sell": "exit timing"
      },
      "params": {
        "window": 20,
        "retest_window_days": 20,
        "rule_entry": "MaRetestEntry"
      }
    },
    "ma120_gap": {
      "meaning": "The raw-gap band breach below MA120 (the Bollinger band-breach architecture on the raw gap axis); the engine picks the gap bar per (code, trend) from {3, 5, 8}%",
      "trigger": "gap120 <= the cell's gap bar",
      "action": {
        "buy": "reversal up"
      },
      "params": {
        "window": 120,
        "bar_candidates_pct": [
          3,
          5,
          8
        ],
        "rule_entry": "MaGdepthEntry"
      }
    },
    "ma255_gap": {
      "meaning": "The raw-gap band breach below the YEAR window MA255 — the deepest-trend discount; the engine picks the gap bar per (code, trend) from {3, 5, 8}%",
      "trigger": "gap255 <= the cell's gap bar",
      "action": {
        "buy": "reversal up"
      },
      "params": {
        "window": 255,
        "bar_candidates_pct": [
          3,
          5,
          8
        ],
        "rule_entry": "MaGdepthEntry"
      }
    }
  }
}$json$::jsonb,
       '2026-10-14'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'ma_cross'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
