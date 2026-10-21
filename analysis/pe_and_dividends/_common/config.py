"""Shared configuration constants for analysis.pe_and_dividends.

The two metric subpackages (``pe`` / ``dividends``) hold their own
table / identity / description constants; this module holds what the
pipeline's shared layers (_common.fetch, the __main__ orchestration)
need — the sec_type universe and its per-sec_type identity tables.
"""
# 'industry' must stay LAST: its dividends stage reads the member indices'
# rows from analysis.dividends (sec_type='index'), so the index processor
# must have run first within the same invocation.
SEC_TYPES = ("index", "etf", "stock", "industry")

SEC_TYPE_IDENTITY_TABLE = {
    "etf":   "stats.etf_identity",
    "index": "stats.index_identity",
    "stock": "stats.stock_identity",
    # VIEW (database/sql/stats/13_industry.sql): the (date,
    # code=industry_id, name=industry_label) reshape of
    # stats.industry_basic_stats pool 'all'.
    "industry": "stats.industry_identity",
}
