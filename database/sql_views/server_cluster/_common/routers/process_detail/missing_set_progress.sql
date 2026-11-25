-- The generic missing-set oracle (control unification plan Phase 3's
-- derived progress): the identity (source) table vs the result table
-- on ONE key column, at the latest date present in the identity table
-- (or the given one) — expected minus missing, ONE round trip. Served
-- by the process-detail endpoint for services whose service_config
-- ``progress`` block names the table pair; the plan's rule: the query
-- lives HERE, the endpoint and (future) child loops render it.
-- args:
--   identity_table — fragment: the source identity table (schema-qualified)
--   result_table   — fragment: the result table (schema-qualified)
--   key_col        — fragment: the shared key column (code / contract_code)
--   key_date_col   — fragment: the shared date column (date)
--   date           — value date|null: the target date; NULL picks the
--                    identity table's latest date
WITH target AS (
    SELECT COALESCE({{date}}::date,
                    (SELECT MAX({{key_date_col}}) FROM {{identity_table}})) AS d
),
expected AS (
    SELECT DISTINCT {{key_col}} AS key
    FROM {{identity_table}} ident, target
    WHERE ident.{{key_date_col}} = target.d
),
done AS (
    SELECT DISTINCT {{key_col}} AS key
    FROM {{result_table}} res, target
    WHERE res.{{key_date_col}} = target.d
)
SELECT target.d AS trade_date,
       (SELECT COUNT(*) FROM expected) AS expected_total,
       (SELECT COUNT(*) FROM (
            SELECT key FROM expected
            EXCEPT
            SELECT key FROM done
        ) missing) AS missing
FROM target
