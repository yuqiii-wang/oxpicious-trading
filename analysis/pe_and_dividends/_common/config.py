"""Shared configuration constants for analysis.pe_and_dividends.

The two metric subpackages (``pe`` / ``dividends``) hold their own
table / identity / description constants; this module holds what the
pipeline's shared layers (_common.fetch, the __main__ orchestration)
need — the sec_type universe and its per-sec_type identity tables.
"""
SEC_TYPES = ("index", "etf", "stock")

SEC_TYPE_IDENTITY_TABLE = {
    "etf":   "stats.etf_identity",
    "index": "stats.index_identity",
    "stock": "stats.stock_identity",
}
