-- mov_rsi — the family knowledge document (see 00_schema.sql).
INSERT INTO static.analysis_signal_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "name": "mov_rsi",
  "description": "The RSI percentile family: a BUY fires when Wilder RSI(W), W in {14, 10} falls below the code's OWN bottom tail quantile over its trailing 10y (the capitulation); a SELL when it exceeds the top tail (the exhaustion exit). Bars are per-code adaptive percentile quantiles, never fixed 30/70 — a fixed level either never triggers or triggers constantly.",
  "condition": "RSI(W), W in {14, 10} beyond the code's own trailing-10y 1/2/3% tail quantile (bars shifted one row, no look-ahead)",
  "sources": [
    "analysis.mov_ave_rsi.rsi_14days",
    "analysis.mov_ave_rsi.rsi_10days",
    "analysis.mov_ave_rsi.is_typical_14days",
    "analysis.mov_ave_rsi.is_typical_10days",
    "stats.market_regimes.regime"
  ],
  "principles": {
    "asymmetry": "Sharp falls revert, sharp rises continue: the sell is EXIT TIMING for longs (gate 0), the buy needs a measured edge (gate 0.75% blended dir_ave); sample floors 4 buys / 3 sells — sell evidence is structurally thinner under positive drift.",
    "episodes": "One signal per episode at the FIRST day; a run only ends after more than 5 consecutive out-of-band TRADED days (K=5 absorb — holiday-closure re-entries are not new signals), on the code's own traded-day ordinals.",
    "nested_carving": "The tails carve deepest-first: a deep breach counts once, at its deepest claim (the 1% tail carves out of 2%, 2% out of 3%).",
    "regimes": "Regimes are SPLITS, never weights: sign-aligned reversal after tail breaches concentrates in hot/panic; the calm-trend extreme continues."
  },
  "sub_signals": {
    "rsi14_1pct": {
      "meaning": "The deep capitulation — RSI below its own 1% tail",
      "trigger": "rsi_14days < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell)",
      "action": {
        "buy": "reversal up",
        "sell": "exit timing"
      },
      "params": {
        "window": 14,
        "tail_pct": 1,
        "bar_window_days": 2550
      }
    },
    "rsi14_2pct": {
      "meaning": "The 2% tail episode, excluding days already claimed by the 1% tail",
      "trigger": "rsi_14days in its own 2% tail band (nested under the 1% carve)",
      "action": {
        "buy": "reversal up",
        "sell": "exit timing"
      },
      "params": {
        "window": 14,
        "tail_pct": 2,
        "bar_window_days": 2550
      }
    },
    "rsi14_3pct": {
      "meaning": "The shallow 3% tail episode — the widest net, excluding deeper claims",
      "trigger": "rsi_14days in its own 3% tail band (nested under the 2% carve)",
      "action": {
        "buy": "reversal up",
        "sell": "exit timing"
      },
      "params": {
        "window": 14,
        "tail_pct": 3,
        "bar_window_days": 2550
      }
    },
    "rsi10_1pct": {
      "meaning": "The deep capitulation — RSI below its own 1% tail",
      "trigger": "rsi_10days < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell)",
      "action": {
        "buy": "reversal up",
        "sell": "exit timing"
      },
      "params": {
        "window": 10,
        "tail_pct": 1,
        "bar_window_days": 2550
      }
    },
    "rsi10_2pct": {
      "meaning": "The 2% tail episode, excluding days already claimed by the 1% tail",
      "trigger": "rsi_10days in its own 2% tail band (nested under the 1% carve)",
      "action": {
        "buy": "reversal up",
        "sell": "exit timing"
      },
      "params": {
        "window": 10,
        "tail_pct": 2,
        "bar_window_days": 2550
      }
    },
    "rsi10_3pct": {
      "meaning": "The shallow 3% tail episode — the widest net, excluding deeper claims",
      "trigger": "rsi_10days in its own 3% tail band (nested under the 2% carve)",
      "action": {
        "buy": "reversal up",
        "sell": "exit timing"
      },
      "params": {
        "window": 10,
        "tail_pct": 3,
        "bar_window_days": 2550
      }
    }
  }
}$json$::jsonb,
       '2026-10-15'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'mov_rsi'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
