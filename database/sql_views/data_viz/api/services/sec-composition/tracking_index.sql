-- The primary tracking index of an ETF from stats.sec_classification
-- (type='etf'; parent_index_code populated by build_classification.py from
-- the CSIndex CSV) — the composition fallback when the ETF itself has no
-- holdings. NULL when parent_index_code is empty. The index name resolves
-- via a self-JOIN on the index's own sec_classification row.
-- Serves GET /api/sec-composition.
-- args:
--   codes: value text[] — codeVariants(stripped etf code)
SELECT sc.parent_index_code AS index_code,
            si.name               AS index_name
       FROM stats.sec_classification sc
       LEFT JOIN stats.sec_classification si
         ON si.code = sc.parent_index_code AND si.type = 'index'
      WHERE sc.type = 'etf'
        AND sc.code = ANY({{codes}}::text[])
        AND sc.parent_index_code <> ''
      LIMIT 1
