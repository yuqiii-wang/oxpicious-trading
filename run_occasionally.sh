# download, run on every biz date 19:00 (A-share trading day AND hour >= 19).
# Set FORCE_DOWNLOADS=1 to bypass the guard for manual/test runs.
main() {

# on monthly start date
python -m downloads.etf.sse.composition
python -m downloads.etf.szse.composition
python -m downloads.index.csindex.composition
python -m downloads.index.szse.composition
python -m downloads.etf.csindex.linked_etf
python -m downloads.macro.pboc.stats

# run semi-annually on 1st Mar and 1st Sep
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

}

main "$@"