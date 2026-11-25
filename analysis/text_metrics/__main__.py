"""Entry point for analysis.text_metrics.

Run via ``python -m analysis.text_metrics``.

Daily Text Metrics — per-(industry_id, date) news-text metrics in
analysis.text_metrics_daily, plus the broad-market row
(industry_id '' = ALL digestible articles that day, tagged or not; a
different '' convention than text.news_content_audit's untagged
bucket — see the DDL banner).

METRIC (2026-11-22 — the news-quantification study's Tier-1 set:
quantifies text into assistant-info metrics with the literature's
best-evidenced transforms, zero new model calls)

  Sources: text.news LEFT JOIN text.news_digestions (per-article
  sentiment_level and its jev component reads) and text.news_keywords ⋈
  static.keyword_maps (kw_market bull/bear mention sums — the
  closed-taxonomy lexicon overlay).

  Per (industry, article-day) panel:
    article_count   — digestible articles that day (the flow volume)
    digested_count  — of them, with a sentiment_level (coverage evidence)
    sentiment_mean  — raw mean sentiment_level (EVIDENCE ONLY: the jev
                      scorer carries a +0.62 positive level shift over
                      the 2020-2026 corpus — the raw level is never the
                      signal)
    sentiment_std   — cross-article std (ddof=0), the dispersion /
                      disagreement metric (Isakin 2023)
    sentiment_z     — Tetlock (2007) abnormal sentiment:
                      (mean − prior-window mean) / prior-window std
                      over the trailing Z_WINDOW (60) PRIOR article-days
                      (shift-1 — the current day never enters its own
                      window), requiring MIN_Z_OBS (20) prior obs; NULL
                      during warm-up
    sentiment_ma5   — 5-article-day mean of sentiment_z incl. current
                      (the crowdedness_score_ma5 online smoothing)
    pos/neg_count   — articles with |level| >= SENTIMENT_BAND (0.5 — the
                      digestion direction-label band)
    flow_z          — abnormal news volume: the same prior-window z
                      arithmetic on article_count (Heston-Sinha 2017:
                      flow carries the longer-horizon signal, stronger
                      than tone at weekly+ horizons)
    kw_bull/bear    — bullish/bearish kw_market mention sums that day
                      (codes in config; non-directional codes excluded)
    kw_tone         — (bull − bear) / (bull + bear), NULL when both 0 —
                      the lexicon cross-check; disagreement with
                      sentiment_z flags ambiguous days

  Windows are counted in ARTICLE-DAYS (panel rows), not calendar days —
  sparse industries (ndrc/pboc notice feeds) would otherwise need years
  of lookback. Rows exist only where digested_count >= MIN_ARTICLES (3)
  — the analysis.pe NULL rule.

  Incremental by date (missing dates + the GRACE_RECOMPUTE_DAYS 14-day
  recent-grace window — late digestions revise recent aggregates);
  --force truncates and COPY-rebuilds the full history. Both paths
  fetch FULL history: the corpus is the working set (~25k articles) and
  article-day windows make a bounded lookback unsound for sparse
  industries (config.FETCH_* rationale).
"""
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.log_setup import setup_logging

setup_logging("text_metrics")

from analysis.text_metrics.pipeline import TextMetricsAnalysis

if __name__ == "__main__":
    TextMetricsAnalysis().execute()
