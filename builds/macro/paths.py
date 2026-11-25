"""builds.macro.paths — Source file paths for the macro daily build.

Source data produced by downloads.macro.gold_silver (full-history
per-instrument CSVs, overwritten each run):
  - temps/gold_silver/sge_au99.99.csv
  - temps/gold_silver/sge_ag99.99.csv
  - temps/gold_silver/comex_gc.csv
  - temps/gold_silver/comex_si.csv

The csv_stem values MUST match downloads/macro/gold_silver/_endpoints.py
(the registry in builds/macro/instruments.py is the sync point).
"""
from __future__ import annotations

import os

from _common.build_commons import PROJECT_ROOT

from builds.macro.instruments import INSTRUMENTS

GOLD_SILVER_DIR: str = os.path.join(PROJECT_ROOT, "temps", "gold_silver")

INSTRUMENT_CSVS: dict[str, str] = {
    inst.code: os.path.join(GOLD_SILVER_DIR, f"{inst.csv_stem}.csv")
    for inst in INSTRUMENTS
}
