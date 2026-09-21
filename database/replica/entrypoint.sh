#!/bin/bash
# Self-seeding read-only streaming replica entrypoint.
set -e

PGDATA=/var/lib/postgresql/data
PRIMARY_HOST=${PRIMARY_HOST:-db}
PRIMARY_PORT=${PRIMARY_PORT:-5432}
REPL_USER=${REPL_USER:-postgres}
REPL_PASSWORD=${REPL_PASSWORD:-postgres}

if [ ! -f "$PGDATA/PG_VERSION" ]; then
  echo "Empty data dir, running pg_basebackup from $PRIMARY_HOST..."
  rm -rf "$PGDATA"/*
  # A physical slot pins WAL on the primary so it is never recycled while
  # this standby still needs it (wal_keep_size=0 otherwise loses segments
  # and the standby can never catch up). An inactive leftover slot from a
  # previous re-seed is dropped first so -C cannot fail on it.
  PGPASSWORD="$REPL_PASSWORD" psql -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" \
    -U "$REPL_USER" -d postgres -q \
    -c "SELECT pg_drop_replication_slot('replica_slot')
        FROM pg_replication_slots
        WHERE slot_name = 'replica_slot' AND NOT active"
  PGPASSWORD="$REPL_PASSWORD" pg_basebackup \
    -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$REPL_USER" \
    -D "$PGDATA" -Fp -Xs -P -R -C -S replica_slot \
    -c fast
  chmod 700 "$PGDATA"
fi

# Ensure hot standby
echo "hot_standby = on" >> "$PGDATA/postgresql.conf"

# listen_addresses: the primary gets '*' from its container CMD flag, which
# is NOT part of the basebackup-copied config (initdb default is localhost).
# Without this, host port-forward connections are reset (ECONNRESET).
exec docker-entrypoint.sh postgres -c listen_addresses='*'
