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
    "rf252_roof_deep": {
      "meaning": "A close more than k% above the prior day's 252-day donchian roof",
      "trigger": "close > roof252[t-1] x (1 + k)"
    },
    "rf504_roof_deep": {
      "meaning": "A close more than k% above the prior day's 504-day donchian roof — the two-year-high continuation",
      "trigger": "close > roof504[t-1] x (1 + k)"
    },
    "rf60_floor_deep": {
      "meaning": "A close more than k% below the prior day's 60-day donchian floor (k self-adaptive in {2,3,5}%)",
      "trigger": "close < floor60[t-1] x (1 - k)"
    },
    "rf120_floor_deep": {
      "meaning": "A close more than k% below the prior day's 120-day donchian floor",
      "trigger": "close < floor120[t-1] x (1 - k)"
    },
    "rf252_floor_deep": {
      "meaning": "A close more than k% below the prior day's 252-day donchian floor",
      "trigger": "close < floor252[t-1] x (1 - k)"
    },
    "rf504_floor_deep": {
      "meaning": "A close more than k% below the prior day's 504-day donchian floor — the two-year-low washout",
      "trigger": "close < floor504[t-1] x (1 - k)"
    },
    "rf60_med_roof_deep": {
      "meaning": "A close more than k% above the prior day's 60-day median-consensus roof",
      "trigger": "close > mroof60[t-1] x (1 + k)"
    },
    "rf120_med_roof_deep": {
      "meaning": "A close more than k% above the prior day's 120-day median-consensus roof",
      "trigger": "close > mroof120[t-1] x (1 + k)"
    },
    "rf252_med_roof_deep": {
      "meaning": "A close more than k% above the prior day's 252-day median-consensus roof",
      "trigger": "close > mroof252[t-1] x (1 + k)"
    },
    "rf504_med_roof_deep": {
      "meaning": "A close more than k% above the prior day's 504-day median-consensus roof",
      "trigger": "close > mroof504[t-1] x (1 + k)"
    },
    "rf60_med_floor_deep": {
      "meaning": "A close more than k% below the prior day's 60-day median-consensus floor",
      "trigger": "close < mfloor60[t-1] x (1 - k)"
    },
    "rf120_med_floor_deep": {
      "meaning": "A close more than k% below the prior day's 120-day median-consensus floor",
      "trigger": "close < mfloor120[t-1] x (1 - k)"
    },
    "rf252_med_floor_deep": {
      "meaning": "A close more than k% below the prior day's 252-day median-consensus floor",
      "trigger": "close < mfloor252[t-1] x (1 - k)"
    },
    "rf504_med_floor_deep": {
      "meaning": "A close more than k% below the prior day's 504-day median-consensus floor",
      "trigger": "close < mfloor504[t-1] x (1 - k)"
    },
    "rf60_floor_bounce": {
      "meaning": "The close breaks the prior day's 60-day MEDIAN consensus floor while the 60-day DONCHIAN wick floor still holds below — the wick-bounded dip",
      "trigger": "close < mfloor60[t-1] AND close >= floor60[t-1]"
    },
    "rf120_floor_bounce": {
      "meaning": "The close breaks the prior day's 120-day median consensus floor while the donchian wick floor holds",
      "trigger": "close < mfloor120[t-1] AND close >= floor120[t-1]"
    },
    "rf252_floor_bounce": {
      "meaning": "The close breaks the prior day's 252-day median consensus floor while the donchian wick floor holds",
      "trigger": "close < mfloor252[t-1] AND close >= floor252[t-1]"
    },
    "rf504_floor_bounce": {
      "meaning": "The close breaks the prior day's 504-day median consensus floor while the donchian wick floor holds",
      "trigger": "close < mfloor504[t-1] AND close >= floor504[t-1]"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'rf_break'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
