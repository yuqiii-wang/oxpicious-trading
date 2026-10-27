-- rebound — the family knowledge document (see 00_schema.sql).
-- 2026-10-26 first seed: the drop→rebound→resolution family from the
-- ma-spread rebound study (temp_scripts/ma_spread_rebound_study.py,
-- docs/ma_spread_rebound_study_2026-10-26.md), user-directed
-- implementation with the is_pe_boosted PK axis — each sub type emits
-- a PLAIN and a PE-conditioned variant.
INSERT INTO static.signal_rules_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "sub_signals": {
    "rebound_capit_60": {
      "meaning": "The close breaks the running trough after a big drop from the trailing-60d high — the capitulation V-bottom",
      "trigger": "close < trough (episode active: dd <= -5%|10% from hi60[t-1])"
    },
    "rebound_capit_255": {
      "meaning": "The close breaks the running trough after a big drop from the trailing-255d high — the deep capitulation",
      "trigger": "close < trough (episode active: dd <= -5%|10% from hi255[t-1])"
    },
    "rebound_fail_60": {
      "meaning": "The rebound stalls within 10% of the last 60d high and rolls over (recovery ratio R in [0.90, 1.0)) — the failed reclaim",
      "trigger": "close < peak x 0.97 with R in [0.90, 1.0)"
    },
    "rebound_fail_255": {
      "meaning": "The failed reclaim of the trailing-255d high — the yearly-anchor stall",
      "trigger": "close < peak x 0.97 with R in [0.90, 1.0)"
    },
    "rebound_reclaim_60": {
      "meaning": "The close reclaims the trailing-60d high after the drop — the full-reclaim continuation",
      "trigger": "close > hi60[t-1] (episode active)"
    },
    "rebound_reclaim_255": {
      "meaning": "The close reclaims the trailing-255d high after the drop — the yearly-anchor reclaim",
      "trigger": "close > hi255[t-1] (episode active)"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'rebound'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
