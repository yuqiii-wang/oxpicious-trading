-- MA-Spread "momentum" extras group — one code's per-date
-- lookback-adaptive momentum rows (analysis.mov_ave_momentum). The regime
-- SMALLINT code decodes to its label here — the only decode boundary
-- (labels never ride the pipeline frames); momentum NUMERICs cast float8
-- server-side.
-- Serves GET /api/analysis/mov-ave-spread/extras (metrics=momentum).
-- args:
--   sec_type: value int — static.label_dim sec_type SMALLINT code (secTypeParam)
--   codes: value text[] — the code variants to chart (codeVariants)
--   regime_decode: fragment — labelDecodeSql("regime", "m.regime")
    SELECT
      m.date,
      {{regime_decode}} AS regime,
      m.vol_regime,
      m.lb_short,
      m.lb_mid,
      m.lb_long,
      m.momentum_5days::float8,
      m.momentum_20days::float8,
      m.momentum_short::float8,
      m.momentum_mid::float8,
      m.momentum_long::float8
    FROM analysis.mov_ave_momentum m
    WHERE m.sec_type = {{sec_type}}
      AND m.code = ANY({{codes}}::text[])
    ORDER BY m.date
