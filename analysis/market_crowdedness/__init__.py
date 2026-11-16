"""analysis.market_crowdedness — market crowdedness curves.

Populates the per-(code, sec_type, date) crowdedness table
analysis.market_crowdedness (see database/sql/analysis/
29_market_crowdedness.sql) for the full active classified index universe
and composite industries, and serves the Commons "Market Crowdedness"
page (/analysis/commons/market-crowdedness) through
/api/analysis/market-crowdedness/*.

crowdedness_score (v2) = renormalized blend 0.30·concentration_pct +
0.30·flow_pct + 0.25·comovement_pct + 0.15·valuation_pct — each pillar
read as its percentile vs the pool's own trailing-250-day history,
computed per date from the pool's DYNAMIC hot set = the top 10% of the
day's active member stocks by trading amount, floored at 5 names (pool:
latest sec_composition snapshot for an index; the DISTINCT member-index
stock union for an industry); see the __main__ module docstring for the
full definition.

Run via ``python -m analysis.market_crowdedness``.

Layout (the 2026-11-14 split of the former monolithic __main__.py —
pure code motion, every name unchanged):
  config.py   — the four output tables, the metric knobs and the
                output column contracts (pure literals)
  fetch.py    — the raw-input SQL reads (sql_views: .../fetch/)
  compute/    — metrics / finalize / pinned / pool_batch / driver —
                the chunked compute core
  pipeline.py — the MarketCrowdednessAnalysis run orchestration +
                incremental writes (sql_views: .../pipeline/)
"""
