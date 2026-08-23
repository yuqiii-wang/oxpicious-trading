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
  PGPASSWORD="$REPL_PASSWORD" pg_basebackup \
    -h "$PRIMARY_HOST" -p "$PRIMARY_PORT" -U "$REPL_USER" \
    -D "$PGDATA" -Fp -Xs -P -R \
    -c fast
  chmod 700 "$PGDATA"
fi

# Ensure hot standby
echo "hot_standby = on" >> "$PGDATA/postgresql.conf"

exec docker-entrypoint.sh postgres
