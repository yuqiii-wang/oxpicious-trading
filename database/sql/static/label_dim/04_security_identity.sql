-- ============================================================================
--  static.label_dim seed — SECURITY IDENTITY vocabularies
--
--  What a security / contract IS: the security class, listing board,
--  owner classification, and the futures / options contract classes.
--  Owning tables: stats.stock_identity / sec_board_map (board),
--  stats.sec_owners (sec_owner_type), stats.futures_identity +
--  analysis.futures_gap_quintiles (contract_type), stats.options_terms
--  + the analysis.options_* tables (option_type), the analysis.mov_ave*
--  family and monitor.src_data_audit (sec_type); skew_type is PENDING
--  (analysis.options_skewness_stats / live.options_intraday_skewness).
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- sec_type: security class (converted in the analysis.mov_ave*
    -- family + the analysis_signals tables — other sec_type columns
    -- keep TEXT, see 01_label_dim.sql header)
    ('sec_type',   0, 'etf',   0),
    ('sec_type',   1, 'index', 1),
    ('sec_type',   2, 'stock', 2),
    -- board: listing board
    ('board',      0, 'MAIN', 0),
    ('board',      1, 'STAR', 1),
    ('board',      2, 'GEM',  2),
    ('board',      3, 'BSE',  3),
    -- sec_owner_type: stats.sec_owners.type vocabulary (CHECK order)
    ('sec_owner_type', 0, 'fund_manager', 0),
    ('sec_owner_type', 1, 'broker',       1),
    ('sec_owner_type', 2, 'asset_manager', 2),
    ('sec_owner_type', 3, 'insurance',    3),
    ('sec_owner_type', 4, 'bank',         4),
    ('sec_owner_type', 5, 'company',      5),
    ('sec_owner_type', 6, 'other',        6),
    -- contract_type: futures
    ('contract_type', 0, 'index', 0),
    ('contract_type', 1, 'bond',  1),
    -- option_type: options contract class (converted 2026-10-07 —
    -- stats.options_terms + the analysis.options_* tables)
    ('option_type', 0, 'CALL', 0),
    ('option_type', 1, 'PUT',  1),
    -- PENDING domain: vocabulary registered ahead of the table
    -- conversion — the owning columns are STILL TEXT (see header)
    -- skew_type: options skew metric family (DDL order)
    ('skew_type',  0, 'oi_moneyness', 0),
    ('skew_type',  1, 'iv_smile',     1),
    ('skew_type',  2, 'greek_delta',  2),
    ('skew_type',  3, 'greek_gamma',  3),
    ('skew_type',  4, 'greek_vega',   4);
