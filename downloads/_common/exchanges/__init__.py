"""Per-exchange downloader/format adapters.

SSE, SZSE and BJS publish data through different endpoints with different
on-disk CSV conventions; each exchange's specifics are consolidated in a
dedicated module here while truly shared logic stays in the parent
``downloads._common`` package.
"""
