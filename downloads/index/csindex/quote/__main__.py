"""COMPAT SHIM (2026-11-23) — ``downloads.index.csindex.quote`` split
into ``downloads.index.csindex.price`` (the daily sweep) and
``downloads.index.csindex.pe`` (the weekly PE fetch, own weekly gate).

This shim exists ONLY so a run already queued under the old script name
(the nightly pass in flight when the split landed — its step list was
fixed at tier start) still executes. It forwards to the price entry
verbatim, which still accepts every old argument (``--pe-only``
included). Nothing in the tier files, the UI or the config trees
references this path anymore — DELETE once the pass that carried the
old step list has ended.
"""
from __future__ import annotations

from downloads.index.csindex.price.__main__ import CsindexPriceDownload

if __name__ == "__main__":
    CsindexPriceDownload().execute()
