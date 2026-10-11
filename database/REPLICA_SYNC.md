# Replica sync operations (primary ↔ read replica)

The cluster is a Postgres 17 primary (`oxpicious-db`, port 9876) plus a
physical streaming replica (`oxpicious-db-replica`, port 9877) — see
`docker-compose.yml`. Physical replication ships every WAL record, so
**DDL replicates exactly like data**: `CREATE`/`ALTER`/`DROP`, index
builds, table rewrites — all appear on the replica as soon as replay
catches up. There is no "apply DDL to the replica" step to remember.

What that means in practice: "always in sync" is a monitoring and
convention problem, not a sync mechanism to build. This doc is the
convention.

## What WAL does NOT carry

| Thing | Where it lives | Rule |
|---|---|---|
| Replica server settings | replica `postgresql.conf` | add to `database/replica/entrypoint.sh` — it re-applies them on every (re)start, because `pg_basebackup` overwrites the config on a re-seed |
| `pg_hba.conf` (both nodes) | repo file | bind-mounted from `database/pg_hba.conf` — never edit inside the container |
| Image version | `docker-compose.yml` | both services must pin the SAME `supabase/postgres` tag; a major-version upgrade of the primary invalidates the replica's data dir → re-seed (below) |

Server settings on the primary (`ALTER SYSTEM`, e.g. `max_wal_size`)
stay primary-only; the replica inherits none of them.

## DDL ground rules on the primary

- Always target the primary (9876). The replica sessions are read-only
  (`default_transaction_read_only = on` on every pooled connection) —
  a safety net, not a process.
- Heavy DDL on the big tables (`analysis_forecasts.forecast_results`
  is ~17GB): prefer `CREATE INDEX CONCURRENTLY`; `ADD COLUMN` with
  constants/NULL only (no table rewrite); avoid `VACUUM FULL`/`CLUSTER`
  outside maintenance windows — each emits table-sized WAL that the
  replica must replay.
- Set `SET lock_timeout = '5s'` on interactive DDL sessions so a queued
  lock cannot stall primary writes (a stalled primary stops emitting
  WAL and everything waits on it).
- Expect the replica to fall behind during a DDL burst: the API probe
  (WAL byte backlog, threshold 64MB — `REPLICA_MAX_LAG_BYTES` in
  `data_viz/api/services/db.service.ts`) automatically fails reads over
  to the primary for the catch-up window and returns once drained.
  That is the designed behavior, not an incident.

## Monitoring

`database/monitoring.py` (run from the repo root; exit 0 = healthy,
1 = alert):

```bash
python -m database.monitoring                  # replication state + backlog + slot retention + pg_wal dir size + stream liveness
python -m database.monitoring --schema-drift   # catalog fingerprint: primary vs replica
```

Both run in `scripts/monitoring_occasionally.sh` (first step of
`main_run.sh`, so every daily scheduled pass checks before heavy
writes). Thresholds: backlog alert 64MB (same as the API probe), slot
retained-WAL alert 2GB, pg_wal dir alert 1.5× `max_wal_size` — the dir
legitimately idles under `max_wal_size` (recycled segments are kept for
reuse), so only growth past the recycle horizon means segments are
being pinned (an inactive slot, stuck replay/checkpoint).

`database/db_health.py` is the HISTORY side of the same picture: an
always-on sampler (registered as `database.db_health` in
`_common/service_heartbeat.MONITORED_SERVICES`, so the stream watchdog
restarts it like the pollers) that appends one row per instance per ~60s
to `monitor.db_health` — reachability, connect latency, uptime,
connections, size, cache hit, xact_commit, WAL shipping state, replay
backlog, the instance's pg_wal dir size, and container CPU/memory from
`docker stats`. Unreachable instances are sampled too
(`healthy = false` + a short `error`), so outages leave a trail; the
table keeps 90 days. Started by the watchdog (main_run.sh adopts it),
or directly:

```bash
python -u -m database.db_health        # foreground; --once for a single pass
```

Never use the wall-clock age of `pg_last_xact_replay_timestamp()` as a
lag measure: it grows without bound on an idle primary and once
misrouted every API read to the primary for hours (the 2026-09-27
forecast-table 30s regression). Backlog bytes are the only idle-safe
measure — the same query the API probe uses:

```sql
SELECT pg_wal_lsn_diff(pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn());
```

## Post-DDL gate

After applying schema changes from `database/sql/` to the primary:

```bash
python -m database.monitoring --wait-catchup          # default: <= 1MB within 300s
python -m database.monitoring --schema-drift          # fingerprints must match
```

Optional: with feedback enabled, `backend_xmin` in the primary's
`pg_stat_replication` shows the standby's oldest protected xact once a
replica snapshot exists; `pg_stat_database_conflicts.confl_snapshot`
on the replica should stay flat now that `hot_standby_feedback = on`.

## Re-seed (rarely needed; always schema-current)

If the replica must be rebuilt (version upgrade, corrupted state):

```bash
docker compose -f database/docker-compose.yml restart db-replica
# or wipe the data dir first for a forced pg_basebackup
```

The entrypoint (`database/replica/entrypoint.sh`) drops a stale
`replica_slot`, runs `pg_basebackup -Xs -R -C -S replica_slot`, and
re-applies the replica-only settings (`hot_standby`,
`hot_standby_feedback`). A fresh basebackup is byte-identical to the
primary — DDL included — so re-seeding is also the recovery path if
`--schema-drift` ever reports a mismatch that catch-up doesn't fix.
Verify with `--wait-catchup` and `--schema-drift` before trusting
replica reads again.

## Accepted limitation

Replication is async: a primary crash can lose the last few hundred
milliseconds of commits, including a just-run DDL. Both containers
share one host and one E: disk, so the replica protects against a
volume/vhdx failure, not host loss — async is the right trade here.
Zero-loss would require `synchronous_standby_names` +
`synchronous_commit = on` on the primary, at a per-write round-trip
cost the bulk builds would feel.
