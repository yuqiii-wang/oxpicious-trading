"""Entry point for analysis.news_text_daily.

Run via ``python -m analysis.news_text_daily [--force]``.

The daily news-text metric panel — text.news_text_daily (DDL:
database/sql/text/news_text_daily.sql). Per (date, scope): the
lexicon-blended sentiment level, trailing-60d z, ewm halflife-3/10
lines, day change, the abnormal-news-flow flag (median/IQR of daily
article counts over 63d), sign-split counts, origin dispersion, and
the kw_market px-tone cross-check on the two market scopes; per
industry — event-day aggregates only (CO-MENTION attribution since
2026-11-22: an article counts for every catalog industry whose
keywords it matches with >= 4 total matched chars — niche industries
like 高端制造 populate from the same corpus instead of losing every
article to the primary tag); per sector — the same co-mention
event-day aggregates rolled up the taxonomy to the L1 parent sector.

VALIDATED READINGS (2026-11-21/22 feasibility study on this corpus,
2023+ window): sentiment levels are STATE, not a direction call (IC ~0
at 1-5d); sent_ema10 is CONTRARIAN at the monthly tail (after top-20%
sentiment months the SSE Composite averaged +0.33% the next month vs
+1.54% after bottom-20%); flow_af is the panel's most consistent risk
flag (negative forward IC at 1-10d horizons in every scope and year).

Orchestration: analysis_daily step, AFTER analysis.news_lexicon (whose
disagreement flags drive the blend weights). Reads text.news +
news_digestions + news_lexicon_scores + text.news_keyword_audit +
text.news_keywords ⋈ static.keyword_maps (the industry co-mention
frame) + text.news_coverage_audit + stats.v_index_daily; forward
returns and regime flags are deliberately NOT stored — join
stats.v_index_daily on (scope_code, date).
"""
from _common.data_pipeline import bootstrap_runtime

# CPU-only pipeline: the frames are text-scale (daily article rows — no
# GPU-width compute), and the nightly chain runs alongside the resident
# local-model host whose VRAM the lexicon pipeline's host-first segmenter
# needs — bootstrap_runtime() would gate on 16 GiB free and lose.
bootstrap_runtime(gpu_mode="off", require_gpu=False)

from _common.log_setup import setup_logging  # noqa: E402

setup_logging("news_text_daily")

from analysis.news_text_daily.pipeline import NewsTextDailyAnalysis  # noqa: E402

if __name__ == "__main__":
    NewsTextDailyAnalysis().execute()
