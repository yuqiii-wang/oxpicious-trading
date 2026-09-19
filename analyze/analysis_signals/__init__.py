"""Signal strategies + history signals over analysis_forecasts
(analyze.analysis_signals).

A forecast bucket (code × stat_month snapshot M × config, trailing
5-year window (M - 5y, M]) whose MIXED forecast_results row passes the
plain gate IS a signal strategy for that forecast period
(analysis_signals.signal_strategies); the bucket's trigger days inside
the snapshot month M are its history signals
(analysis_signals.history_signals). The live tier
(live.live_signals) breaches the strategies' stored bars — it never
re-detects anything.

NO CASE/WHEN anywhere in the emission SQL: the bucket's side is a
STORED column on signal_strategies, and every other conditional lives
in this package's vectorized cudf.pandas engines.
"""
