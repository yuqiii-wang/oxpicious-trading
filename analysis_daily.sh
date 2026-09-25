# analyze, run on every biz date 19:00 (A-share trading day AND hour >= 19),
# after downloads_and_builds_daily.sh. Set FORCE_DOWNLOADS=1 to bypass the
# guard for manual/test runs.
_is_biz_date=$(python -c "from _common._holidays_and_weekdays import is_trading_day; from datetime import date; print(int(is_trading_day(date.today())))")
_cur_hm=$(date +%H%M)
if [ "${FORCE_DOWNLOADS:-0}" = "1" ] || { [ "$_is_biz_date" = "1" ] && [ "$_cur_hm" -ge 1900 ]; }; then
# analyze, run daily. The industry baseline now lives in
# stats.industry_basic_stats (built by builds.industry in
# downloads_and_builds_daily.sh);
# industry_correlations + industry_attributions + industry_etf_contribution
# are internal steps of industry_sentiments (run automatically reading from
# the baseline table, reusing the same DB connection; stats.cross_stats is
# the producer (builds.cross_stats above) that the attributions +
# etf_contribution aggregations read from). mov_ave_rsi is
# now an internal step of mov_ave_spread (runs automatically after the
# detail + peaks_and_floors tables are repopulated, reusing the same DB
# connection and source price DataFrame). analysis_forecasts reads
# analysis.mov_ave_rsi + analysis.mov_ave_spreads_detail (mov_ave_spread
# above) + stats.*_tech_stats, so it must run after mov_ave_spread; it is
# incremental at completed-month granularity (no-ops until a new month
# closes). analysis_signals reads the forecast buckets (the plain
# forecast-results rule: mixed-row mean reversal > 1% + reverse P > 1%)
# + the same mov_ave inputs, so it must run after analysis_forecasts; it
# is also incremental at month granularity. analysis_composites reads
# stats.industry_basic_stats + stats.index_basic_stats (builds.industry /
# builds.index in downloads_and_builds_daily.sh), so it runs after those;
# incremental at window-end granularity (opposite industry correlations by
# benchmark offset).
for m in \
  analyze.industry_sentiments \
  analyze.mov_ave_spread \
  analyze.pe_and_dividends \
  analyze.margins \
  analyze.futures \
  analyze.options
do
  python -m "$m"
done
fi
