"""analysis.text_metrics.config — module constants.

The output table, the metric knobs (windows / bands / gates), the
kw_market tone grouping and the output column contract. Pure literals —
nothing here imports pandas, so any module may import config before the
cudf.pandas hook installs. The metric definition itself lives in the
``__main__.py`` docstring.
"""

# ---------------------------------------------------------------------------
#  Configuration
# ---------------------------------------------------------------------------

TABLE = "analysis.text_metrics_daily"
ANALYSIS_NAME = "text_metrics"
ANALYSIS_DESCRIPTION = (
    "Per-(industry_id, date) daily news-text metrics — sentiment "
    "pressure and news flow per industry plus the broad-market row "
    "(industry_id '' = ALL digestible articles that day, tagged or not; "
    "a different '' convention than text.news_content_audit's untagged "
    "bucket). Metrics follow the news-text quantification literature: "
    "sentiment_z = Tetlock (2007) abnormal sentiment ((the day's mean "
    "sentiment_level − the trailing-window prior mean) / prior std over "
    "PRIOR article-days only — the raw level is kept as evidence but "
    "NEVER is the signal, the scorer carries a +0.62 level shift over "
    "the 2020-2026 corpus); sentiment_std = the cross-article "
    "dispersion metric (Isakin 2023 — disagreement is its own signal); "
    "pos/neg_count = the |level| >= SENTIMENT_BAND split; flow_z = the "
    "abnormal news-volume z (Heston-Sinha 2017 — flow carries the "
    "longer-horizon signal); kw_tone = the closed-taxonomy lexicon "
    "overlay (bullish vs bearish kw_market mentions from "
    "text.news_keywords ⋈ static.keyword_maps) cross-checking the LLM "
    "sentiment — disagreement flags ambiguous days. sentiment_ma5 = the "
    "5-article-day smoothing of sentiment_z (the "
    "crowdedness_score_ma5 online-smoothing pattern). Rows exist only "
    "where digested_count >= MIN_ARTICLES (the analysis.pe NULL rule); "
    "z columns are NULL until the trailing window has MIN_Z_OBS prior "
    "article-days. Built by analysis.text_metrics (incremental: missing "
    "dates + the recent-grace window; --force truncates and COPY-"
    "rebuilds the full history). Sources: text.news LEFT JOIN "
    "text.news_digestions (the sentiment_level + its jev component "
    "reads) and text.news_keywords ⋈ static.keyword_maps (kw_market "
    "tone)."
)

# The trailing windows are counted in ARTICLE-DAYS (panel rows: days the
# industry had digestible articles), NOT trading/calendar days — sparse
# industries (ndrc/pboc notice feeds) would otherwise need years of
# lookback for 60 calendar days. The z windows read PRIOR rows only: the
# rolling aggregates are shifted by 1 before the z arithmetic, so the
# current day never enters its own window.
Z_WINDOW = 60           # trailing article-days for the abnormal z
MIN_Z_OBS = 20          # prior-window non-null obs required for a defined z
MA_WINDOW = 5           # smoothing rows of sentiment_z (incl. current)

SENTIMENT_BAND = 0.5    # |level| >= band → pos/neg count (the digestion
                        # summary's direction-label band)
MIN_ARTICLES = 3        # digested_count below this → NO output row
GRACE_RECOMPUTE_DAYS = 14  # incremental: already-present dates within
                           # this many days of the newest panel date are
                           # recomputed (late digestions revise recent
                           # aggregates; older ones go through --force)

# kw_market tone grouping — static.label_dim 'kw_market' codes
# (_common.labels.KW_MARKET_CODE), family-grouped up/dn pairs. The
# non-directional codes (12 divergence, 15 volatile, 16 mood, 17
# position) enter neither side.
KW_BULL_CODES: tuple[int, ...] = (
    0,   # px_up
    2,   # sharp_up
    4,   # gap_up
    6,   # limit_up
    8,   # rebound
    10,  # vol_up
    13,  # bullish
    18,  # break_up
)
KW_BEAR_CODES: tuple[int, ...] = (
    1,   # px_dn
    3,   # sharp_dn
    5,   # gap_dn
    7,   # limit_dn
    9,   # pullback
    11,  # vol_dn
    14,  # bearish
    19,  # break_dn
)
KW_CATEGORY_MARKET = 1  # static.label_dim 'kw_category_type': 1 = market

# The broad-market row key — '' = ALL articles (a different meaning than
# text.news_content_audit's '' untagged bucket; spelled out in the DDL).
MARKET_INDUSTRY_ID = ""

# FULL-HISTORY fetch (no from_date window): the corpus is the working
# set (~25k articles / ~50k panel rows — one trivial read), and the
# trailing windows are counted in article-DAYS, so a bounded lookback
# cannot cover a sparse industry's 60-row window. Force vs incremental
# differ only in the WRITE scope.
FETCH_VIEW_ARTICLES = "analysis/text_metrics/fetch/articles"
FETCH_VIEW_KEYWORD_TONE = "analysis/text_metrics/fetch/keyword_tone"

OUT_COLUMNS: list[str] = [
    "industry_id", "date", "article_count", "digested_count",
    "sentiment_mean", "sentiment_std", "sentiment_z", "sentiment_ma5",
    "pos_count", "neg_count", "flow_z",
    "kw_bull_mentions", "kw_bear_mentions", "kw_tone",
]
OUT_FLOAT_COLUMNS: list[str] = [
    "sentiment_mean", "sentiment_std", "sentiment_z", "sentiment_ma5",
    "flow_z", "kw_tone",
]
OUT_INT_COLUMNS: list[str] = [
    "article_count", "digested_count", "pos_count", "neg_count",
    "kw_bull_mentions", "kw_bear_mentions",
]
KEY_COLUMNS: list[str] = ["industry_id", "date"]
