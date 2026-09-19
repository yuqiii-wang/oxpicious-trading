"""mov_pairs signal-family SQL (analyze.analysis_signals.fetch.mov_pairs)
— one shared template pair for the four pair-cross families."""
from ._sql import (
    MOV_PAIRS_BUCKET_COLUMNS,
    MOV_PAIRS_BUCKET_EPOCH_COLS,
    mov_pairs_buckets_sql,
    mov_pairs_values_sql,
    PAIRS_FAMILY_SOURCES,
)

__all__ = [
    "MOV_PAIRS_BUCKET_COLUMNS",
    "MOV_PAIRS_BUCKET_EPOCH_COLS",
    "mov_pairs_buckets_sql",
    "mov_pairs_values_sql",
    "PAIRS_FAMILY_SOURCES",
]
