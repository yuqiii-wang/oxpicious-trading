"""analysis.hot_stock_momentum — hot stock momentum curves.

Populates the per-(code, sec_type, date) hot-stock momentum table
analysis.hot_stock_momentum (see database/sql/analysis/
30_hot_stock_momentum.sql) for indices and composite industries, and
serves the Commons "Hot Stock Momentum" page
(/analysis/commons/hot-stock-momentum) through
/api/analysis/hot-stock-momentum/*.

IMPLEMENTATION PLACEHOLDER (2026-10-31): the schema is live and the
service is wired (heartbeat, service_config, orchestration step, API,
UI), but the compute pipeline is empty — run() logs and exits without
writing. Fill run() (fetch sources → hot-stock momentum curve → upsert)
when the metric definition lands.

Run via ``python -m analysis.hot_stock_momentum``.
"""
