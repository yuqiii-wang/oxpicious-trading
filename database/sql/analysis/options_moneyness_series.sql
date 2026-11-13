-- ============================================================================
--  Options Moneyness Series + Spot Daily — materialized read models for the
--  OI-weighted Moneyness Skew panel (/api/szse-options/oi-moneyness-series).
--
--  The endpoint used to compute the series LIVE per request: a full-history
--  5-way join of stats.options_terms LEFT JOIN options_strike /
--  options_settlement / options_greeks / options_volume_oi (~1M rows per
--  side, hash-partitioned by contract_code so the underlying filter can
--  never prune the sub-table scans — measured ~4.8s cold on the biggest
--  CFFEX underlying, plus ~1.7s for the spot-only terms ⋈ settlement leg).
--  Both aggregates change only when the daily options build runs, so they
--  are materialized here by analysis.options (PIPELINE 8) and the API reads
--  the precomputed rows per underlying (PK-prefix indexed, single-digit ms).
--
--  Semantics are EXACTLY the former live SQL (see
--  api/services/options.service.ts getOptionsMoneynessSeries history):
--    • valid rows   : expiry_date >= date AND 0 < implied_vol < 5
--    • weights      : GREATEST(open_interest, 1)  (NULL OI -> weight 1)
--    • e_m          : (Σ w·K / Σ w) / day_spot_close, NULL when n_valid < 3
--                     or the date's close is missing/<= 0
--    • group key    : (underlying_code, date, DATE_TRUNC('month',
--                     expiry_date)); expiry_date = MIN over the group's
--                     valid rows
--    • oi columns   : sums over the group's ACTIVE rows (expiry_date >=
--                     date AND open_interest > 0); OTM = strike beyond
--                     spot on the side's moneyness (call: K >= S, put:
--                     K <= S); 0 when the group has no active rows
--    • spot daily   : per (underlying_code, date) MAX(underlying_close)
--                     over settlement rows (INNER basis — dates without
--                     any settlement row are absent, matching the former
--                     spot query; NULL close stays NULL, the API maps
--                     close <= 0 to null)
--
--  Both tables are keyed by the NATIVE underlying code (same convention as
--  the sibling options_* analysis tables: 1599xx SZSE / 510xxx SSE ETFs,
--  000xxx CFFEX indices) and carry NO FK to options_expiry_identity
--  (month-group keys are not expiry-granular — same exemption class as
--  options_oi_stats / options_vol_index / options_bands).
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The OI-weighted moneyness skew of each underlying's option chain per day and expiry month — where standing open interest sits relative to spot.
-- tags: options, 期权, moneyness, 价度, open interest, 持仓量, skew, 偏度


CREATE TABLE IF NOT EXISTS analysis.options_moneyness_series (
    date                      DATE          NOT NULL,
    underlying_code           TEXT          NOT NULL,
    expiry_month              DATE          NOT NULL,
    expiry_date               DATE,

    n_valid                   INTEGER       NOT NULL DEFAULT 0,
    e_m                       NUMERIC(12,8),
    wsum_k                    NUMERIC(24,4),
    wsum                      NUMERIC(24,4),
    call_oi                   NUMERIC(20,4),
    put_oi                    NUMERIC(20,4),
    call_otm_oi               NUMERIC(20,4),
    put_otm_oi                NUMERIC(20,4),

    CONSTRAINT pk_options_moneyness_series
        PRIMARY KEY (underlying_code, date, expiry_month)
) PARTITION BY HASH (underlying_code);

-- Native hash partitions (8) keyed by code — via the shared util
-- (database/sql/00_partition_utils.sql); children are named _p00.._p07
SELECT public.create_hash_partitions('analysis', 'options_moneyness_series', 8);

COMMENT ON TABLE  analysis.options_moneyness_series IS 'Materialized oi_moneyness skew series per (underlying_code, date, expiry_month) — the precomputed aggregate behind /api/szse-options/oi-moneyness-series (the OI-weighted Moneyness Skew panel). Valid rows: expiry_date >= date AND 0 < implied_vol < 5; weights GREATEST(open_interest, 1); e_m = (Σ w·K / Σ w) / day close (NULL when n_valid < 3 or close missing/<= 0); oi_* columns sum the group''s ACTIVE rows (expiry_date >= date AND open_interest > 0) with OTM = strike beyond spot per side; expiry_date = the group''s MIN valid expiry. Native underlying codes; no FK (month-group keys). Built by analysis.options; all INSERTs in Python per project rule.';
COMMENT ON COLUMN analysis.options_moneyness_series.expiry_month IS 'Month group key = DATE_TRUNC(''month'', expiry_date) — the frontend expiry-month grouping.';
COMMENT ON COLUMN analysis.options_moneyness_series.expiry_date IS 'MIN(expiry_date) over the group''s valid rows on the date — the group boundary the frontend shades.';
COMMENT ON COLUMN analysis.options_moneyness_series.n_valid IS 'Count of the group''s valid rows (expiry_date >= date AND 0 < implied_vol < 5) — n_valid >= 3 gates e_m.';
COMMENT ON COLUMN analysis.options_moneyness_series.e_m IS 'E[M] = Σ(GREATEST(OI,1)·K) / Σ(GREATEST(OI,1)) / day close — OI-weighted mean moneyness of the group (raw-unit ratio; K and close share the loader scale). NULL when n_valid < 3 or the day close is missing/<= 0.';
COMMENT ON COLUMN analysis.options_moneyness_series.wsum_k IS 'Σ GREATEST(open_interest,1) · strike_price over the group''s valid rows (raw strike units; rows with a NULL strike contribute nothing).';
COMMENT ON COLUMN analysis.options_moneyness_series.wsum IS 'Σ GREATEST(open_interest,1) over the group''s valid rows.';
COMMENT ON COLUMN analysis.options_moneyness_series.call_oi IS 'Σ open_interest over the group''s ACTIVE CALL rows; 0 when the group has no active rows.';
COMMENT ON COLUMN analysis.options_moneyness_series.put_oi IS 'Σ open_interest over the group''s ACTIVE PUT rows; 0 when the group has no active rows.';
COMMENT ON COLUMN analysis.options_moneyness_series.call_otm_oi IS 'Σ open_interest over ACTIVE CALL rows with strike >= day close (OTM calls).';
COMMENT ON COLUMN analysis.options_moneyness_series.put_otm_oi IS 'Σ open_interest over ACTIVE PUT rows with strike <= day close (OTM puts).';
