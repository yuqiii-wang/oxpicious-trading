-- Keywords tab — the seeded keyword → category map (static.keyword_maps),
-- category order. The categorical columns ride the DB as static.label_dim
-- SMALLINT codes; the service decodes them at the response boundary.
-- Serves GET /api/config/keyword-maps.
-- args: none
SELECT keyword, category_type, industry_id, category_code,
            parts_of_speech, is_negation, language
     FROM static.keyword_maps
     ORDER BY category_type,
              category_code NULLS LAST,
              industry_id NULLS LAST,
              keyword
