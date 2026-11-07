# database/sql_writes — SQL write action files

Write-side implementations live HERE, not inline in service code. Every
custom parameterized write the production code issues (UI actions, deep
agent persistence, KG chain cache, live pipelines) executes SQL rendered
from an action file in this tree; the code keeps only the arg values and
row mapping. The row-dict writes that go through the `_common.db_commons`
bulk helpers (`bulk_upsert*`, `copy_*`, `chunked_purge*`) are helper
machinery — their SQL is the helpers' own and stays there, as does the
plain-SQL study rung (`temp_scripts/` one-offs).

## Layout — mirror the executing code

```
database/sql_writes/<app>/<layer>/<table>/<action>.sql
```

The app/layer prefix mirrors the executing code's location, so a path
names its usage, not just its table:

- TypeScript (data_viz API): the executing code's dir under `data_viz/`
  — `data_viz/api/secret_config/insert.sql` executes from
  `data_viz/api/services/secret-config.service.ts`.
- Python: the executing module's package path + file stem —
  `llm_agents/deep_agents/planning/store/deep_agent_states/tick_settle.sql`
  executes from `llm_agents/deep_agents/planning/store.py`,
  `live/_base/live_identity/upsert.sql` from `live/_base.py`.

Two actions may carry the SAME statement against the same table when
they execute from different apps (e.g. the UI's thread delete and the
deep agent's purge twin each mirror their own executor) — that is the
convention working, not duplication to refactor: each path names its
usage, and each app's loader stays within its own tree.

## Template format

A `.sql` file is ONE statement with plain `$n` positional placeholders
and a leading header comment documenting every parameter:

```sql
-- SQL write action — llm.deep_agent_states UPDATE (settle a tick).
-- Executed by llm_agents/deep_agents/planning/store.py (PlanStore.tick_step)
--
-- Params (asyncpg $n, in order):
--   $1 state_id           BIGINT — the row settling
--   $2 status             TEXT — 'completed' | 'failed'
--   ...
UPDATE llm.deep_agent_states SET status = $2, ...
WHERE state_id = $1
```

`$n` placeholders are the common denominator of the executors: the
data_viz API's node-postgres and Python's asyncpg both bind them
positionally, so ONE file serves both. Casts stay in the file
(`$1::text[]`, `$3::jsonb`) — the SQL site owns the types. Placeholders
must be dense `$1..$n` with no gaps (both loaders' contracts).

## Loaders

- TypeScript: `loadSqlWrite(app, layer, table, action)` in
  `data_viz/api/services/sql-writes.service.ts` — returns the raw text;
  the service binds the params and routes to the primary pool.
- Python: `_common/sql_writes.py` —
  - `render_sql_write(name, /, *params)` for one-statement calls
    (`execute` / `fetchval` / `fetchrow`): binds positionally and
    verifies the action's highest `$n` equals the arg count (gap-free),
    so a file/call-site drift fails named before any DB round trip.
    Feed the returned `(sql, params)` pair to the existing db_commons
    write path: `write = render_sql_write("live/_base/live_identity/upsert", …)`
    then `await conn.execute(write.sql, *write.params)`.
  - `load_sql_write(name)` for `executemany` batches (raw text; asyncpg
    validates each row itself).

Both load fresh on mtime change — an edited action file applies without
an API/daemon restart (the same dev contract as the static-config
trees). Names are validated (`[A-Za-z0-9_./-]`), so a traversal or odd
segment never reaches the filesystem.

## What does NOT go here

- One-time SQL (migration ALTERs, backfills, renames, legacy-row
  DELETEs): runs ONCE from `temp_scripts/`, never parked in this tree —
  fold its effect into the original DDL under `database/sql/`.
- Reads: `database/sql_views/` (see that tree's README).
- Helper-generated bulk writes: `_common/db_commons` owns their SQL.
- Seed INSERT files (initial population / source of record):
  `database/sql/` in their re-appliable shape.
