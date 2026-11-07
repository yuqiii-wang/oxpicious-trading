-- MA-Spread "pxvol" extras group — one code's per-date Price ×
-- Trading-Amount state registry rows (analysis.mov_ave_price_vs_amt — the
-- px_vol family's date-level source of truth). Every state-valid day joins
-- exactly ONE of the 15 px_speed × vol_state categories; the UI shades the
-- matching dates per picked combo. px_speed/vol_state decode to their
-- label_dim TEXT labels here (labels never ride frames).
-- Serves GET /api/analysis/mov-ave-spread/extras (metrics=pxvol).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
--   px_speed_decode: fragment — labelDecodeSql("px_speed", "p.px_speed")
--   vol_state_decode: fragment — labelDecodeSql("vol_state", "p.vol_state")
    SELECT
      p.date,
      {{px_speed_decode}} AS px_speed,
      {{vol_state_decode}} AS vol_state
    FROM analysis.mov_ave_price_vs_amt p
    WHERE p.sec_type = {{sec_type}}
      AND p.code = ANY({{codes}}::text[])
    ORDER BY p.date
