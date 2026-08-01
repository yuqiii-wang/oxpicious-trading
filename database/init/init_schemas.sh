#!/bin/bash
set -e

echo "Applying schema files to 'oxpicious-stats' database..."

# stats schema (existing tables: debt, etf, sec, options, index baselines + views)
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/01_debt_baseline.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/02_etf_margin.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/03_sec_composition.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/04_options_quote.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/05_index_baseline.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/08_index_exts.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/99_reconstruct_views.sql

# analysis schema (new: analysis_identity registry + per-analysis result tables)
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/analysis/00_init.sql

echo "Schema applied successfully!"
