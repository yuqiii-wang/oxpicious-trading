#!/bin/bash
set -e

# FRESH-INIT ONLY: runs once on first container initialization
# (docker-entrypoint-initdb.d, after 00_create_db_and_user.sql) against
# an EMPTY volume. This list is a SUBSET of the full schema tree (stats
# basics + analysis core) — the remaining schemas (analysis_signals /
# live / strategy / text / llm / the rest of stats) are applied per
# tree from the database/sql/<tree>/00_init.sql master lists (each list
# is complete + FK-safe; ONE TABLE = ONE FILE since 2026-11-10).
# NEVER re-run against a populated database: several applied files are
# DROP+CREATE refreshes.

echo "Applying schema files to 'oxpicious-stats' database..."

# shared partition util (public.create_hash_partitions) — stats tables hash-partition
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/00_partition_utils.sql

# stats schema subset: debt family (identity first — satellites FK it)
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/debt_identity.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/debt_omo.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/debt_repo.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/debt_outright_repo.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/debt_mlf.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/debt_shibor.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/debt_treasury.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/debt_lpr.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/pboc_oma.sql
# etf family
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/etf_identity.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/etf_basic_stats.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/etf_tech_stats.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/etf_adjustment.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/etf_liquidity_margin.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/etf_intraday_5min.sql
# sec composition
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/sec_composition.sql
# options quote family
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/options_identity.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/options_terms.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/options_strike.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/options_settlement.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/options_greeks.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/options_volume_oi.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/options_aggregate.sql
# index baselines
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/index_identity.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/index_basic_stats.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/index_valuation.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/index_tech_stats.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/index_intraday_5min.sql
# index extensions
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/index_exts.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/etf_trading_amt.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/exchange_trading_amt.sql
# reconstruct views (over the tables above)
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/stats/99_reconstruct_views.sql

# static label index (the old stats/00_init chained static/label_dim —
# preserved: the typed-column decode vocabulary every tree reads)
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/static/label_dim/label_dim.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/static/label_dim/02_market_state.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/static/label_dim/03_industry_aggregation.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/static/label_dim/03_signal_detection.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/static/label_dim/04_security_identity.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/static/label_dim/05_trading.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/static/label_dim/06_monitoring_infra.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/static/label_dim/07_text_llm.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/static/label_dim/08_keyword_maps.sql

# static news provider prestige (the curated chip ordering for the News page)
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/static/news_prestige/news_prestige.sql
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/static/news_prestige/01_seed_prestige.sql

# analysis schema (identity registry + per-analysis result tables)
psql -U postgres -d "oxpicious-stats" -f /docker-entrypoint-sql.d/analysis/00_init.sql

echo "Schema applied successfully!"
