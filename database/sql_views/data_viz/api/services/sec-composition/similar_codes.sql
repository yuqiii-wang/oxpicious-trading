-- Similar codes — the latest sec_similars row's five ranked column-pairs
-- unpivoted into rows, each JOINed to sec_classification (type='index') to
-- resolve the display name. The industry / dissimilar-industry variants are
-- the same query with the column-name prefix regex-substituted on the
-- RENDERED text in sec-composition.service.ts (the unpivot shape is
-- identical; only similar_Nst_code_* becomes similar_Nst_industry_code_* /
-- dissimilar_Nst_industry_code_*). Top-5 similar codes + top-5 similar/
-- dissimilar industry-classified peers by MUTUAL shared composition weight
-- (built by builds.index._sec_similars from stats.sec_composition +
-- stats.sec_classification). Serves GET /api/sec-composition/similar-indices.
-- args:
--   code: value text — the subject index code ($1, reused per UNION branch)
--   date: value text — the snapshot date picked by similars_latest_date
--     ($2, reused per UNION branch)
    SELECT u.rank,
           u.code,
           sc.name,
           u.sharing_weight_pct
      FROM (
        SELECT 1 AS rank,
               similar_1st_code_by_sharing_weights AS code,
               similar_1st_code_sharing_weight_pct AS sharing_weight_pct
          FROM stats.sec_similars
         WHERE code = {{code}} AND sec_type = 'index' AND date = {{date}}
        UNION ALL
        SELECT 2, similar_2nd_code_by_sharing_weights,
               similar_2nd_code_sharing_weight_pct
          FROM stats.sec_similars
         WHERE code = {{code}} AND sec_type = 'index' AND date = {{date}}
        UNION ALL
        SELECT 3, similar_3rd_code_by_sharing_weights,
               similar_3rd_code_sharing_weight_pct
          FROM stats.sec_similars
         WHERE code = {{code}} AND sec_type = 'index' AND date = {{date}}
        UNION ALL
        SELECT 4, similar_4th_code_by_sharing_weights,
               similar_4th_code_sharing_weight_pct
          FROM stats.sec_similars
         WHERE code = {{code}} AND sec_type = 'index' AND date = {{date}}
        UNION ALL
        SELECT 5, similar_5th_code_by_sharing_weights,
               similar_5th_code_sharing_weight_pct
          FROM stats.sec_similars
         WHERE code = {{code}} AND sec_type = 'index' AND date = {{date}}
      ) u
      LEFT JOIN LATERAL (
        SELECT name
          FROM stats.sec_classification sc
         WHERE sc.code = u.code AND sc.type = 'index'
         LIMIT 1
      ) sc ON true
     WHERE u.code IS NOT NULL AND u.code <> ''
     ORDER BY u.rank
