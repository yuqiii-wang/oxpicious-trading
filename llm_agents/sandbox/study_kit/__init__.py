"""study_kit — the finished, often-used query and calculation helpers
of the docker py-sandbox, importable from EVERY sandbox session.

The package ships INSIDE the oxpicious-py-sandbox image (the Dockerfile
copies it to /opt/study_kit and puts it on PYTHONPATH), so a sandbox
script simply does ``import study_kit`` — no per-session copying, no
repo imports. It is plain pandas/numpy/psycopg (the cutepandas stack),
read-only against the database: the container's preset PG* env points
psycopg at the ``readonly_analytics`` role and writes fail server-side.

Modules
-------
``study_kit.db``      — the query layer: ``query(sql, params)`` (any
    SELECT → DataFrame) and ``fetch_daily_bars(sec_type, code, start,
    end)`` (daily OHLCV over the reconstructed views with the
    precomputed MA/EMA columns).
``study_kit.studies`` — the calculations the house studies repeat:
    sma / ema / wilder_rsi / macd / roc / zscore / trailing_pct_rank /
    drawdown / forward_return / forward_blend (the repo's dir_ave
    weights) / ma_alignment / spread_report (value + trailing-window
    percentile + z + verdict per MA spread).

Quick start::

    import study_kit
    df = study_kit.db.fetch_daily_bars("index", "000001",
                                       "2024-01-01", "2026-10-31")
    report = study_kit.studies.spread_report(df["close"], "000001")
    print(report.tail())

Conventions the kit bakes in (see the backend_tech skills in
llm.ai_prompts): volume means 成交额 ``trading_amount``; percentiles
anchor against the security's OWN trailing history; pull one extra
year of warmup before a studied window when computing rolling stats.
"""
from study_kit import db, studies  # noqa: F401

__all__ = ["db", "studies"]
