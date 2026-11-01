"""Tokenization helpers: Chinese word segmentation via the local StructBERT
model, plus search-index tokenization and weighted tsvector writes for the
text schema.  Class-oriented API — see chinese_word_segmenter /
search_indexing for behavior and design notes; keyword_ngrams matches
token streams back to the keyword_map vocabulary via up-to-4-gram joins."""
from .chinese_word_segmenter import ChineseWordSegmenter
from .keyword_ngrams import NGRAM_MAX, match_keywords, match_keywords_batch
from .search_indexing import TSV_SPECS, TsvSpec, SearchIndexer

__all__ = [
    "ChineseWordSegmenter",
    "SearchIndexer",
    "TSV_SPECS",
    "TsvSpec",
    "NGRAM_MAX",
    "match_keywords",
    "match_keywords_batch",
]
