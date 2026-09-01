# download, run on every biz date 19:00 (A-share trading day AND hour >= 19).
# Set FORCE_DOWNLOADS=1 to bypass the guard for manual/test runs.
_is_biz_date=$(python -c "from _common._holidays_and_weekdays import is_trading_day; from datetime import date; print(int(is_trading_day(date.today())))")
_cur_hm=$(date +%H%M)
if [ "${FORCE_DOWNLOADS:-0}" = "1" ] || { [ "$_is_biz_date" = "1" ] && [ "$_cur_hm" -ge 1900 ]; }; then
for m in \
  downloads.stock.szse.trend \
  downloads.etf.szse.trend \
  downloads.index.szse.trend \
  downloads.index.sse.trend \
  downloads.stock.sse.trend \
  downloads.etf.sse.trend \
  downloads.margin.szse \
  downloads.margin.sse \
  downloads.bond.shibor \
  downloads.bond.chinabond \
  downloads.macro.pboc.repo_news \
  downloads.options.sse.price \
  downloads.options.szse.trend \
  downloads.options.cffex.trend \
  downloads.macro.pboc.lpr_news \
  downloads.macro.zhihu.news \
  downloads.macro.gov.news
do
  python -m "$m"
done

# download, run on every biz date 19:00 (cont.)
for m in \
  downloads.index.csindex.quote
do 
  python -m "$m"
done

# build combined CSVs
# builds.stock includes tech_stats (MA/EMA) as an internal final step.
# builds.index runs three sequential phases — composition (CSI+SZSE) →
# baseline (CSIndex daily) → exts (stats.index_exts + ETF/exchange trading
# amt + sec similars). Composition must run before baseline; exts must run
# after baseline (exchange_trading_amt is driven by index_basic_stats).
# builds.index's exts phase (stats.index_exts.total_etf_trading_amount)
# feeds sec_alloc_perf_attribution.code_etf_trading_amount, which
# analyze.industry_sentiments' etf_contribution step aggregates into
# analysis.industry_etf_contribution (Industry Sentiments ETF chart).
# builds.industry (stats.industry_basic_stats) must run AFTER builds.index —
# it aggregates index baseline OHLC across member indices per industry.
for m in \
  builds.stock \
  builds.etf \
  builds.index \
  builds.industry \
  builds.bond \
  builds.options \
  builds.futures
do
  python -m "$m"
done

# analyze, run daily. The industry baseline now lives in
# stats.industry_basic_stats (built by builds.industry above);
# industry_correlations + sec_alloc_perf_attribution + industry_attributions
# + industry_etf_contribution are internal steps of industry_sentiments (run
# automatically reading from the baseline table, reusing the same DB
# connection; sec_alloc_perf_attribution is the producer that the
# attributions + etf_contribution aggregations read from). mov_ave_rsi is
# now an internal step of mov_ave_spread (runs automatically after the
# detail + peaks_and_floors tables are repopulated, reusing the same DB
# connection and source price DataFrame). analysis_forecasts reads
# analysis.mov_ave_rsi + analysis.mov_ave_spreads_detail (mov_ave_spread
# above) + stats.*_tech_stats, so it must run after mov_ave_spread; it is
# incremental at completed-month granularity (no-ops until a new month
# closes).
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

# optional to run on daily
if [ "${FORCE_DOWNLOADS:-0}" = "1" ] || { [ "$_is_biz_date" = "1" ] && [ "$_cur_hm" -ge 1900 ]; }; then
for m in \
  analyze.recurring_cycles
do
  python -m "$m"
done

for m in \
  analyze.analysis_forecasts
do
  python -m "$m"
done
fi

# on monthly start date
python -m downloads.etf.sse.composition
python -m downloads.etf.szse.composition
python -m downloads.index.csindex.composition
python -m downloads.index.szse.composition
python -m downloads.etf.csindex.linked_etf
python -m downloads.macro.pboc.stats

# run quarterly
python -m downloads.stock.sse.dividend
python -m downloads.stock.szse.dividend
python -m downloads.etf.szse.archive reports
python -m builds.stock.dividends

# download, run once
python -m downloads.stock.szse.archive
python -m downloads.etf.szse.archive
python -m downloads.index.szse.archive
python -m downloads.stock.sse.archive
python -m downloads.index.cnindex.archive
python -m downloads.futures.cffex.archive

# build, run once
python -m builds.classification
python -m builds.sec_info

# always run
python -m downloads.stream.sse.price
python -m downloads.stream.szse.price
python -m downloads.stream.csindex.price
python -m downloads.stream.cnindex.price

# for strategy — discover all available secs in analysis.mov_ave_spreads_detail,
# backtest them, then compute internal risk metrics for every run.
python -m strategy.singleton_trading

cd data_viz && npm run dev