for f in \
  download_szse_trend.py \
  download_sse_trend.py \
  download_szse_margin.py \
  download_sse_margin.py \
  download_csindex.py \
  download_shibor.py \
  download_chinabond.py \
  download_pboc_repo_news.py \
  download_sse_options_price.py \
  download_pboc_lpr_news.py
do
  python "$f"
done

# build combined CSVs
for f in \
  build_szse_sse_bse_stocks.py \
  build_szse_sse_etf_and_margin.py \
  build_csindex.py \
  build_debt_baseline.py \
  build_szse_options.py
do
  python "$f"
done

# analyze, run daily
for f in \
  analyze_mov_ave_spread.py \
  analyze_sec_alloc_perf_attribution.py
do
  python "$f"
done

# on monthly start date
python download_sse_etf_composition.py
python download_szse_etf_composition.py
python download_index_composition.py
python download_szse_index_composition.py
python download_csindex_linked_etf.py

# download, run once 
python download_szse_archive.py
python download_sse_archive.py

# build, run once
python build_sec_classification.py

# always run
python stream_sse_price.py
python stream_szse_price.py
