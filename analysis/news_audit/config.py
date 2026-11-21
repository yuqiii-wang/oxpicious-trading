"""Config for analysis.news_audit — the 5-day news provider/content audit.

Three tables in the text schema (DDL: database/sql/text/):
  text.news_providers       — origin registry (source, author), is_blocked
  text.news_provider_audit  — per 5d window per origin
  text.news_content_audit   — per 5d window per industry (+ keyword rollup)
"""
from __future__ import annotations

from datetime import date

ANALYSIS_NAME = "news_audit"
DESCRIPTION = (
    "News provider/content audit: syncs the text.news_providers origin "
    "registry (source, author, is_blocked) and builds the two 5-day-window "
    "audit tables — text.news_provider_audit (per origin: items, digested, "
    "fake share, avg sentiment, avg word count, industry spread) and "
    "text.news_content_audit (per industry: the same metrics plus the "
    "distinct-keywords / keyword-mentions / articles-with-keywords rollup "
    "from text.news_keywords)."
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

# Days since the unix epoch of the anchor date — the grid's integer origin.
ANCHOR_DAYS = (ANCHOR_DATE - date(1970, 1, 1)).days
