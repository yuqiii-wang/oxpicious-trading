#!/usr/bin/env bash
set -euo pipefail

# Backup Dockerized PostgreSQL from this repository's database/docker-compose.yml.
# Usage:
#   BACKUP_DIR=./backups POSTGRES_DB=postgres ./backup_postgres.sh
#   BACKUP_DIR="D:/oxpicious-stats-db-backup" POSTGRES_DB=oxpicious-stats ./backup_postgres.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.yml"
SERVICE_NAME="db"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-oxpicious-stats}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP="$(date +'%Y%m%d_%H%M%S')"

if [[ "$POSTGRES_DB" == "all" || "$POSTGRES_DB" == "" ]]; then
  echo "ERROR: Per-table backup requires a specific database. Set POSTGRES_DB to the target database."
  exit 1
fi

mkdir -p "$BACKUP_DIR"

echo "Ensuring database service '$SERVICE_NAME' is running..."
docker compose -f "$COMPOSE_FILE" up -d "$SERVICE_NAME"

echo "Waiting for PostgreSQL to become ready..."
docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE_NAME" pg_isready -U "$POSTGRES_USER"

BACKUP_SUBDIR="$BACKUP_DIR/$POSTGRES_DB/$TIMESTAMP"
mkdir -p "$BACKUP_SUBDIR"

TABLE_LIST=$(docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE_NAME" \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At \
  -c "SELECT quote_ident(schemaname) || '.' || quote_ident(tablename) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY schemaname, tablename;")

if [[ -z "$TABLE_LIST" ]]; then
  echo "No user tables found in database '$POSTGRES_DB'."
  exit 1
fi

echo "Backing up tables from database '$POSTGRES_DB' into '$BACKUP_SUBDIR'..."

while IFS= read -r table; do
  if [[ -z "$table" ]]; then
    continue
  fi
  safe_name=$(printf '%s' "$table" | sed 's/[\/ .]/_/g')
  backup_file="$BACKUP_SUBDIR/${safe_name}.sql.gz"
  echo "- Dumping table $table -> $backup_file"
  docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE_NAME" \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t "$table" | gzip > "$backup_file"
done <<< "$TABLE_LIST"

echo "Per-table backup completed: $BACKUP_SUBDIR"
