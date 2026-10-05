# downloads, run on every biz date 19:00 (A-share trading day AND hour >= 19).
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
  downloads.stock.bse.price \
  downloads.etf.sse.trend \
  downloads.margin.szse \
  downloads.margin.sse \
  downloads.bond.shibor \
  downloads.bond.chinabond \
  downloads.macro.pboc.repo_news \
  downloads.options.sse.price \
  downloads.options.sse.risk \
  downloads.options.szse.trend \
  downloads.options.cffex.trend \
  downloads.macro.pboc.lpr_news \
  downloads.macro.zhihu.news \
  downloads.macro.gov.news \
  downloads.macro.ai_daily
do
  python -m "$m"
done

# SSE options EOD capture — fetch the post-close tstyle snapshot once
# (published ~16:29; the server serves it until the next session) so the
# day's temps/sse_options_intraday CSV gains a >=15:00 sample and the
# nightly builds.options finality gate accepts the date. Idempotent upsert.
python -m downloads.stream.sse.price --options-eod

# The daily AI market summary is ARTIFACT-ONLY (temps/ai_daily/*.json):
# this downloader never touches the DB. builds_daily.sh (source ai_daily)
# loads the artifacts into the text.* tables the UI reads — answers +
# reference hits into text.news, the Q&A into text.llm_qa
# (+ llm_qa_refs / news-group provenance).

# download, run on every biz date 19:00 (cont.)
for m in \
  downloads.index.csindex.quote \
  downloads.index.cnindex.archive \
  downloads.index.bse.archive \
  downloads.etf.huatai_pb.trend
do
  python -m "$m"
done
fi