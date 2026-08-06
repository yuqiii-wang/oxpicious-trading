"""NaN/inf/None sanitization for asyncpg bulk upsert.

Consolidates the scattered pattern:
    df[cols] = df[cols].round(N)
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.astype(object).where(pd.notna(df), None)
    rows = df.to_dict(orient="records")

into a single function. This is the FINAL step before bulk_upsert_async
— it converts a pandas DataFrame into a list of dicts suitable for
asyncpg, with all NaN/inf replaced by None (SQL NULL).

The ``astype(object)`` step is necessary because pandas would otherwise
convert ``None`` back to ``NaN`` in numeric columns. By casting to
object dtype first, None stays as None.

cuDF note: ``to_dict(orient="records")`` always materializes Python
objects (GPU→CPU). This is unavoidable for asyncpg. The rest of the
pipeline should stay in numeric dtype as long as possible and only call
this function at the very end.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sanitize_for_db_insert(
    df: pd.DataFrame,
    *,
    numeric_cols: list[str] | None = None,
    round_to: int | None = None,
) -> list[dict]:
    """Sanitize a DataFrame for asyncpg bulk upsert.

    Steps (applied to ``numeric_cols`` only; other columns pass through):
      1. Round to ``round_to`` decimal places (NaN-safe — round preserves
         NaN). Optional; pass ``None`` to skip.
      2. Replace +/-inf with NaN (rolling correlation / division can
         produce inf when one series has zero variance).
      3. Cast to object dtype, then replace NaN with None so asyncpg
         serializes them as SQL NULL.
      4. ``to_dict(orient="records")`` — materialize list of dicts.

    Args:
        df: DataFrame to sanitize. Modified on a copy; original is not
            mutated.
        numeric_cols: columns to sanitize. If None, ALL columns except
            object/string columns are sanitized. Use explicit list when
            the frame has mixed numeric + string columns (the typical
            case — code, date, sec_type are strings).
        round_to: decimal places for rounding (e.g. 4 for NUMERIC(10,4)).
            None skips rounding.

    Returns:
        List of dicts suitable for ``bulk_upsert_async``.
    """
    if df.empty:
        return []

    out = df.copy()

    # Determine numeric columns if not specified.
    if numeric_cols is None:
        numeric_cols = [
            c for c in out.columns
            if pd.api.types.is_numeric_dtype(out[c])
        ]

    if numeric_cols and round_to is not None:
        out[numeric_cols] = out[numeric_cols].round(round_to)

    if numeric_cols:
        # Replace inf/-inf with NaN (one vectorized call).
        out[numeric_cols] = out[numeric_cols].replace(
            [np.inf, -np.inf], np.nan
        )
        # Cast to object so None stays None (not converted back to NaN).
        out[numeric_cols] = (
            out[numeric_cols]
            .astype(object)
            .where(pd.notna(out[numeric_cols]), None)
        )

    return out.to_dict(orient="records")
