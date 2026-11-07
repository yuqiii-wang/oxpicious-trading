-- Futures EXT analysis — all analysis.futures_ext rows for ONE product
-- (index or bond), by joining against stats.futures_identity so only rows
-- whose contract_code starts with the product prefix (e.g. "IF2409") are
-- returned. gap_price_vs_underlying and corr_price_vs_underlying are
-- pre-computed by analysis/futures/compute.py.
-- Serves GET /api/analysis/futures/ext
-- args:
--   product: value text — the product_code prefix (e.g. 'IF', 'T')
    SELECT
        fe.date,
        fe.code,
        fe.gap_price_vs_underlying,
        fe.corr_price_vs_underlying
    FROM analysis.futures_ext fe
    JOIN stats.futures_identity fi
      ON fi.date = fe.date AND fi.code = fe.code
    WHERE fi.product_code = {{product}}
    ORDER BY fe.code, fe.date
