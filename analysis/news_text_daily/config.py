"""Config for analysis.news_text_daily — the daily news-text metric panel.

One output table in the text schema (DDL: database/sql/text/
news_text_daily.sql):
  text.news_text_daily — per (date, scope_kind, scope_code): the daily
  sentiment state (level / z / ewm halflife 3+10 / day change), the
  abnormal-flow flag, sign-split counts, origin dispersion, and the
  keyword-tone cross-check (market kinds), plus per-industry event-day
  aggregates (kind 2) and their per-sector rollup (kind 3).

The metric definitions and their validated readings live in the DDL
header and the __main__.py docstring; the window/threshold knobs below
are the 2026-11-21 feasibility study's settings.
"""
from __future__ import annotations

from _common.labels import NEWS_SCOPE_KIND_CODE

ANALYSIS_NAME = "news_text_daily"
DESCRIPTION = (
    "Daily news-text metric panel (text.news_text_daily): per market "
    "scope — the lexicon-blended sentiment level, trailing-60d z, ewm "
    "halflife-3/10 lines, day change, the abnormal-news-flow flag "
    "(median/IQR of daily article counts over 63d), sign-split article "
    "counts, origin dispersion, and the kw_market px-tone cross-check; "
    "per industry — event-day aggregates only (a row on days the "
    "industry is substantially co-mentioned — the 2026-11-22 "
    "co-mention attribution: an article counts for every catalog "
    "industry whose keywords it matches with >= MENTION_MIN_KW_CHARS "
    "total matched chars, not just its single primary tag — so niche "
    "industries like 高端制造/ADVMFG populate from the same corpus "
    "instead of losing every article to the financial primaries; the "
    "daily-index grain stays infeasible, the validated event-study "
    "framing instead); per sector — the co-mention event-day "
    "aggregates rolled UP the taxonomy to the L1 parent sector (the "
    "catalog's industry->sector map, each article counted once per "
    "sector — the same event-day framing — a sector pools its member "
    "industries' coverage so its event days run denser). Weekend "
    "articles roll to the next SSE trading day (T+1-compatible)."
)

# --- Tables ------------------------------------------------------------------

TABLE = "text.news_text_daily"

# --- Scopes ------------------------------------------------------------------

#: The market scopes' return index (calendar + the join key stored in
#: scope_code for kinds 0/1).
MARKET_INDEX_CODE = "000001"

#: The broad-market recap tag — the market_recap scope's article subset
#: (the CFSD paper's source-class split: official recap text vs the
#: whole corpus).
RECAP_INDUSTRY = "BROAD_SSE"

KIND_MARKET_ALL = NEWS_SCOPE_KIND_CODE["market_all"]
KIND_MARKET_RECAP = NEWS_SCOPE_KIND_CODE["market_recap"]
KIND_INDUSTRY = NEWS_SCOPE_KIND_CODE["industry"]
KIND_SECTOR = NEWS_SCOPE_KIND_CODE["sector"]

# --- Windows (trading days; the 2026-11-21 study's settings) -----------------

#: Trailing window of the sentiment z-score and the keyword-tone z.
Z_WINDOW = 60
#: Min observations the trailing z needs (the 2026-11-22 fill study:
#: 20 left the panel's first ~3 weeks and every post-warm-up low-
#: coverage stretch NULL — 10 keeps the z defined ~2 weeks in while the
#: low_coverage flag still marks the thin days).
Z_MIN_OBS = 10

#: Min observations the origin-dispersion z needs (the 2026-11-22
#: improvement study's measured form: disp_origin carries NaN on
#: single-origin days, so its rolling window gathers obs slower than
#: the level's — 20 of a 60-row window, moments shifted 1 row).
DISP_Z_MIN_OBS = 20

#: Trailing window of the abnormal-flow median/IQR (one quarter).
FLOW_WINDOW = 63
#: Min observations AF needs (40 blanked the panel's first two months;
#: 15 defines it from ~3 weeks in — the median/IQR of a 0-count-filled
#: series is still meaningful on quiet histories).
FLOW_MIN_OBS = 15

#: ewm halflives on sent_z — the shock line and the persistent line
#: (the persistent one carries the validated contrarian tail).
EMA3_HALFLIFE = 3
EMA10_HALFLIFE = 10

# --- Blend + guards ----------------------------------------------------------

#: Per-article blend: s = 0.7*LLM + 0.3*lexicon (lexicon absent -> LLM
#: alone); a disagreeing article (text.news_lexicon_scores.disagrees)
#: carries this weight instead of 1.0.
BLEND_LLM_WEIGHT = 0.7
BLEND_LEX_WEIGHT = 0.3
DISAGREE_WEIGHT = 0.5

#: The noisy-mean guard, TWO tiers (the 2026-11-22 fill study): the
#: LEVEL computes from LEVEL_MIN_SCORED (1) — a thin day still carries
#: a reading instead of a hole — while SCORED_MIN (3) drives the
#: low_coverage FLAG the UI greys with. SCORED_MIN no longer NULLs the
#: level; it only marks it thin.
LEVEL_MIN_SCORED = 1
SCORED_MIN = 3

#: Sign-split thresholds on the blended score (the study's ±1 cut).
FLOW_POS_CUT = 1.0
FLOW_NEG_CUT = -1.0

#: The co-mention substantiality bar (the 2026-11-22 attribution
#: rework): an article attributes to a mentioned industry when its
#: total matched keyword length for that industry reaches this many
#: characters — one 4-char keyword (智能制造/高端装备) or two 2-char
#: hits (煤炭+焦煤); a single incidental 2-char mention does not
#: attribute. The 2026-11-22 window study: >= 4 chars covered 121
#: catalog industries in the recent 20d vs 46 under primary-tag
#: attribution (ADVMFG 0 -> 15 event days), while dropping only ~350
#: thin single-2-char-hit pairs vs the any-mention bar.
MENTION_MIN_KW_CHARS = 4

# --- Run scope ---------------------------------------------------------------

#: Calendar days of warmup loaded before the earliest target date so the
#: trailing windows are defined from the first computed day.
WARMUP_CAL_DAYS = 120

#: Trailing calendar days recomputed every run — late digestions /
#: lexicon scores correct the just-closed days (the news_audit grace
#: convention).
GRACE_DAYS = 15
