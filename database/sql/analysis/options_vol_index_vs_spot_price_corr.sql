

-- ----------------------------------------------------------------------------
-- options_vol_index_vs_spot_price_corr — rolling correlation between the
-- 30d model-free vol index and its underlying's spot price, per underlying
-- (the VIX-vs-spot "fear gauge" relation: CBOE documents a historically
-- strong INVERSE relationship between the S&P 500 and VIX, conventionally
-- measured as daily spot RETURNS vs daily vol-index CHANGES — both
-- stationary. Correlating the LEVELS would be spurious on trending
-- series.) Formulation: per trading session t,
--   x_t = ln(spot_t / spot_{t-1})        daily spot log-return
--   y_t = ln(vol_t / vol_{t-1})          daily vol-index log-change
--   corr_Wd_t = Pearson corr of x,y over the trailing W TRADING sessions
--               (sessions where BOTH x_t and y_t exist; NULL until the
--               window holds W such joint-valid pairs — honest gaps, a
--               missing vol-index or spot day NaNs its session AND the
--               one after, never a cross-gap return)
-- Windows 20/60/252 sessions (≈ 1m / 3m / 1y, the repo ma-convention).
-- A-share daily spot-logret vs vol-logchg typically sits near -0.6..-0.8
-- (the leverage effect: declines bid up vol faster than rallies deflate
-- it). Append-only sampling: fresh underlyings seed the 5-session
-- stride grid (the 5th, 10th, ... row of their options_vol_index
-- history) + their latest session; each incremental run then adds every
-- session STRICTLY AFTER the underlying's latest stored corr date (one
-- row per new trading day, each row's windows reaching back up to 252
-- sessions) — stored rows are never rewritten or deleted. Spot source:
-- analysis.options_spot_daily (the options loader's
-- settlement close — same data family as the vol index; log-returns are
-- scale-invariant so venue 厘/yuan units cancel). Date-granular — no FK
-- to options_expiry_identity.
-- ----------------------------------------------------------------------------
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: How strongly each underlying's vol index moves with its spot price over trailing windows.
-- tags: options, 期权, vol index, 波动率指数, correlation, 相关性, spot, 现货

CREATE TABLE IF NOT EXISTS analysis.options_vol_index_vs_spot_price_corr (
    date                      DATE          NOT NULL,
    underlying_code           TEXT          NOT NULL,

    -- stride by 5 sessions, corr for the 20d / 60d / 252d windows
    corr_20d                  NUMERIC(6,4),     -- Pearson corr of daily spot log-return vs vol-index log-change, trailing 20 sessions (≈ 1 month)
    corr_60d                  NUMERIC(6,4),     -- trailing 60 sessions (≈ 3 months)
    corr_252d                 NUMERIC(6,4),     -- trailing 252 sessions (≈ 1 year)

    CONSTRAINT pk_options_vol_index_vs_spot_price_corr
        PRIMARY KEY (underlying_code, date)
) PARTITION BY HASH (underlying_code);

-- Native hash partitions (8) keyed on underlying_code — the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07.
SELECT public.create_hash_partitions('analysis', 'options_vol_index_vs_spot_price_corr', 8);

COMMENT ON TABLE  analysis.options_vol_index_vs_spot_price_corr IS 'Rolling correlation between the 30d model-free vol index (analysis.options_vol_index) and the underlying spot close (analysis.options_spot_daily): per session x = ln(spot_t/spot_{t-1}), y = ln(vol_t/vol_{t-1}); corr_20d/60d/252d = Pearson corr of x,y over the trailing 20/60/252 sessions (joint-valid pairs only, NULL until the window is full). Append-only sampling: fresh underlyings seed the 5-session stride grid + latest session; each incremental run adds every session strictly after the underlying''s latest stored corr date (one row per new trading day, windows reaching back up to 252 sessions) — stored rows are never rewritten or deleted. Level-vs-level correlation is deliberately NOT stored (spurious on trending series). PK (underlying_code, date); no FK (date-granular). Built by analysis.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_vol_index_vs_spot_price_corr.corr_20d  IS 'Pearson corr of daily spot log-return vs daily vol-index log-change over the trailing 20 trading sessions (joint-valid pairs only; NULL until 20 such pairs).';
COMMENT ON COLUMN analysis.options_vol_index_vs_spot_price_corr.corr_60d  IS 'Same over the trailing 60 trading sessions.';
COMMENT ON COLUMN analysis.options_vol_index_vs_spot_price_corr.corr_252d IS 'Same over the trailing 252 trading sessions.';

INSERT INTO analysis.analysis_identity (name, detail_name, summary_name, last_run_datetime, description) VALUES
    ('options_vol_index_vs_spot_price_corr', 'options_vol_index_vs_spot_price_corr', NULL, NOW(),
     'Rolling correlation between the 30d model-free vol index and the underlying spot close: per session x = ln(spot_t/spot_{t-1}) vs y = ln(vol_t/vol_{t-1}); corr_20d/60d/252d = Pearson corr over the trailing 20/60/252 sessions (joint-valid pairs only). Append-only sampling: fresh underlyings seed the 5-session stride grid + latest session; each incremental run adds every session after the underlying''s latest stored corr date. PK (underlying_code, date), no FK (date-granular). Built by analysis.options; all INSERTs in Python per project rule.')
ON CONFLICT (name) DO UPDATE SET
    detail_name       = EXCLUDED.detail_name,
    summary_name      = EXCLUDED.summary_name,
    last_run_datetime = NOW(),
    description       = EXCLUDED.description;
