"""DB fetchers for analyze.mov_ave_spread.

source — the per-(code, date) price + MA source frame every internal step
consumes (one DB round-trip per sec_type for the whole pipeline).
"""
from analyze.mov_ave_spread.fetch.source import fetch_source_data

__all__ = ["fetch_source_data"]
