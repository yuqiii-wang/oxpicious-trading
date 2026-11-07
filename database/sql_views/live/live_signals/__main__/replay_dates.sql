-- live_signals range replay (--date-from/--date-to): the trading dates of
-- [date_from, date_to] that have SOURCE data for one sec_type — the union
-- of the sec_type's intraday 5-min bars and its daily baseline closes
-- (code narrows to one ticker in single-code mode; NULL = every code).
-- Dates without any bar/close are skipped: the as-of evaluation would
-- produce nothing for them.
-- args:
--   intraday_table: fragment — the sec_type's intraday table (INTRADAY_TABLES[st])
--   daily_table: fragment — the sec_type's daily table (DAILY_TABLES[st])
--   date_from: value date — replay range start
--   date_to: value date — replay range end (inclusive)
--   code: value text or None — single-code narrowing (NULL = every code)

            SELECT DISTINCT date FROM (
                SELECT date FROM {{intraday_table}}
                WHERE date BETWEEN {{date_from}} AND {{date_to}}
                  AND ({{code}}::text IS NULL OR code = {{code}})
                UNION
                SELECT date FROM {{daily_table}}
                WHERE date BETWEEN {{date_from}} AND {{date_to}}
                  AND close IS NOT NULL
                  AND ({{code}}::text IS NULL OR code = {{code}})
            ) d
