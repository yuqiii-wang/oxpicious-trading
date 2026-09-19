# download, run on every biz date 19:00 (A-share trading day AND hour >= 19).
# Set FORCE_DOWNLOADS=1 to bypass the guard for manual/test runs.
main() {

# optional to run on daily
if [ "${FORCE_DOWNLOADS:-0}" = "1" ] || { [ "$_is_biz_date" = "1" ] && [ "$_cur_hm" -ge 1900 ]; }; then
for m in \
  builds.market_hypes \
  analyze.analysis_forecasts \
  analyze.analysis_signals \
  analyze.analysis_composites
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

# for strategy — discover all available secs in analysis.mov_ave_spreads_detail,
# backtest them, then compute internal risk metrics for every run.
python -m strategy.singleton_trading

cd data_viz && npm run dev
}

main "$@"