-- options_wall — the family knowledge document (see 00_schema.sql).
INSERT INTO static.signal_rules_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "sub_signals": {
    "ow_put_025pct": {
      "meaning": "The VRP-gated put-wall breach — the capitulation-rebound trade",
      "trigger": "close <= put_wall_low x (1 - width) AND vrp_ratio >= its trailing-255d 60th-percentile bar (shifted); width is the single 0.10% (the 2026-10-19 grid collapse)"
    },
    "ow_put_025pct_rej": {
      "meaning": "The VRP-FAIL twin — the same breach with cheap implied vol (the falling knife); observational only",
      "trigger": "close <= put_wall_low x (1 - width) AND the VRP gate failed; width is the single 0.10% (the 2026-10-19 grid collapse)"
    },
    "ow_call_025pct": {
      "meaning": "The call-wall breach — the exhaustion exit above the chain's outer cap, ungated",
      "trigger": "close >= call_wall_high x (1 + width); width is the single 0.10% (the 2026-10-19 grid collapse)"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'options_wall'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
