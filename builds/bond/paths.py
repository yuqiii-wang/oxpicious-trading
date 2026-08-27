"""builds.bond.paths — Source file paths for the debt-market baseline build.

Source data produced by the downloads side:
  - temp_data/analysis_output/pboc_repo_news/instruments_combined.csv
  - temps/pboc_lpr_news/lpr_combined.csv
  - temps/pboc_oma_news/oma_combined.csv
  - temps/shibor/shibor_his_*.csv        (converted from xlsx by downloads)
  - temps/chinabond/chinabond_bzqx_treasury_bond_*.csv (same)
"""
from __future__ import annotations

import os

from _common.build_commons import PROJECT_ROOT

PBOC_INSTRUMENTS_CSV: str = os.path.join(
    PROJECT_ROOT, "temp_data", "analysis_output", "pboc_repo_news", "instruments_combined.csv"
)
PBOC_LPR_CSV: str = os.path.join(
    PROJECT_ROOT, "temps", "pboc_lpr_news", "lpr_combined.csv"
)
PBOC_OMA_CSV: str = os.path.join(
    PROJECT_ROOT, "temps", "pboc_oma_news", "oma_combined.csv"
)
SHIBOR_DIR: str = os.path.join(PROJECT_ROOT, "temps", "shibor")
CHINABOND_DIR: str = os.path.join(PROJECT_ROOT, "temps", "chinabond")
