# builds, run on every biz date 19:00 (A-share trading day AND hour >= 19).
# Set FORCE_DOWNLOADS=1 to bypass the guard for manual/test runs.
_is_biz_date=$(python -c "from _common._holidays_and_weekdays import is_trading_day; from datetime import date; print(int(is_trading_day(date.today())))")
_cur_hm=$(date +%H%M)
if [ "${FORCE_DOWNLOADS:-0}" = "1" ] || { [ "$_is_biz_date" = "1" ] && [ "$_cur_hm" -ge 1900 ]; }; then
# build combined CSVs
# builds.stock includes tech_stats (MA/EMA) as an internal final step.
# builds.index runs three sequential phases — composition (CSI+SZSE) →
# baseline (CSIndex daily) → exts (stats.index_exts + ETF/exchange trading
# amt + sec similars). Composition must run before baseline; exts must run
# after baseline (exchange_trading_amt is driven by index_basic_stats).
# builds.index's exts phase (stats.index_exts.total_etf_trading_amount)
# feeds stats.cross_stats' code_etf_trading_amount, which
# analysis.industry_sentiments' etf_contribution step aggregates into
# analysis.industry_etf_contribution (Industry Sentiments ETF chart).
# builds.industry (stats.industry_basic_stats) must run AFTER builds.index —
# it aggregates index baseline OHLC across member indices per industry.
# The stats.sec_board_map board tag slices are refreshed INSIDE each
# build's own pipeline (no standalone build): builds.stock writes the
# stock rows, builds.etf the ETF mixes, builds.index the index mixes —
# each reads the data its own run just wrote.
# builds.market_regimes reads the sec_types' basic_stats close + amount
# (wholesale per-scope rebuild, no incremental mode) — it must stay in
# this loop, AFTER the basic_stats builds and BEFORE the analysis tier
# (analysis_forecasts buckets + live.live_signals breaches join the
# daily states; a lagging table silently defaults them to 'calm').
# builds.etf.feeder_nav (ETF联接 feeder NAV from the huatai-pb fund site,
# is_ohl_estimated rows) runs right after builds.etf: builds.etf --force
# truncates etf_identity CASCADE, wiping feeder rows — this re-loads them
# from the daily trend snapshots + archives.
for m in \
  builds.stock \
  builds.etf \
  builds.etf.feeder_nav \
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