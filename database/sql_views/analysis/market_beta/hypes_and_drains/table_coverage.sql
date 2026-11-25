-- Hypes & drains step — per-date table coverage probe for the incremental
-- mode decision: an empty table forces a full build (the missing-date
-- detection floors at the table's own MIN(date), which is NULL when
-- empty); a populated table goes through find_missing_dates.
-- args: none
    SELECT COUNT(*) AS n_rows,
           MIN(date) AS min_date,
           MAX(date) AS max_date
    FROM analysis.industry_hypes_and_drains
