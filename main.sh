# download, run daily
for m in \
  downloads.stock.szse.trend \
  downloads.etf.szse.trend \
  downloads.options.szse.trend \
  downloads.index.szse.trend \
  downloads.stock.sse.trend \
  downloads.etf.sse.trend \
  downloads.margin.szse \
  downloads.margin.sse \
  downloads.bond.shibor \
  downloads.bond.chinabond \
  downloads.macro.pboc.repo_news \
  downloads.options.sse.price \
  downloads.macro.pboc.lpr_news
do
  python -m "$m"
done

# download, run daily
for m in \
  downloads.index.csindex.quote \
  downloads.macro.zhihu.news
do
  python -m "$m"
done

# build combined CSVs
for m in \
  builds.stock \
  builds.stock.tech_stats \
  builds.etf \
  builds.index.composition \
  builds.index.baseline \
  builds.bond \
  builds.options.szse
do
  python -m "$m"
done

# analyze, run daily. industry_correlations + sec_alloc_perf_attribution +
# industry_attributions + industry_etf_contribution are now internal steps of
# industry_sentiments (run automatically after the sentiments table is
# repopulated, reusing the same DB connection; sec_alloc_perf_attribution is
# the producer that the attributions + etf_contribution aggregations read
# from). mov_ave_rsi is now an internal step of mov_ave_spread (runs
# automatically after the detail + peaks_and_floors tables are repopulated,
# reusing the same DB connection and source price DataFrame).
for m in \
  analyze.industry_sentiments \
  analyze.mov_ave_spread
do
  python -m "$m"
done

# on monthly start date
python -m downloads.etf.sse.composition
python -m downloads.etf.szse.composition
python -m downloads.index.csindex.composition
python -m downloads.index.szse.composition
python -m downloads.etf.csindex.linked_etf

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

# build, run once
python -m builds.classification
python -m builds.sec_info

# always run
python -m downloads.stream.sse.price
python -m downloads.stream.szse.price
python -m downloads.stream.csindex.price

# for strategy — discover all available secs in analysis.mov_ave_spreads_detail,
# backtest them, then compute internal risk metrics for every run.
python -m strategy.ma_spread_trading
