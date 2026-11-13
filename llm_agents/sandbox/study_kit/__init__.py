"""study_kit — the finished, often-used query and calculation helpers
of the docker py-sandbox, importable from EVERY sandbox session.

The package ships INSIDE the oxpicious-py-sandbox image (the Dockerfile
copies it to /opt/study_kit and puts it on PYTHONPATH), so a sandbox
script simply does ``import study_kit`` — no per-session copying, no
repo imports. It is plain pandas/numpy/psycopg (the cutepandas stack),
read-only against the database: the container's preset PG* env points
psycopg at the ``readonly_analytics`` role and writes fail server-side.

Layout — one public function per file:

``study_kit.db``
    ``query(sql, params)`` — any SELECT → DataFrame;
    ``fetch_daily_bars(sec_type, code, start, end, adjusted)`` — daily
    OHLCV over the reconstructed views with the precomputed MA/EMA
    columns;
    ``fetch_spread_stats(sec_type, code, start, end, pairs)`` — the
    MATERIALIZED MA-spread trailing stats (gap + trailing-252d
    pct_rank / zscore / verdict) from analysis.mov_ave_spreads_detail
    joined to its same-grain trailing companion
    analysis.mov_ave_spreads_detail_trailing;
    ``fetch_amt_metrics(sec_type, code, start, end)`` — the
    MATERIALIZED trading-amount metrics (amt MAs / Bollinger σ /
    market share / slopes — the 量能 lane) from
    analysis.mov_ave_trading_amt.

``study_kit.studies``
    sma / ema / wilder_rsi / macd / roc / zscore / trailing_pct_rank /
    drawdown / forward_return / forward_blend (the repo's dir_ave
    weights) / ma_alignment / spread_report / kdj (the stochastic:
    K/D/J zones + the K/D cross) / bollinger (mid/upper/lower + pct_b
    + bandwidth) / volume_contrast (the up/down-day 量能对比 the
    tables do not store) — the compute fallbacks for what the tables
    do NOT materialize (custom windows, spans, horizons, thresholds,
    weights).

MATCH FIRST: before writing a new calculation, check whether
``study_kit.db`` already materializes the value or ``study_kit.studies``
already computes it — import and call the existing function as-is;
write new code only for what the kit lacks.

Conventions the kit bakes in (see the backend_tech skills in
llm.ai_prompts): volume means 成交额 ``trading_amount``; percentiles
anchor against the security's OWN trailing history; pull one extra
year of warmup before a studied window when computing rolling stats.
"""
from study_kit import db, studies  # noqa: F401

__all__ = ["db", "studies"]
