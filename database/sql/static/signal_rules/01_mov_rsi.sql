-- mov_rsi — the family knowledge document (see 00_schema.sql).
INSERT INTO static.signal_rules_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "sub_signals": {
    "rsi14_1pct": {
      "meaning": "The deep capitulation — RSI below its own 1% tail",
      "trigger": "rsi_14days < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell)"
    },
    "rsi14_2pct": {
      "meaning": "The 2% tail episode, excluding days already claimed by the 1% tail",
      "trigger": "rsi_14days in its own 2% tail band (nested under the 1% carve)"
    },
    "rsi14_3pct": {
      "meaning": "The shallow 3% tail episode — the widest net, excluding deeper claims",
      "trigger": "rsi_14days in its own 3% tail band (nested under the 2% carve)"
    },
    "rsi10_1pct": {
      "meaning": "The deep capitulation — RSI below its own 1% tail",
      "trigger": "rsi_10days < trailing-10y 1% quantile (bottom side, buy) / > 99% quantile (top side, sell)"
    },
    "rsi10_2pct": {
      "meaning": "The 2% tail episode, excluding days already claimed by the 1% tail",
      "trigger": "rsi_10days in its own 2% tail band (nested under the 1% carve)"
    },
    "rsi10_3pct": {
      "meaning": "The shallow 3% tail episode — the widest net, excluding deeper claims",
      "trigger": "rsi_10days in its own 3% tail band (nested under the 2% carve)"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'mov_rsi'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
