-- futures — treasury yield columns backing the bond futures'
-- theoretical-price compute (column set from BOND_PRODUCT_TENOR).
-- args:
--   yield_columns: fragment — the yield select list (per tenor:
--       '<col>::float8 AS <col>', joined with ', ')

        SELECT extract(epoch from date)::float8 AS date,
               {{yield_columns}}
        FROM stats.debt_treasury
        WHERE date IS NOT NULL
        ORDER BY date
    