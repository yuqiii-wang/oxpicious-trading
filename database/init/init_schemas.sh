#!/bin/bash
set -e

echo "Applying schema files to 'oxpicious-stats' database..."
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/01_debt_baseline.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/02_etf_margin.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/03_sec_composition.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/04_options_quote.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/05_index_baseline.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/99_reconstruct_views.sql

echo "Schema applied successfully!"
