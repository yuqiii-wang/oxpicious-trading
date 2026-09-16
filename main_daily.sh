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
  downloads.macro.gov.news \
  downloads.macro.ai_daily
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
# feeds stats.cross_stats' code_etf_trading_amount, which
# analyze.industry_sentiments' etf_contribution step aggregates into
# analysis.industry_etf_contribution (Industry Sentiments ETF chart).
# builds.industry (stats.industry_basic_stats) must run AFTER builds.index —
# it aggregates index baseline OHLC across member indices per industry.
# builds.cross_stats (stats.cross_stats pair + industry grain) must run
# AFTER builds.index (composition shared weights + index_exts ETF amounts)
# and builds.stock (stock liquidity feeds the industry-grain trading-amount
# split); it is the producer that analyze.industry_sentiments' attributions
# + etf_contribution aggregations read from — its incremental run here makes
# the analyze step's internal producer a no-op.
# builds.text loads the downloaded news text (downloads.macro.*.news above)
# into text.news + text.news_keywords — idempotent upserts, so it is safe
# on every run even before the first news download.
for m in \
  builds.stock \
  builds.etf \
  builds.index \
  builds.industry \
  builds.cross_stats \
  builds.bond \
  builds.options \
  builds.futures \
  builds.text
do
  python -m "$m"
done

# analyze, run daily. The industry baseline now lives in
# stats.industry_basic_stats (built by builds.industry above);
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
# closes). analysis_signals reads the forecast buckets (QRp_P90 gate) +
# the same mov_ave inputs, so it must run after analysis_forecasts; it is
# also incremental at month granularity. analysis_composites reads
# stats.industry_basic_stats + stats.index_basic_stats (builds.industry /
# builds.index above), so it runs after those; incremental at window-end
# granularity (opposite industry correlations by benchmark offset).
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
