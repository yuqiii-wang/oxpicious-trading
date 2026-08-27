"""NaN/inf/None sanitization for asyncpg bulk upsert.

Moved from analyze/_common/sanitize.py (2026-08-24) so both builds.* and
analyze.* packages share ONE implementation. The original module remains
as a thin shim for backward compatibility.

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

After processing numeric_cols, any remaining NaN/NaT in object or
datetime columns (e.g. date columns with NaN for non-matured expiries)
is also converted to None so asyncpg serializes them as SQL NULL.

cuDF note: ``to_dict(orient="records")`` always materializes Python
objects (GPU→CPU). This is unavoidable for asyncpg. The rest of the
pipeline should stay in numeric dtype as long as possible and only call
this function at the very end.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def safe_columns(df: pd.DataFrame) -> list[str]:
    """Materialize column names as a plain Python list.

    Avoids cudf.pandas fallback for `col in df.columns` (Index.__contains__ /
    Index.__len__ / IndexOpsMixin.tolist all force a slow path due to
    transfer blocking). np.asarray goes through the __array__ protocol,
    a single explicit transfer with no fallback; membership checks and
    iteration then run on CPU.
    """
    return np.asarray(df.columns).tolist()


def sanitize_for_db_insert(
    df: pd.DataFrame,
    *,
    numeric_cols: list[str] | None = None,
    round_to: int | None = None,
) -> list[dict]:
    """Sanitize a DataFrame for asyncpg bulk upsert.

    Steps (applied to ``numeric_cols`` only; other columns pass through
    the final NaN→None sweep):
      1. Round to ``round_to`` decimal places (NaN-safe — round preserves
         NaN). Optional; pass ``None`` to skip.
      2. Replace +/-inf with NaN (rolling correlation / division can
         produce inf when one series has zero variance).
      3. Cast to object dtype, then replace NaN with None so asyncpg
         serializes them as SQL NULL.
      4. Final sweep: convert any remaining NaN/NaT in object/datetime
         columns to None (e.g. date columns with NaN for non-matured
         expiries).
      5. ``to_dict(orient="records")`` — materialize list of dicts.

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

    # ---- Columnar fast path ------------------------------------------
    # The original DataFrame chain (round -> replace -> astype(object)
    # .where -> to_dict) costs ~3.4 us/row; at 15M+ rows (industry
    # correlations) that is ~50 s of pure pandas overhead. Building
    # each column as a python list with vectorized numpy and zipping
    # into dicts is ~3x faster with identical semantics.
    numeric_set = set(numeric_cols) if numeric_cols is not None else {
        c for c in safe_columns(df) if pd.api.types.is_numeric_dtype(df[c])
    }

    names = safe_columns(df)
    col_lists: list[list] = []
    for c in names:
        s = df[c]
        if c in numeric_set and pd.api.types.is_float_dtype(s):
            arr = s.to_numpy(dtype=np.float64)
            if round_to is not None:
                arr = np.round(arr, round_to)
            # NaN and +/-inf all -> None (SQL NULL) in one mask.
            bad = ~np.isfinite(arr)
            if bad.any():
                oa = arr.astype(object)
                oa[bad] = None
                col_lists.append(oa.tolist())
            else:
                col_lists.append(arr.tolist())
        elif c in numeric_set:
            # Non-float numeric (ints — rounding is a no-op): NaN sweep
            # only. Never coerce to float64: asyncpg would encode int
            # values as float8 for integer DB columns.
            col_lists.append(
                s.astype(object).where(pd.notna(s), None).tolist()
            )
        elif pd.api.types.is_datetime64_any_dtype(s):
            # datetime64 -> python datetime (asyncpg-native); NaT->None.
            # CRITICAL cudf.pandas quirk: Series.to_numpy() returns a
            # PROXIED ndarray whose .tolist() emits raw int64 ns values
            # (asyncpg then dies with "'int' object has no attribute
            # 'toordinal'"). Round-tripping through astype("datetime64[us]")
            # .astype(object) materializes REAL python datetime objects on
            # the host — the proxy's astype falls back to numpy internally
            # and hands back a host array. [us] is lossless for PostgreSQL
            # TIMESTAMP columns (microsecond resolution).
            arr = s.to_numpy().astype("datetime64[us]")
            bad = np.isnat(arr) if arr.dtype.kind == "M" else None
            if bad is not None and bad.any():
                oa = arr.astype(object)
                oa[bad] = None
                col_lists.append(oa.tolist())
            else:
                col_lists.append(arr.astype(object).tolist())
        else:
            # Object/string columns: only NaN -> None (vectorized).
            mask = s.isna()
            if mask.any():
                col_lists.append(
                    s.astype(object).where(~mask, None).tolist()
                )
            else:
                col_lists.append(s.tolist())

    return [dict(zip(names, row)) for row in zip(*col_lists)]
