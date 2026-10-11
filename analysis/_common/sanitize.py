"""Thin shim: sanitize_for_db_insert moved to _common/df_utils/sanitize.py
(2026-08-24) so builds.* and analysis.* share ONE implementation. Kept for
backward compatibility with existing ``from analysis._common import ...``
imports."""
from _common.df_utils.sanitize import sanitize_for_db_insert

__all__ = ["sanitize_for_db_insert"]
