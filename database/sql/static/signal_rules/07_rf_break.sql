-- rf_break — the family knowledge document (see 00_schema.sql).
-- 2026-10-26 rewrite: the LEGACY CLEANUP (user-directed) retired the
-- catch-all roof-for-buy / floor-for-sell sub types — the inventory is
-- the depth tiers + the floor bounce, ALL action BUY (the family
-- carries no sell); reflects the depth-split study
-- (temp_scripts/rf_depth_split_study.py) and the two-method gap study
-- (temp_scripts/rf_gap_study.py).
INSERT INTO static.signal_rules_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "sub_signals": {
    "rf60_roof_deep": {
      "meaning": "A close more than k% above the prior day's 60-day donchian roof (k self-adaptive in {1,2,3}%)",
      "trigger": "close > roof60[t-1] x (1 + k)"
    },
    "rf120_roof_deep": {
      "meaning": "A close more than k% above the prior day's 120-day donchian roof",
      "trigger": "close > roof120[t-1] x (1 + k)"
    },
    "rf255_roof_deep": {
      "meaning": "A close more than k% above the prior day's 255-day donchian roof",
      "trigger": "close > roof255[t-1] x (1 + k)"
    },
    "rf500_roof_deep": {
      "meaning": "A close more than k% above the prior day's 500-day donchian roof — the two-year-high continuation",
      "trigger": "close > roof500[t-1] x (1 + k)"
    },
    "rf60_floor_deep": {
      "meaning": "A close more than k% below the prior day's 60-day donchian floor (k self-adaptive in {2,3,5}%)",
      "trigger": "close < floor60[t-1] x (1 - k)"
    },
    "rf120_floor_deep": {
      "meaning": "A close more than k% below the prior day's 120-day donchian floor",
      "trigger": "close < floor120[t-1] x (1 - k)"
    },
    "rf255_floor_deep": {
      "meaning": "A close more than k% below the prior day's 255-day donchian floor",
      "trigger": "close < floor255[t-1] x (1 - k)"
    },
    "rf500_floor_deep": {
      "meaning": "A close more than k% below the prior day's 500-day donchian floor — the two-year-low washout",
      "trigger": "close < floor500[t-1] x (1 - k)"
    },
    "rf60_med_roof_deep": {
      "meaning": "A close more than k% above the prior day's 60-day median-consensus roof",
      "trigger": "close > mroof60[t-1] x (1 + k)"
    },
    "rf120_med_roof_deep": {
      "meaning": "A close more than k% above the prior day's 120-day median-consensus roof",
      "trigger": "close > mroof120[t-1] x (1 + k)"
    },
    "rf255_med_roof_deep": {
      "meaning": "A close more than k% above the prior day's 255-day median-consensus roof",
      "trigger": "close > mroof255[t-1] x (1 + k)"
    },
    "rf500_med_roof_deep": {
      "meaning": "A close more than k% above the prior day's 500-day median-consensus roof",
      "trigger": "close > mroof500[t-1] x (1 + k)"
    },
    "rf60_med_floor_deep": {
      "meaning": "A close more than k% below the prior day's 60-day median-consensus floor",
      "trigger": "close < mfloor60[t-1] x (1 - k)"
    },
    "rf120_med_floor_deep": {
      "meaning": "A close more than k% below the prior day's 120-day median-consensus floor",
      "trigger": "close < mfloor120[t-1] x (1 - k)"
    },
    "rf255_med_floor_deep": {
      "meaning": "A close more than k% below the prior day's 255-day median-consensus floor",
      "trigger": "close < mfloor255[t-1] x (1 - k)"
    },
    "rf500_med_floor_deep": {
      "meaning": "A close more than k% below the prior day's 500-day median-consensus floor",
      "trigger": "close < mfloor500[t-1] x (1 - k)"
    },
    "rf60_floor_bounce": {
      "meaning": "The close breaks the prior day's 60-day MEDIAN consensus floor while the 60-day DONCHIAN wick floor still holds below — the wick-bounded dip",
      "trigger": "close < mfloor60[t-1] AND close >= floor60[t-1]"
    },
    "rf120_floor_bounce": {
      "meaning": "The close breaks the prior day's 120-day median consensus floor while the donchian wick floor holds",
      "trigger": "close < mfloor120[t-1] AND close >= floor120[t-1]"
    },
    "rf255_floor_bounce": {
      "meaning": "The close breaks the prior day's 255-day median consensus floor while the donchian wick floor holds",
      "trigger": "close < mfloor255[t-1] AND close >= floor255[t-1]"
    },
    "rf500_floor_bounce": {
      "meaning": "The close breaks the prior day's 500-day median consensus floor while the donchian wick floor holds",
      "trigger": "close < mfloor500[t-1] AND close >= floor500[t-1]"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'rf_break'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
