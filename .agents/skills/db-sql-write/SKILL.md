---
name: db-sql-write
description: How to write data to the PostgreSQL DB in the oxpicious-trading repo — match the `_common/db_commons` helper to the job's size, study by reading fewer than 10 sampled code examples instead of sweeping the codebase, and never pull bulk-write machinery (COPY / chunked writers) into a simple study script. Use for ANY DB write, insert, upsert, purge, bulk load, DDL apply, or temp_scripts study script that touches the database — even when the user just says "save the results" or "write it to the DB".
---

# DB SQL writes (`_common/db_commons`)

## Study write scope (MANDATORY)

To do study in analysis and strategy, only check by industries and
indices (no etf and stocks unless explicitly said), only these 10 indices
(000001, 931743, 931160, 931071, 000015, 930708, 931994, 931594, 000683,
000991), and only write the 10 indices to DB table, unless otherwise
explicitly instructed then run all indices.

## Study by sampling — the <10-file budget

Before writing any DB write code, do NOT read all of `_common/db_commons/`
and do NOT grep the repo for "how others write". Budget the whole study
phase at fewer than 10 sampled code reads:

1. `_common/db_commons/__init__.py` — one read; it lists every public
   helper with a comment naming the submodule behind each export.
2. 2–3 usage examples from the curated list below, matched to the job's
   shape.
3. Only if the signature is still unclear, the target helper's docstring
   (open that line range, not the whole module).

Curated examples — one per job shape, verified live in the repo:

| Job shape | Read |
| --- | --- |
| One-off study / DDL-apply script | `temp_scripts/apply_confidence_col.py` |
| Incremental pipeline upsert + force-mode purge | `analyze/mov_ave_spread/rsi.py` |
| Build-pipeline copy-or-upsert split | `builds/stock/pipeline/writer.py` |
| Force rebuild via chunked CSV COPY | `builds/index/daily/db_insert.py` |

## Match the helper to the job size — do not rush to bulk writes

The helpers ladder up from plain SQL to chunked COPY. Pick by row count
and conflict exposure, and stop at the first rung that fits:

| Rung | When | Helper |
| --- | --- | --- |
| Plain SQL | a handful of rows; one-off study patch | `get_db_connection()` + cursor `INSERT`/`UPDATE` |
| Upsert | idempotent pipeline write, any size | `bulk_upsert(_async)(conn, table, rows, key_columns)` |
| Daily incremental | rows past MAX(date) are new | `copy_or_upsert_split_async` (COPY fast path + upsert fallback) |
| Force rebuild | scope truncated/purged, conflict-free | `copy_frame_chunked_async` / `csv_copy_frame_chunked_async` |
| Big deletes | purging a scope | `chunked_purge_async` (`chunked_dml_by_key_async` for grouped statements) |

A simple study — a throwaway script answering a question, a small table
patch, a backfill of a few thousand rows — does NOT need the COPY or
chunked machinery. That machinery exists to keep the replication slot's
WAL bounded on multi-100K-row loads; a study script writing 100 rows
gains nothing from it and pays in complexity and failure modes. Rule of
thumb: under ~100K rows (one commit chunk) any correct helper is fine —
pick the simplest (its single commit chunk pays only one 0.1s
`DEFAULT_CHUNK_PAUSE_S` cool-down — leave it on). Bulk COPY earns its
keep at millions of rows or daily-repeated loads.

```py
# Study-script rung: sync psycopg, a few rows — nothing fancier.
from _common.db_commons import get_db_connection, bulk_upsert

conn = get_db_connection()
with conn.cursor() as cur:
    cur.execute("UPDATE stats.my_table SET note = %s WHERE code = %s", (note, code))
# or, for row dicts:
bulk_upsert(conn, "stats.my_table", rows, key_columns=["date", "code"])
```

## Non-negotiables at every size

- Connections only from `_common.db_commons` — `get_db_connection()`,
  `get_db_connection_async()`, the pools, and the `get_read_db_connection*`
  replica variants all read `database/.env`. Never hand-build conn params.
  Writes go to the primary; heavy reads to the replica — a read connection
  is `SET default_transaction_read_only = on`, so never aim a write at it.
- COPY has no ON CONFLICT. Use it only into a truncated or purged
  (guaranteed conflict-free) scope; when conflicts are possible, upsert.
- Do not reimplement or opt out of the bulk-write rule (chunk by partition
  key → one COMMIT per chunk → between-chunk throttle). The throttle is
  the helpers' `_throttle_between_chunks(_async)`: a fixed cool-down
  `DEFAULT_CHUNK_PAUSE_S = 0.1s` before every chunk (breaks up sustained
  fsync-bound write bursts — the reactive lag cap alone only engages once
  the standby is 4 GB behind), then the replica-lag wait. Defaults
  (`partition_key="auto"`, `chunk_pause_s=0.1`, `max_replica_lag_mb=4096`)
  are the right call; passing `None` re-creates the max-duty-cycle disk
  saturation and the ~29 GB retained-WAL freeze the rule exists to
  prevent. The pause is cheap by design: a 70-chunk force rebuild pays
  ~7s extra, a single-chunk daily write pays 0.1s — never "optimize" it
  away.
- After a multi-100K-row write, run `analyze_tables_async(conn, [table])` —
  stale planner statistics turn value-blind prepared-statement readers
  into seq scans (the 2026-09-27 30s+ API-read regression).
- Schema/DDL changes are SQL first under `database/sql/`, applied from a
  `temp_scripts/` script. Run Python through WSL — see the
  `oxpicious-dev-conventions` skill, which this skill specializes for
  writes.

Sync vs async: pipelines use async (asyncpg). A simple study script can
use the sync psycopg API — simpler, no event loop.
