-- pe_and_dividends — distinct trading dates from the sec_type's
-- identity table (the date axis for trailing-12m DPS compute).
-- args:
--   identity_table: fragment — the sec_type's identity table
--       name (SEC_TYPE_IDENTITY_TABLE)
SELECT DISTINCT date FROM {{identity_table}} ORDER BY date ASC