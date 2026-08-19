"""builds._commons.paths — Centralized source CSV directory paths.

All build scripts share the same set of temps/ directories for source CSVs.
This module defines them once so builds/stock, builds/etf, builds/index,
builds/bond, and builds/options can import from a single source.

Usage:
    from builds._commons.paths import (
        SZSE_ARCHIVE_DIR, SZSE_TREND_DIR, SSE_TREND_DIR, BSE_TREND_DIR,
        SZSE_MARGIN_DIR, SSE_MARGIN_DIR, SSE_PE_DIR,
        COMP_DIR, INDEX_COMP_DIR, SZSE_INDEX_COMP_DIR,
        CSINDEX_DIR, CNINDEX_DIR, SHIBOR_DIR, CHINABOND_DIR,
        SZSE_ETF_PREFIXES, SSE_ETF_PREFIXES,
    )
"""
from __future__ import annotations

import os

from _common.build_commons import PROJECT_ROOT

# ---------------------------------------------------------------------------
# Source CSV directories — shared across stock, etf, index, options
# ---------------------------------------------------------------------------
SZSE_ARCHIVE_DIR   = os.path.join(PROJECT_ROOT, "temps", "szse_archive")
SZSE_TREND_DIR     = os.path.join(PROJECT_ROOT, "temps", "szse_trend")
SSE_TREND_DIR      = os.path.join(PROJECT_ROOT, "temps", "sse_trend")
BSE_TREND_DIR      = os.path.join(PROJECT_ROOT, "temps", "bse_trend")

# SSE PE files are per-stock ({code}_pe.csv) in the sse_archive dir
SSE_PE_DIR         = os.path.join(PROJECT_ROOT, "temps", "sse_archive")

# Margin detail CSVs (per-security 融资融券)
SZSE_MARGIN_DIR    = os.path.join(PROJECT_ROOT, "temps", "szse_margin")
SSE_MARGIN_DIR     = os.path.join(PROJECT_ROOT, "temps", "sse_margin")

# ETF composition CSVs (per-ETF holdings snapshots)
COMP_DIR           = os.path.join(PROJECT_ROOT, "temps", "szse_etf_composition")

# Index composition CSVs
INDEX_COMP_DIR      = os.path.join(PROJECT_ROOT, "temps", "csi_index_composition")
SZSE_INDEX_COMP_DIR = os.path.join(PROJECT_ROOT, "temps", "szse_index_composition")

# CSIndex / CNINDEX history CSVs (index OHLCV + PE)
CSINDEX_DIR        = os.path.join(PROJECT_ROOT, "temps", "csindex")
CNINDEX_DIR        = os.path.join(PROJECT_ROOT, "temps", "cnindex_archive")

# SHIBOR + China bond yield curve (debt/bond builds)
SHIBOR_DIR         = os.path.join(PROJECT_ROOT, "temps", "shibor")
CHINABOND_DIR      = os.path.join(PROJECT_ROOT, "temps", "chinabond")

# SZSE options source dir (same as szse_trend for trend CSVs)
SZSE_OPTION_DIR    = SZSE_TREND_DIR

# ---------------------------------------------------------------------------
# ETF code prefixes — used to distinguish ETFs from stocks in margin CSVs
# ---------------------------------------------------------------------------
SZSE_ETF_PREFIXES = ("15", "16")
SSE_ETF_PREFIXES  = ("510", "511", "512", "513", "515", "516", "518", "56")

# ---------------------------------------------------------------------------
# SZSE broad-market index codes (supplement CSIndex history)
# ---------------------------------------------------------------------------
SZSE_INDEX_CODES = {"399001", "399006"}  # 深证成指, 创业板指
