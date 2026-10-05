-- ============================================================================
--  Table: stats.label_dim  (closed-vocabulary label <-> SMALLINT code map)
--
--  The DB-side source of truth for every SMALLINT-coded label column
--  (the label-code contract): a converted column stores the CODE, this
--  table maps code -> label, and the boundary mirrors carry the same
--  mapping — Python _common/labels.py (pipelines), TypeScript
--  data_viz/api/services/label-codes.ts (API decode/params).
--  temp_scripts/check_label_dim.py asserts all three stay identical.
--
--  WHY codes: label strings riding cudf.pandas frames through
--  sort/merge/concat chains fault at host transfer on cuDF 26.08
--  (cudaErrorIllegalAddress — string offsets/chars view corruption);
--  int16 codes are cudf-native through every op. Strings are decoded
--  ONLY at the API boundary and for display/logging — never inside a
--  compute frame.
--
--  Converted columns (2026-10-05 refactor):
--    regime     stats.market_regimes, stats.market_regime_spans,
--               analysis_signals.signal_thresholds / history_signals
--               (as regime_state), live.live_signals (as regime_state)
--    side       analysis_signals.signal_thresholds
--    action     analysis_signals.signal_thresholds / history_signals,
--               live.live_signals
--    weighting  analysis.industry_hypes_and_drains / _seasonal
--    rank_side  analysis.industry_hypes_and_drains / _seasonal
--    pool_size  stats.industry_basic_stats, analysis.industry_correlations,
--               analysis.industry_etf_contribution,
--               analysis_composites.industry_corr_benchmark_offsets
--
--  NOT converted (deliberately): sec_type / industry_id / sector_id
--  (API-facing identity + filter columns; in cudf frames they ride as
--  INTERNAL int16 codes materialized back at the Python write boundary
--  — _common.labels.SEC_TYPE_CODE), signal_sub_type (compositional
--  vocabulary), attribution_type (SQL-side aggregation only).
-- ============================================================================

DROP TABLE IF EXISTS stats.label_dim CASCADE;

CREATE TABLE stats.label_dim (
    domain     TEXT     NOT NULL,  -- vocabulary name (the converted column family)
    code       SMALLINT NOT NULL,  -- the stored value in the owning table
    label      TEXT     NOT NULL,  -- the human/API string
    sort_order SMALLINT NOT NULL,  -- presentation order for API/UI listings

    CONSTRAINT pk_label_dim PRIMARY KEY (domain, code),
    CONSTRAINT uq_label_dim_domain_label UNIQUE (domain, label)
);

INSERT INTO stats.label_dim (domain, code, label, sort_order) VALUES
    -- regime: code order == builds.market_regimes REGIMES tuple ==
    -- lexicographic label order (ORDER BY code == ORDER BY label)
    ('regime',     0, 'calm',   0),
    ('regime',     1, 'hot',    1),
    ('regime',     2, 'panic',  2),
    ('regime',     3, 'quiet',  3),
    -- side: the bucket's own side of the indicator
    ('side',       0, 'top',    0),
    ('side',       1, 'bottom', 1),
    -- action: 0 buy (reversal up), 1 sell (down) — pairs with the
    -- excess-sign convention (sell <-> positive signal_excess)
    ('action',     0, 'buy',    0),
    ('action',     1, 'sell',   1),
    -- weighting: industry aggregation family (equal-weight vs
    -- trading_amount-weighted)
    ('weighting',  0, 'equal',  0),
    ('weighting',  1, 'amt',    1),
    -- rank_side: hypes-and-drains ranking direction
    ('rank_side',  0, 'HYPE',   0),
    ('rank_side',  1, 'DRAIN',  1),
    -- pool_size: code order is SEMANTIC (small -> mid -> large -> all);
    -- sort_order keeps the pre-refactor TEXT presentation order
    -- (alphabetical) so API/UI listings are unchanged
    ('pool_size',  0, 'small',  3),
    ('pool_size',  1, 'mid',    2),
    ('pool_size',  2, 'large',  1),
    ('pool_size',  3, 'all',    0);

COMMENT ON TABLE  stats.label_dim IS 'Closed-vocabulary label <-> SMALLINT code map (the label-code contract): every SMALLINT-coded label column stores the code and decodes through this table at the API boundary. Mirrors: _common/labels.py (Python), data_viz/api/services/label-codes.ts (TS) — temp_scripts/check_label_dim.py asserts the three stay identical. Labels never ride cudf compute frames as strings (cuDF 26.08 string host-transfer fault class).';
COMMENT ON COLUMN stats.label_dim.domain     IS 'Vocabulary name = the converted column family: regime / side / action / weighting / rank_side / pool_size.';
COMMENT ON COLUMN stats.label_dim.code       IS 'The value stored in the owning table''s converted column (0-based, SMALLINT).';
COMMENT ON COLUMN stats.label_dim.label      IS 'The human/API string the code decodes to (calm, buy, HYPE, all, ...).';
COMMENT ON COLUMN stats.label_dim.sort_order IS 'Presentation order for listings that enumerate a domain. pool_size deliberately differs from code order: it preserves the pre-refactor alphabetical TEXT order (all, large, mid, small) the API/UI already render; code order is semantic (small, mid, large, all).';
