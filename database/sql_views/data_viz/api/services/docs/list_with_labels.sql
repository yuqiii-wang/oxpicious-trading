-- The static.docs registry WITH the run-matching label arrays
-- (bound_labels / optional_labels, contract:
--  database/sql/static/docs/00_schema.sql) — the summary bridge's doc
-- matching input. Not exposed through /api/docs.
-- args: (none)
SELECT doc_id, title, intro, series, notes,
              bound_labels, optional_labels
         FROM static.docs
        ORDER BY doc_id
