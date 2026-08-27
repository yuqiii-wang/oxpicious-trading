"""Thin entry point for ``python -m builds.stock``.

The pipeline lives in builds.stock.pipeline (cli / discovery /
gap_detection / margin_gap / archive / writer / main). This file only
sets up the runtime (warnings, cudf.pandas activation, UTF-8 stdout)
before pandas is imported anywhere, then delegates to pipeline.main().
"""

# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
import warnings
warnings.filterwarnings("ignore")

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

from _common.build_commons import setup_utf8_stdout
setup_utf8_stdout()

import asyncio

from builds.stock.pipeline.main import main

if __name__ == "__main__":
    asyncio.run(main())
