-- momentum — the family knowledge document (see 00_schema.sql).
INSERT INTO static.analysis_signal_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "name": "momentum",
  "description": "The log-return momentum family: a BUY fires when a lookback axis's log-return ln(1+momentum) falls below the code's OWN bottom tail quantile over its trailing 10y (the capitulation crash — deep negative cumulative momentum reverts); a SELL when it exceeds the top tail (the exhaustion run-up — exit timing for longs). Self-adaptive at two levels: the short/mid/long lookbacks are vol-adaptive upstream (per (code, regime) equal-expected-move targeting in analysis.mov_ave_momentum), and the bars are per-code trailing percentile quantiles of the axis's own log-return — the multi-level threshold grid is the nested 1/2/3% tail carving.",
  "condition": "ln(1+momentum_{axis}), axis in {5days, 20days, short, mid, long}, beyond the code's own trailing-10y 1/2/3% tail quantile (bars shifted one row, no look-ahead); the confidence depth normalized by the axis's own trailing sigma",
  "sources": [
    "analysis.mov_ave_momentum.momentum_5days",
    "analysis.mov_ave_momentum.momentum_20days",
    "analysis.mov_ave_momentum.momentum_short",
    "analysis.mov_ave_momentum.momentum_mid",
    "analysis.mov_ave_momentum.momentum_long",
    "stats.market_regimes.regime"
  ],
  "principles": {
    "log_return": "The log transform maps the bounded-below (-100%) / unbounded-above rate of change to a symmetric additive space where a trailing quantile bar tracks the code's own volatility level — self-adaptive across codes and volatility regimes (raw-space tails are dominated by the floor asymmetry).",
    "asymmetry": "Sharp falls revert, sharp rises continue: the sell is EXIT TIMING for longs (gate 0), the buy needs a measured edge (gate 0.75% blended dir_ave); sample floors 4 buys / 3 sells.",
    "episodes": "One signal per episode at the FIRST day; a run only ends after more than 5 consecutive out-of-band TRADED days (K=5 absorb — the plain, non-trend-aware absorb until the family's own A/B is measured), on the code's own traded-day ordinals.",
    "nested_carving": "The tails carve deepest-first: a deep breach counts once, at its deepest claim (the 1% tail carves out of 2%, 2% out of 3%).",
    "regimes": "Regimes are SPLITS, never weights: sign-aligned reversal after tail breaches concentrates in hot/panic; the calm-trend extreme continues.",
    "depth_in_sigma": "The breach depth that scales confidence is normalized by the axis's OWN trailing sigma — a 5-day and a 100-day breach compare in sigma units (self-adaptive across the axis grid)."
  },
  "sub_signals": {
    "mom5_1pct": {
      "meaning": "The deep weekly crash — 5-day log-return below its own 1% tail",
      "trigger": "ln(1+momentum_5days) < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "5days", "tail_pct": 1, "bar_window_days": 2550 }
    },
    "mom5_2pct": {
      "meaning": "The 2% weekly tail episode, excluding days already claimed by the 1% tail",
      "trigger": "ln(1+momentum_5days) in its own 2% tail band (nested under the 1% carve)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "5days", "tail_pct": 2, "bar_window_days": 2550 }
    },
    "mom5_3pct": {
      "meaning": "The shallow 3% weekly tail episode — the widest net, excluding deeper claims",
      "trigger": "ln(1+momentum_5days) in its own 3% tail band (nested under the 2% carve)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "5days", "tail_pct": 3, "bar_window_days": 2550 }
    },
    "mom20_1pct": {
      "meaning": "The deep monthly crash — 20-day log-return below its own 1% tail",
      "trigger": "ln(1+momentum_20days) < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "20days", "tail_pct": 1, "bar_window_days": 2550 }
    },
    "mom20_2pct": {
      "meaning": "The 2% monthly tail episode, excluding deeper claims",
      "trigger": "ln(1+momentum_20days) in its own 2% tail band (nested under the 1% carve)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "20days", "tail_pct": 2, "bar_window_days": 2550 }
    },
    "mom20_3pct": {
      "meaning": "The shallow 3% monthly tail episode — the widest net, excluding deeper claims",
      "trigger": "ln(1+momentum_20days) in its own 3% tail band (nested under the 2% carve)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "20days", "tail_pct": 3, "bar_window_days": 2550 }
    },
    "momshort_1pct": {
      "meaning": "The deep crash on the vol-adaptive SHORT term (~one expected 3% move) — below its own 1% tail",
      "trigger": "ln(1+momentum_short) < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell); the lookback itself is vol-adaptive per (code, regime)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "short", "tail_pct": 1, "bar_window_days": 2550 }
    },
    "momshort_2pct": {
      "meaning": "The 2% adaptive-short tail episode, excluding deeper claims",
      "trigger": "ln(1+momentum_short) in its own 2% tail band (nested under the 1% carve)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "short", "tail_pct": 2, "bar_window_days": 2550 }
    },
    "momshort_3pct": {
      "meaning": "The shallow 3% adaptive-short tail episode — the widest net",
      "trigger": "ln(1+momentum_short) in its own 3% tail band (nested under the 2% carve)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "short", "tail_pct": 3, "bar_window_days": 2550 }
    },
    "mommid_1pct": {
      "meaning": "The deep crash on the vol-adaptive MID term (~one expected 6% move) — below its own 1% tail",
      "trigger": "ln(1+momentum_mid) < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell); the lookback itself is vol-adaptive per (code, regime)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "mid", "tail_pct": 1, "bar_window_days": 2550 }
    },
    "mommid_2pct": {
      "meaning": "The 2% adaptive-mid tail episode, excluding deeper claims",
      "trigger": "ln(1+momentum_mid) in its own 2% tail band (nested under the 1% carve)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "mid", "tail_pct": 2, "bar_window_days": 2550 }
    },
    "mommid_3pct": {
      "meaning": "The shallow 3% adaptive-mid tail episode — the widest net",
      "trigger": "ln(1+momentum_mid) in its own 3% tail band (nested under the 2% carve)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "mid", "tail_pct": 3, "bar_window_days": 2550 }
    },
    "momlong_1pct": {
      "meaning": "The deep drawdown on the vol-adaptive LONG term (~one expected 12% move) — below its own 1% tail",
      "trigger": "ln(1+momentum_long) < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell); the lookback itself is vol-adaptive per (code, regime)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "long", "tail_pct": 1, "bar_window_days": 2550 }
    },
    "momlong_2pct": {
      "meaning": "The 2% adaptive-long tail episode, excluding deeper claims",
      "trigger": "ln(1+momentum_long) in its own 2% tail band (nested under the 1% carve)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "long", "tail_pct": 2, "bar_window_days": 2550 }
    },
    "momlong_3pct": {
      "meaning": "The shallow 3% adaptive-long tail episode — the widest net",
      "trigger": "ln(1+momentum_long) in its own 3% tail band (nested under the 2% carve)",
      "action": { "buy": "reversal up", "sell": "exit timing" },
      "params": { "axis": "long", "tail_pct": 3, "bar_window_days": 2550 }
    }
  }
}$json$::jsonb,
       '2026-10-15'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'momentum'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
