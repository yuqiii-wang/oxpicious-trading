-- Intraday stream progress for ONE asset stream — the completeness
-- counts the SSE price poller's is_intraday_complete /
-- get_intraday_progress need, in ONE round trip (identity rows vs the
-- intraday bar table at the close window; moved here 2026-11-21 from
-- the poller's inline SQL so the poller loop AND the area server's
-- process-detail endpoint render the SAME query — the control
-- unification plan's derived-progress rule: the SQL lives in one
-- place, table names ride the args).
-- args:
--   identity_table — fragment: the asset's identity table (schema-qualified)
--   intraday_table — fragment: the asset's intraday bar table (schema-qualified)
--   code_col       — fragment: the code column ('code' on stock/etf/index
--                    tables, 'contract_code' on the options tables)
--   exchange_col   — fragment: the bar table's exchange column reference
--                    ('exchange') or the literal NULL when the table has
--                    no exchange column (index/option tables)
--   exchange       — value text|null: the canonical exchange code ('SS');
--                    ignored when exchange_col is NULL
--   close_time     — value time: the close window (the poller's CLOSE_TIME)
--   trade_date     — value date|null: the target date; NULL picks the
--                    identity table's latest date (the endpoint side, which
--                    does not know the poller's current trade date)
WITH target AS (
    SELECT COALESCE({{trade_date}}::date,
                    (SELECT MAX(date) FROM {{identity_table}})) AS d
),
identity AS (
    SELECT COUNT(*)                AS n_identity,
           COUNT(DISTINCT {{code_col}}) AS n_total_codes
    FROM {{identity_table}} ident, target
    WHERE ident.date = target.d
),
bars AS (
    SELECT MAX(time)               AS max_time
    FROM {{intraday_table}} bars, target
    WHERE bars.date = target.d
),
closed AS (
    SELECT COUNT(DISTINCT {{code_col}}) AS n_codes_done
    FROM {{intraday_table}} bars, target
    WHERE bars.date = target.d
      AND bars.time = {{close_time}}::time
      AND ({{exchange_col}} IS NULL
           OR {{exchange_col}} = {{exchange}}::text)
)
SELECT target.d                         AS trade_date,
       identity.n_identity              AS n_identity,
       identity.n_total_codes           AS n_total_codes,
       bars.max_time                    AS max_time,
       closed.n_codes_done              AS n_codes_done,
       (bars.max_time IS NOT NULL
        AND bars.max_time >= {{close_time}}::time) AS close_reached
FROM target CROSS JOIN identity CROSS JOIN bars CROSS JOIN closed
