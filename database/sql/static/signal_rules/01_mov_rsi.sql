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
    },
    "rsi14_fswing": {
      "meaning": "The Wilder failure swing (2026-11-19, the RSI canon study's graduate: +1.78% pooled, hit 73%) — the capitulation that FAILS to make a new price-extreme in RSI space",
      "trigger": "inside an rsi_14days tail3 episode: trough (min RSI within 40 rows) -> bounce high (max RSI within the next 20 rows) -> re-dip HOLDING above the trough -> BUY the first RSI cross back above the bounce high (within 60 rows, min segment 5); BUY-only — rides the same per-code adaptive trailing-10y 3% quantile bar (shift 1)"
    },
    "rsi10_fswing": {
      "meaning": "The Wilder failure swing on the measured-best buy window (2026-11-19: +2.14% pooled, hit 75%)",
      "trigger": "inside an rsi_10days tail3 episode: trough -> bounce high -> re-dip holding above the trough -> BUY the first cross back above the bounce high; BUY-only — same pattern constants and adaptive bars as rsi14_fswing"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'mov_rsi'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
