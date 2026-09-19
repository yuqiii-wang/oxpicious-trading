#!/bin/bash
set -e

# FRESH-INIT ONLY: runs once on first container initialization
# (docker-entrypoint-initdb.d) against an EMPTY volume. This list is a
# SUBSET of the full schema tree (stats basics + analysis core) — the
# remaining schemas (analysis_forecasts / analysis_signals / live /
# strategy / text / the rest of stats) are applied per-file from
# database/sql/**/00_init.sql master lists. NEVER re-run against a
# populated database: several applied files are DROP+CREATE refreshes.

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
