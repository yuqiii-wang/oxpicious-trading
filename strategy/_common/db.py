"""Async DB connection re-exports for strategy pipelines.

All strategy scripts need:
  - get_db_or_exit() — open a connection or exit with an error
  - bulk_upsert_async() — batch INSERT ... ON CONFLICT upsert
  - setup_utf8_stdout() — Windows console fix
  - print_build_header() / print_wall_time() — consistent logging

These already live in `_common.build_commons` (the build-script shared module
at project root). This module re-exports them under `strategy._common.db` so
strategy code imports from a single, semantically-named location:

    from strategy._common.db import get_db_or_exit, bulk_upsert_async

This keeps strategy packages self-contained: a future strategy doesn't need
to know about the build-script commons layout.
"""
from __future__ import annotations

from _common.build_commons import (  # noqa: F401
    setup_utf8_stdout,
    get_db_or_exit,
    print_build_header,
    print_wall_time,
    bulk_upsert_async,
    truncate_table_async,
)
