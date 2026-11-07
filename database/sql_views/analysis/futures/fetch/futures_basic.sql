-- futures — all futures contracts with a close (identity joined
-- to basic_stats; epoch float8 dates) — the pipeline's base
-- frame before the underlying joins.
-- args: none

        SELECT
            extract(epoch from i.date)::float8 AS date,
            i.code,
            i.product_code,
            i.contract_type,
            i.underlying_code,
            i.days_to_expiry,
            b.close::float8 AS futures_close
        FROM stats.futures_identity i
        JOIN stats.futures_basic_stats b
          ON b.date = i.date AND b.code = i.code
        WHERE b.close IS NOT NULL
        ORDER BY i.code, i.date
    