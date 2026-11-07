"""Stock build pipeline — small modules split from the former monolithic
builds/stock/__main__.py.

Modules:
- cli:           argument parsing + --code normalization
- discovery:     source CSV discovery (OHLCV + margin) and loadable dates
- gap_detection: DB missing-date detection (identity + basic_stats)
- margin_gap:    margin (融资融券) gap detection (StockMarginGaps)
- archive:       SSE archive ({code}_trend.csv) + PE ({code}_pe.csv) loading
- writer:        row construction + DB writes (StockWriter: identity /
                 basic_stats / liquidity_margin / PE estimation)
- main:          orchestration (StockPipeline)
"""
from builds.stock.pipeline.main import StockPipeline

__all__ = ["StockPipeline"]
