-- bollinger — the family knowledge document (see 00_schema.sql).
INSERT INTO static.signal_rules_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "sub_signals": {
    "boll20_2std": {
      "meaning": "The classic 20/2sigma breach — the default Bollinger setup",
      "trigger": "z < -2 (buy) / z > +2 (sell), nested under the deeper bands"
    },
    "boll20_2p5std": {
      "meaning": "The 2.5-sigma breach, excluding days already claimed by 3 sigma",
      "trigger": "z < -2.5 (buy) / z > +2.5 (sell), nested under 3 sigma"
    },
    "boll20_3std": {
      "meaning": "The deep 3-sigma breach — the deepest single claim",
      "trigger": "z < -3 (buy) / z > +3 (sell)"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'bollinger'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
