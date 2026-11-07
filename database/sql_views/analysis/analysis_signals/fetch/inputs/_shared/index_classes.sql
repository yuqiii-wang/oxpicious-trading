-- Per-code PRIMARY classification industry (stats.sec_classification
-- .industry_id — the finer L2 id; the curve tier's slope pooling key).
-- Codes without a classification row map to UNCLASSIFIED_CLASS
-- caller-side.
-- args:
--   sec_type: value text — the sec_type label ("index" / "etf" / ...)
--   codes: value text[] — the sorted requested codes
        SELECT code, min(industry_id) AS industry_id
        FROM stats.sec_classification
        WHERE type = {{sec_type}}
          AND code = ANY({{codes}}::text[])
          AND industry_id IS NOT NULL AND industry_id <> ''
        GROUP BY code
