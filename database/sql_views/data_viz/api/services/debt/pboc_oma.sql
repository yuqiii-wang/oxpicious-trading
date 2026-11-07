-- PBoC Open Market Announcements (stats.pboc_oma) — high-level policy
-- notices, NOT daily transaction announcements. Small table (~20 rows),
-- always returned in full (no date filter). Serves GET /api/debt/pboc-oma.
-- args: (none)

    SELECT date, title, type, content, detail_url, keywords,
           serial_year, serial_no, detail_slug
    FROM stats.pboc_oma
    ORDER BY date ASC, title ASC
