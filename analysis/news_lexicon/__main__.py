"""Entry point for analysis.news_lexicon.

Run via ``python -m analysis.news_lexicon [--force]``.

CFSD lexicon scoring over the digested news corpus — text.news_lexicon_
scores (see database/sql/text/news_lexicon_scores.sql). Scores every
digested article's text with the static.fin_sentiment_dict dictionary
(the CFSD paper's unit/negation/degree rule) and flags the disagreement
against the LLM digestion score. Orchestration: analysis_daily step,
BEFORE analysis.news_text_daily (which blends these scores into the
daily metrics).
"""
from _common.data_pipeline import bootstrap_runtime

# CPU-only pipeline: the frames are text-scale (~25k article rows — no
# GPU-width compute), and the nightly chain runs alongside the resident
# local-model host whose VRAM this pipeline's own host-first segmenter
# needs — bootstrap_runtime() would gate on 16 GiB free and lose.
bootstrap_runtime(gpu_mode="off", require_gpu=False)

from _common.log_setup import setup_logging  # noqa: E402

setup_logging("news_lexicon")

from analysis.news_lexicon.pipeline import NewsLexiconAnalysis  # noqa: E402

if __name__ == "__main__":
    NewsLexiconAnalysis().execute()
