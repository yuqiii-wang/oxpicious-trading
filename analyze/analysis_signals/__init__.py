"""Signal strategies + history signals over the RSI percentile
(analyze.analysis_signals).

Since the forecast layer's removal (2026-10-05) the tier computes its
OWN rsi-pct buckets in-engine (analysis.mov_ave_rsi + prices — see
engines/signal_families/mov_rsi/_buckets.py): a bucket (code ×
stat_date snapshot M × rsi pct config, trailing 10y window (M - 10y,
M]) whose blended mixed-row stats pass the plain gate at the per-side
robustness floors IS a signal strategy for that window period — one
analysis_signals.signal_thresholds row (the confidence-scale model:
confidence_base + scale_method/base/factor), plus the trigger days
(each qualifying streak's first day) inside the snapshot's calendar
year as history_signals rows. python -m live.live_signals breaches the active
thresholds intraday (live.live_signals).
"""
