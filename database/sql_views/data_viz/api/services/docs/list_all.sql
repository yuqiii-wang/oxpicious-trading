-- The full static.docs UI-description registry (a few hundred text rows),
-- fetched once per session by the UI docs store. Serves GET /api/docs.
-- args: (none)
SELECT doc_id, title, intro, series, notes, ordinal
         FROM static.docs
        ORDER BY doc_id
