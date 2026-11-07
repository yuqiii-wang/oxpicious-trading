-- AI ask-history detail's derived keyword rows (codes, product, in-plot
-- items, series names — llm.llm_qa_keywords_by_ask).
-- Serves GET /api/ai/ask-qa (the keywords block).
-- args:
--   ask_id: value bigint — the ask whose keywords to list
SELECT keyword, kind
       FROM llm.llm_qa_keywords_by_ask
      WHERE ask_id = {{ask_id}}
      ORDER BY kind, keyword
