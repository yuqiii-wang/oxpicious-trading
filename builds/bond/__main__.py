"""builds/bond/__main__.py — Build debt-market baseline and insert directly to
the database (no intermediate CSV).

Aggregates daily-frequency data sources into the 8 debt_* tables:
  1. PBoC OMO daily reverse-repo announcements     → stats.debt_omo
  2. PBoC outright-repo tender announcements        → stats.debt_outright_repo
  3. PBoC MLF tender                                → stats.debt_mlf
  4. Repo lifecycle tracking (running cumulative)   → stats.debt_repo
  5. SHIBOR daily fixing rates                      → stats.debt_shibor
  6. China bond (中债国债) daily yield-curve data    → stats.debt_treasury
  7. PBoC LPR monthly announcements                 → stats.debt_lpr
  8. PBoC Open Market Announcements policy notices  → stats.pboc_oma

Usage:
  python -m builds.bond
  python -m builds.bond --start-date 2024-01-01 --end-date 2026-07-14
  python -m builds.bond --force

See builds/bond/pipeline.py for the full missing-data detection flow.
"""

# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()

import warnings
warnings.filterwarnings("ignore")

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

# Import the pipeline only AFTER activation (its modules import pandas)
from _common.build_commons import setup_utf8_stdout

setup_utf8_stdout()

import asyncio

from builds.bond.pipeline import main


if __name__ == "__main__":
    asyncio.run(main())
