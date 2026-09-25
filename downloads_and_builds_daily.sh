# download + build, run on every biz date 19:00 (A-share trading day AND hour >= 19).
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
  downloads.macro.gov.news \
  downloads.macro.ai_daily
do
  python -m "$m"
done

# The daily AI market summary is ARTIFACT-ONLY (temps/ai_daily/*.json):
# the downloader never touches the DB. builds.text below (source
# ai_daily) loads the artifacts into the text.* tables the UI reads —
# answers + reference hits into text.news, the Q&A into text.llm_qa
# (+ llm_qa_refs / news-group provenance).

# download, run on every biz date 19:00 (cont.)
for m in \
  downloads.index.csindex.quote \
  downloads.index.cnindex.archive
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
# The stats.sec_board_map board tag slices are refreshed INSIDE each
# build's own pipeline (no standalone build): builds.stock writes the
# stock rows, builds.etf the ETF mixes, builds.index the index mixes —
# each reads the data its own run just wrote.
# builds.market_regimes reads the sec_types' basic_stats close + amount
# (wholesale per-scope rebuild, no incremental mode) — it must stay in
# this loop, AFTER the basic_stats builds and BEFORE the analyze tier
# (analysis_forecasts buckets + live.live_signals breaches join the
# daily states; a lagging table silently defaults them to 'calm').
for m in \
  builds.stock \
  builds.etf \
  builds.index \
  builds.industry \
  builds.cross_stats \
  builds.market_regimes \
  builds.bond \
  builds.options \
  builds.futures \
  builds.text
do
  python -m "$m"
done
fi
