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
  downloads.index.csindex.quote \
  downloads.bond.shibor \
  downloads.bond.chinabond \
  downloads.macro.pboc.repo_news \
  downloads.options.sse.price \
  downloads.macro.pboc.lpr_news
do
  python -m "$m"
done

# build combined CSVs
for m in \
  builds.stock \
  builds.etf \
  builds.index.composition \
  builds.index.baseline \
  builds.bond \
  builds.options.szse
do
  python -m "$m"
done

# analyze, run daily. industry_correlations is now an internal step of
# industry_sentiments (runs automatically after the sentiments table is
# repopulated, reusing the same DB connection).
for m in \
  analyze.industry_sentiments \
  analyze.mov_ave_spread \
  analyze.sec_alloc_perf_attribution
do
  python -m "$m"
done

# on monthly start date
python -m downloads.etf.sse.composition
python -m downloads.etf.szse.composition
python -m downloads.index.csindex.composition
python -m downloads.index.szse.composition
python -m downloads.etf.csindex.linked_etf

# download, run once
python -m downloads.stock.szse.archive
python -m downloads.etf.szse.archive
python -m downloads.index.szse.archive
python -m downloads.stock.sse.archive

# build, run once
python -m builds.classification

# always run
python -m downloads.stream.sse.price
python -m downloads.stream.szse.price
python -m downloads.stream.csindex.price   