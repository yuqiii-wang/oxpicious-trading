-- crowdedness — the family knowledge document (see 00_schema.sql).
-- 2026-11-16: v4 (user-directed: the turning dates 2020-05-29 /
-- 2022-10-26 / 2025-06-03 / 2026-10-30 must fire BUYs and 2024-11-11
-- / 2026-06-24 SELLS — the v3 states missed each for a different
-- reason; studies temp_scripts/crowd_action_study4..7 +
-- crowd_date_diagnosis). Five additions to the v3 inventory.
INSERT INTO static.signal_rules_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "source": "BOTH hot-set bases of the nightly crowdedness build, LEFT JOINed per code+sec_type+date: roll_* reads analysis.market_crowdedness (the DAILY-ROLLING hot set, re-ranked every date — crowdedness_score the 0.30/0.30/0.25/0.15 blend of the concentration / flow / comovement / valuation pillar percentiles, _ma5 its 5-pool-date online read), year_* reads analysis.market_crowdedness_year joined on hot_year = the date's own calendar year (the THIS-YEAR pinned hot set); the tech_* subs also read analysis.market_crowdedness_hot_tech (the hot set's median px-vs-MA20/MA60 percentiles + the amount-vs-MA20 percentile); the response measured is the INDEX's own forward blend (the trade is the index, the crowdedness stats are the assistant metric); the family's universe is data-driven (the codes with crowdedness rows — index only) and its incremental-skip horizon is the max(date) across BOTH tables",
  "sub_signals": {
    "crowd_roll_score_drain": {
      "meaning": "The rolling hot set's raw composite at its trailing 60-row LOW while the index ROSE over its own trailing 10 rows — the crowd drained while price rose: the move is not overcrowded, the continuation BUY",
      "trigger": "crowdedness_score <= its prior 60-row low AND index ret10 > 0"
    },
    "crowd_roll_score_pilein": {
      "meaning": "The rolling composite at its trailing 60-row HIGH while the index FELL — distribution churn in a decline, the exit-timing SELL",
      "trigger": "crowdedness_score >= its prior 60-row high AND index ret10 < 0"
    },
    "crowd_roll_ma5_drain": {
      "meaning": "The rolling ONLINE read at its trailing 60-row low while the index rose — the sustained crowd drain in a rise",
      "trigger": "crowdedness_score_ma5 <= its prior 60-row low AND index ret10 > 0"
    },
    "crowd_roll_ma5_pilein": {
      "meaning": "The rolling online read at its trailing 60-row high while the index fell — the smoothed pile-in population",
      "trigger": "crowdedness_score_ma5 >= its prior 60-row high AND index ret10 < 0"
    },
    "crowd_year_score_drain": {
      "meaning": "The THIS-YEAR pinned hot set's raw composite at its trailing 60-row low while the index rose — the year-basket's crowd drain in a rise",
      "trigger": "crowdedness_score_year <= its prior 60-row low AND index ret10 > 0"
    },
    "crowd_year_score_pilein": {
      "meaning": "The this-year composite at its trailing 60-row high while the index fell — the year-basket pile-in",
      "trigger": "crowdedness_score_year >= its prior 60-row high AND index ret10 < 0"
    },
    "crowd_year_ma5_drain": {
      "meaning": "The this-year online read at its trailing 60-row low while the index rose",
      "trigger": "crowdedness_score_ma5_year <= its prior 60-row low AND index ret10 > 0"
    },
    "crowd_year_ma5_pilein": {
      "meaning": "The this-year online read at its trailing 60-row high while the index fell",
      "trigger": "crowdedness_score_ma5_year >= its prior 60-row high AND index ret10 < 0"
    },
    "crowd_roll_score_drain_floor": {
      "meaning": "The rolling composite at its trailing-250-row BOTTOM (pos <= 0.10) while the index ticks up over 5 rows — the year-scale crowd floor with the first up-tick (the 2020-05-29 / 2025-06-03 signature). LOOSE gate: only a negative measured mean is rejected",
      "trigger": "crowdedness_score in its trailing-250-row bottom 10% AND index ret5 > 0"
    },
    "crowd_year_score_drain_floor": {
      "meaning": "The this-year composite at its trailing-250-row bottom while the index ticks up (the same year-scale floor read on the pinned basket)",
      "trigger": "crowdedness_score_year in its trailing-250-row bottom 10% AND index ret5 > 0"
    },
    "crowd_tech_washout": {
      "meaning": "The hot set's median px-vs-MA60 percentile UNDER WATER — the falling-knife capitulation catch, any direction (+0.92% forward blend pooled, hit 61%): fresh-depth arm (<= 0.10 at a fresh 20-row depth low with volume picking up — 2026-10-30) OR amount-climax arm (<= 0.20 at amt >= 0.80 — 2022-10-26, crowd under water + 放量 climax)",
      "trigger": "(median_px_vs_ma60_pct <= 0.10 at a fresh 20-row low with median_amt_vs_ma20_pct >= 0.60) OR (median_px_vs_ma60_pct <= 0.20 AND median_amt_vs_ma20_pct >= 0.80)"
    },
    "crowd_tech_euphoria": {
      "meaning": "The crowd ma5 at its 60-row high or within 10% of its trailing-year high while the hot set's px-vs-MA20 percentile >= 0.85 — the blow-off stretch, exit timing (2026-06-24). Sells are not pooled-gated — each code's own episodes judge (000001's measure favorable while the 10-code pooled continues up)",
      "trigger": "(crowdedness_score_ma5 >= its prior 60-row high OR within 10% of its trailing-year high) AND median_px_vs_ma20_pct >= 0.85"
    },
    "crowd_tech_euphoria_div": {
      "meaning": "The euphoria stretch with the crowd composite ALREADY turning down (score_chg5 < 0) — the price/crowd divergence (2024-11-11's signature; 000001's own episodes -0.70% raw = favorable exit timing)",
      "trigger": "crowd_tech_euphoria state AND crowdedness_score - crowdedness_score(-5) < 0"
    }
  }
}$json$::jsonb,
       '2026-11-16'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'crowdedness'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
