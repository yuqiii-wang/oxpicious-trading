-- momentum — the family knowledge document (see 00_schema.sql).
INSERT INTO static.signal_rules_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "sub_signals": {
    "mom5_1pct": {
      "meaning": "The deep weekly crash — 5-day log-return below its own 1% tail",
      "trigger": "ln(1+momentum_5days) < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell)"
    },
    "mom5_2pct": {
      "meaning": "The 2% weekly tail episode, excluding days already claimed by the 1% tail",
      "trigger": "ln(1+momentum_5days) in its own 2% tail band (nested under the 1% carve)"
    },
    "mom5_3pct": {
      "meaning": "The shallow 3% weekly tail episode — the widest net, excluding deeper claims",
      "trigger": "ln(1+momentum_5days) in its own 3% tail band (nested under the 2% carve)"
    },
    "mom20_1pct": {
      "meaning": "The deep monthly crash — 20-day log-return below its own 1% tail",
      "trigger": "ln(1+momentum_20days) < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell)"
    },
    "mom20_2pct": {
      "meaning": "The 2% monthly tail episode, excluding deeper claims",
      "trigger": "ln(1+momentum_20days) in its own 2% tail band (nested under the 1% carve)"
    },
    "mom20_3pct": {
      "meaning": "The shallow 3% monthly tail episode — the widest net, excluding deeper claims",
      "trigger": "ln(1+momentum_20days) in its own 3% tail band (nested under the 2% carve)"
    },
    "momshort_1pct": {
      "meaning": "The deep crash on the vol-adaptive SHORT term (~one expected 3% move) — below its own 1% tail",
      "trigger": "ln(1+momentum_short) < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell); the lookback itself is vol-adaptive per (code, regime)"
    },
    "momshort_2pct": {
      "meaning": "The 2% adaptive-short tail episode, excluding deeper claims",
      "trigger": "ln(1+momentum_short) in its own 2% tail band (nested under the 1% carve)"
    },
    "momshort_3pct": {
      "meaning": "The shallow 3% adaptive-short tail episode — the widest net",
      "trigger": "ln(1+momentum_short) in its own 3% tail band (nested under the 2% carve)"
    },
    "mommid_1pct": {
      "meaning": "The deep crash on the vol-adaptive MID term (~one expected 6% move) — below its own 1% tail",
      "trigger": "ln(1+momentum_mid) < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell); the lookback itself is vol-adaptive per (code, regime)"
    },
    "mommid_2pct": {
      "meaning": "The 2% adaptive-mid tail episode, excluding deeper claims",
      "trigger": "ln(1+momentum_mid) in its own 2% tail band (nested under the 1% carve)"
    },
    "mommid_3pct": {
      "meaning": "The shallow 3% adaptive-mid tail episode — the widest net",
      "trigger": "ln(1+momentum_mid) in its own 3% tail band (nested under the 2% carve)"
    },
    "momlong_1pct": {
      "meaning": "The deep drawdown on the vol-adaptive LONG term (~one expected 12% move) — below its own 1% tail",
      "trigger": "ln(1+momentum_long) < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell); the lookback itself is vol-adaptive per (code, regime)"
    },
    "momlong_2pct": {
      "meaning": "The 2% adaptive-long tail episode, excluding deeper claims",
      "trigger": "ln(1+momentum_long) in its own 2% tail band (nested under the 1% carve)"
    },
    "momlong_3pct": {
      "meaning": "The shallow 3% adaptive-long tail episode — the widest net",
      "trigger": "ln(1+momentum_long) in its own 3% tail band (nested under the 2% carve)"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'momentum'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
