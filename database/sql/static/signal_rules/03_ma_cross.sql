-- ma_cross — the family knowledge document (see 00_schema.sql).
INSERT INTO static.signal_rules_docs (signal_type, document, updated_at)
SELECT ld.code,
       $json${
  "sub_signals": {
    "ma20_cross": {
      "meaning": "The MA20 reclaim — the first close back above MA20, conditioned on the cross excess (the signed discount at the last below-day); the engine picks the excess bar per (code, trend) from {1, 2, 3}%",
      "trigger": "gap20 >= 0 today, gap20 < 0 yesterday, yesterday's gap <= the cell's excess bar"
    },
    "ma60_cross": {
      "meaning": "The MA60 reclaim — the same conditioned reclaim on the intermediate window",
      "trigger": "gap60 >= 0 today, gap60 < 0 yesterday, yesterday's gap <= the cell's excess bar"
    },
    "ma20_retest": {
      "meaning": "The failed rally / double top — an MA20 breakdown that closes a re-test rally whose above-episode started within 20 traded days of the previous episode's end",
      "trigger": "first close below MA20 after a short-gap above-run (episode gap <= 20 traded days)"
    },
    "ma120_gap": {
      "meaning": "The raw-gap band breach below MA120 (the Bollinger band-breach architecture on the raw gap axis); the engine picks the gap bar per (code, trend) from {3, 5, 8}%",
      "trigger": "gap120 <= the cell's gap bar"
    },
    "ma255_gap": {
      "meaning": "The raw-gap band breach below the YEAR window MA255 — the deepest-trend discount; the engine picks the gap bar per (code, trend) from {3, 5, 8}%",
      "trigger": "gap255 <= the cell's gap bar"
    }
  }
}$json$::jsonb,
       '2026-10-27'::timestamp
FROM static.label_dim ld
WHERE ld.domain = 'signal_type' AND ld.label = 'ma_cross'
ON CONFLICT (signal_type) DO UPDATE
SET document = EXCLUDED.document,
    updated_at = EXCLUDED.updated_at;
