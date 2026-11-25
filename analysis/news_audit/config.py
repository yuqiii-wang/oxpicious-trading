"""Config for analysis.news_audit — the news audits (providers, content,
daily coverage).

Four tables in the text schema (DDL: database/sql/text/):
  text.news_providers        — origin registry (source, author), news_type,
                               is_blocked
  text.news_provider_audit   — per 5d window per origin
  text.news_content_audit    — per 5d window per industry (+ keyword rollup)
  text.news_coverage_audit   — per TRADING DAY per industry ('000001' =
                               the whole-market rollup row): article /
                               digested / fake counts, the daily grain
                               the text panel reads as its flow basis
                               (the 2026-11-22 coverage migration)
"""
from __future__ import annotations

from datetime import date

from _common.labels import NEWS_TYPE_CODE

ANALYSIS_NAME = "news_audit"
DESCRIPTION = (
    "News audit: syncs the text.news_providers origin registry (source, "
    "author, news_type, is_blocked — auto-blocked once its audit history "
    "carries more than one fake item) and builds the audit tables — "
    "text.news_provider_audit (per 5d window per origin: items, digested, "
    "fake share, avg sentiment, avg word count, industry spread), "
    "text.news_content_audit (per 5d window per industry: the same "
    "metrics plus the keyword rollup) and text.news_coverage_audit (per "
    "trading day per industry: article / digested / fake counts at the "
    "daily grain, '000001' = the whole-market rollup — the coverage "
    "basis analysis.news_text_daily reads)."
)

# --- Window grid ------------------------------------------------------------

#: Window length AND stride in CALENDAR days (non-overlapping tiling).
WINDOW_DAYS = 5

#: Fixed epoch anchor for the window grid (a Monday). Window starts are
#: ANCHOR_DATE + 5k calendar days; the fixed epoch keeps the tiling stable
#: across runs (any date belongs to exactly one window, forever).
ANCHOR_DATE = date(2000, 1, 3)

#: Days of slack during which an already-complete window still recomputes —
#: late LLM digestions (fail-soft after the article) and late keyword
#: rewrites must be able to correct a just-closed window's verdicts.
GRACE_DAYS = 15

# --- Tables ------------------------------------------------------------------

PROVIDERS_TABLE = "text.news_providers"
PROVIDER_AUDIT_TABLE = "text.news_provider_audit"
CONTENT_AUDIT_TABLE = "text.news_content_audit"
COVERAGE_TABLE = "text.news_coverage_audit"

#: The coverage audit's whole-market rollup industry_id (the SSE
#: Composite code — matches text.news_text_daily's market scope_code).
MARKET_ROLLUP = "000001"

# --- Coverage toggle ----------------------------------------------------------
#
# --coverage {all|providers|content|coverage}: which audits the run
# executes (default all). 'providers'/'content' are the 5-day window
# audits; 'coverage' is the daily per-industry coverage grain
# (text.news_coverage_audit) the text panel reads. The registry sync
# always runs (cheap, and the window grids' date span comes from it).

COVERAGE_CHOICES = ("all", "providers", "content", "coverage")

# Days since the unix epoch of the anchor date — the grid's integer origin.
ANCHOR_DAYS = (ANCHOR_DATE - date(1970, 1, 1)).days

# --- Origin media type (text.news_providers.news_type) ----------------------
#
# The news_audit pipeline classifies each ORIGIN (text.news.source) into
# the label_dim 'news_type' vocabulary at registry-sync time. The source
# vocabulary itself is OPEN (every web scrape can surface a new portal
# name), so the classify is a curated exact/substring map with a semantic
# DEFAULT: an unmapped source is a news article (the scraped-portal norm),
# never the 99-other fallback — that code stays for genuinely
# unrecognized cases and manual rows.

#: Exact source → news_type code assignments (the pipeline-owned sources).
SOURCE_NEWS_TYPE: dict[str, int] = {
    # official monetary-policy / financial data releases
    "pboc_lpr": NEWS_TYPE_CODE["financial_report"],
    "pboc_omo": NEWS_TYPE_CODE["financial_report"],
    "pboc_oma": NEWS_TYPE_CODE["financial_report"],
    # the repo's own AI digest
    "ai_daily": NEWS_TYPE_CODE["ai_digest"],
    # the UGC platforms that arrive as their own pipeline source
    "zhihu": NEWS_TYPE_CODE["social_media"],
    "web": NEWS_TYPE_CODE["news_article"],
    "gov": NEWS_TYPE_CODE["news_article"],
    "ndrc": NEWS_TYPE_CODE["news_article"],
}

#: Case-insensitive substrings identifying a SOCIAL-MEDIA / UGC platform
#: source (the scraped variants: 知乎专栏, ai.xueqiu.com, guba.eastmoney.com,
#: caifuhao.eastmoney.com, www.jiuyangongshe.com, broker communities …).
SOCIAL_MEDIA_SOURCE_SUBSTRINGS: tuple[str, ...] = (
    "zhihu", "知乎", "xueqiu", "雪球", "weibo", "微博",
    "guba", "股吧", "caifuhao", "财富号", "淘股吧", "tgb.cn",
    "jiuyangongshe", "韭研公社", "bilibili", "哔哩",
    "hstong", "华盛通", "itiger", "老虎社区", "moomoo", "富途",
    "longbridge", "长桥", "blockbeats",
)


def classify_source_type(source: str) -> int:
    """text.news.source → label_dim 'news_type' code.

    Exact pipeline-source assignments first, then the case-insensitive
    UGC-platform substring guards; everything unmapped is a plain
    news article (0) — the scraped-portal norm.
    """
    exact = SOURCE_NEWS_TYPE.get(source)
    if exact is not None:
        return exact
    lowered = source.lower()
    for needle in SOCIAL_MEDIA_SOURCE_SUBSTRINGS:
        if needle in lowered:
            return NEWS_TYPE_CODE["social_media"]
    return NEWS_TYPE_CODE["news_article"]
