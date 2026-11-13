-- MA-Spread "alignment" extras group — one code's per-date MA-chain
-- alignment rows (analysis.mov_ave_spreads_detail): the five-leg
-- alignment score (count of positive legs among close>ma5>ma20>ma60>
-- ma120>ma252, 0..5) and its decoded verdict. The alignment_state
-- SMALLINT code decodes to its label here — the only decode boundary
-- (labels never ride the pipeline frames). NULL score rows (insufficient
-- MA history) come back as NULLs, never a verdict.
-- Serves GET /api/analysis/mov-ave-spread/extras (metrics=alignment).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
--   alignment_decode: fragment — labelDecodeSql("ma_alignment", "s.alignment_state")
    SELECT
      s.date,
      s.alignment_score,
      {{alignment_decode}} AS alignment_state
    FROM analysis.mov_ave_spreads_detail s
    WHERE s.sec_type = {{sec_type}}
      AND s.code = ANY({{codes}}::text[])
    ORDER BY s.date
