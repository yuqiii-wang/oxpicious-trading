-- SQL write action — static.secret_config DELETE (UI: Config → Secrets
-- tab, the row's ✕ action, behind a confirm dialog).
-- Executed by data_viz/api/services/secret-config.service.ts through the pg
-- primary pool with $n parameters; convention of this tree (the app/layer
-- prefix mirrors the executing code's dir under data_viz/):
--   database/sql_writes/<app>/<layer>/<table>/<action>.sql
--
-- Params (node-postgres $n, in order):
--   $1 secret_id INTEGER — the row to delete
--
-- The row goes entirely — api_key_enc WITH it (deleting a credential
-- is the explicit way to remove its stored key; the alternative
-- softer path is the is_active retirement or the key-clear tri-state
-- on the UPDATE action). No row matches an unknown id → empty
-- RETURNING (the caller maps it to 404). Returns the deleted row's
-- identity only.
DELETE FROM static.secret_config
 WHERE secret_id = $1::int
RETURNING secret_id;
