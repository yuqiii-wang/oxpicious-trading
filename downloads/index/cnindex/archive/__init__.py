"""Runner for the CNINDEX daily archive downloader.

Refreshes ``{code}_history.csv`` under ``temps/cnindex_archive`` from the
hq.cnindex.com.cn daily API for the CNINDEX-published indices no other
source covers. Content-freshness gated (see runner docstring).
"""
from __future__ import annotations

from .runner import CnindexArchiveRunner

__all__ = ["CnindexArchiveRunner"]
