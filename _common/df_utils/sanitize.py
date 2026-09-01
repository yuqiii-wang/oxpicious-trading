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


def column_subset(df: pd.DataFrame, needed) -> list[str]:
    """Which of ``needed`` columns exist in ``df`` (host-pure membership).

    Replaces ``[c for c in needed if c in df.columns]`` — each
    ``in df.columns`` is a proxied ``Index.__contains__`` fallback.
    """
    cols = set(safe_columns(df))
    return [c for c in needed if c in cols]


def host_array(x) -> np.ndarray:
    """Unwrap a cudf.pandas proxy ndarray to a RAW host numpy array.

    Arrays derived from proxied pandas objects (``series.to_numpy()``,
    ``series.values`` ...) are proxy-subclass ndarrays whose EVERY
    downstream numpy op dispatches through ``__array_function__`` into
    the cudf fast/slow machinery — profiling (B-A1) showed 128 of 136 s
    per long-history code burned there. Raw ndarray inputs keep the
    whole downstream path in plain host numpy (repo convention: unwrap
    ONCE at the pandas→numpy boundary).

    Also unwraps proxy Series/DataFrames via ``np.asarray`` (one explicit
    transfer, no per-op dispatch).
    """
    unwrapped = x._fsproxy_slow if hasattr(x, "_fsproxy_slow") else x
    if isinstance(unwrapped, np.ndarray):
        return unwrapped
    return np.asarray(unwrapped)


def host_dtypes(df: pd.DataFrame) -> list[np.dtype]:
    """Column dtypes as a plain list of numpy dtypes (ONE transfer).

    Replaces per-column ``pd.api.types.is_*_dtype(df[c])`` checks — each
    of those is a proxied dispatch that falls back on frames holding
    object-date columns. ``df.dtypes`` is metadata (no data transfer),
    and the returned dtype objects are raw numpy — branch on ``dt.kind``
    host-pure.
    """
    return list(np.asarray(df.dtypes))


def host_isna(arr: np.ndarray) -> np.ndarray:
    """Host-pure NaN/None/NaT mask for a RAW host numpy array.

    Replaces ``pd.isna(arr)`` — under cudf.pandas the proxied
    ``pandas.isna`` has no fast implementation for ndarray inputs and
    every call logs a ``NotImplementedError`` fallback before running
    the (identical) slow path. Branching on ``arr.dtype.kind`` keeps the
    whole computation in raw host numpy with zero proxy dispatch:

      - float   : ``np.isnan``
      - datetime/timedelta : ``np.isnat``
      - int/uint/bool      : no missing values → all-False
      - object/other       : ``x != x`` (NaN, NaT) OR ``x == None``
    """
    kind = arr.dtype.kind
    if kind == "f":
        return np.isnan(arr)
    if kind in "Mm":
        return np.isnat(arr)
    if kind in "iub":
        return np.zeros(arr.shape, dtype=bool)
    return (arr != arr) | (arr == None)  # noqa: E711 — object cols only


def epoch_col_to_dt64(vals, unit: str = "us", index=None) -> pd.Series:
    """float8 epoch-seconds column -> datetime64[unit] (ONE host numpy pass).

    The DB read-path convention: SQL returns
    ``extract(epoch from <date/time col>)::float8`` so the date column
    lands as NATIVE float64 (NULL -> NaN) instead of a python
    date/datetime object column — this ends the object-dtype poison
    (cudf MixedTypeError fallbacks on every downstream op) and makes the
    datetime64 UNIT an explicit per-site argument instead of a
    backend-dependent accident ([s] under cudf.pandas' ctor auto-convert,
    [ns] under host pandas, [us] only where the old code remembered
    ``.astype("datetime64[us]")``).

    Default unit is ``"us"`` — the DB-READ convention ([us] = PostgreSQL
    TIMESTAMP / python datetime resolution; safe range through the
    9999-12-31 sentinel dates that overflow datetime64[ns] at 2262).
    Pass ``unit="ns"`` ONLY at wide-op sites whose frames must match
    pandas-native ns Timestamps (see ``epoch_ns_array``).

    Verified (temp_scripts/probe_epoch_edge_cases.py, 2026-08-30):
      - NaN -> NaT natively through ``astype("datetime64[...]")``
      - exact through [s]->[us]->[ns] chains (5,000 midnights, 137-year
        span, zero drift)
      - ``extract(epoch from date)`` is timezone-independent (naive
        semantics; epoch of 1970-01-01 = 0.0 under UTC and Asia/Shanghai
        sessions)

    Args:
        vals: epoch-seconds float8 values — a Series (post-ctor), a
            plain list/tuple (pre-ctor ``rec_cols`` column, NULLs as
            None), or an ndarray.
        unit: numpy datetime64 resolution ("us" default; "s", "ns", "D").
        index: optional index for the returned Series (pass the frame's
            index when replacing a column of an already-indexed frame).

    Returns:
        pd.Series of dtype datetime64[unit] (NaN -> NaT).
    """
    # Values are epoch SECONDS: first land on datetime64[s] (exact — the
    # wire values are integer seconds, float64-exact to ~2^53 s), then
    # up/down-cast to the requested unit (also exact — downcast truncates
    # nothing because there is no sub-second residue). A direct
    # astype("datetime64[us]") would MISREAD the values as microseconds
    # (numpy interprets the integer as a count of the TARGET unit).
    arr = np.asarray(host_array(vals), dtype="float64").astype("datetime64[s]")
    if unit != "s":
        arr = arr.astype(f"datetime64[{unit}]")
    return pd.Series(arr, index=index)


def epoch_ns_array(vals) -> np.ndarray:
    """float8 epoch-seconds column -> RAW host datetime64[ns] ndarray.

    The DB-read boundary for WIDE-OP paths (rolling corr / host numpy
    kernels): the [ns] unit matches pandas-native Timestamps so
    cudf merge/isin/concat against ns frames hits the GPU hash path.
    ``host_array`` unwraps the proxy Series ONCE here — calling
    ``.to_numpy()`` on the proxy Series returned by
    ``epoch_col_to_dt64`` re-poisons with a proxy-subclass ndarray and
    the pd.DataFrame ctor falls back ("Unsupported dtype
    datetime64[ns]").
    """
    return host_array(epoch_col_to_dt64(vals, unit="ns"))


def to_dt64(x, unit: str = "us", index=None):
    """Align any date-like input to datetime64[unit] (proxy-safe).

    The ALIGNMENT convention (S3): wherever a date column meets another
    frame (concat / merge / merge_asof / isin), both sides must share
    the SAME datetime64 unit — cuDF falls back on mixed-unit ops
    ("All columns must be the same type"). Default ``"us"`` matches the
    DB-read convention; pass ``unit="ns"`` to meet pandas-native frames.

    Replaces the scattered ``pd.to_datetime(x).astype("datetime64[..]")``
    idioms — one documented entry point instead of per-usage casts.

    Args:
        x: pd.Series, ndarray, DatetimeIndex, or list-like of python
            date/datetime objects (NaT/None preserved).
        unit: target datetime64 resolution ("us" default; "ns", "s", "D").
        index: optional index for the returned Series (Series input
            only; defaults to the input's own index).

    Returns:
        pd.Series (Series input) or np.ndarray (other inputs) of dtype
        datetime64[unit].
    """
    target = f"datetime64[{unit}]"
    if isinstance(x, pd.Series):
        arr = host_array(x)
        if arr.dtype.kind == "M":
            arr = arr.astype(target)
        else:
            # object (python date/datetime/None) or ISO-string input: ONE
            # plain numpy cast — None -> NaT natively. pd.to_datetime
            # would log a cudf MixedTypeError fallback + transfer-block
            # exceptions per call on object dates; numpy never dispatches.
            arr = np.asarray(arr, dtype=target)
        return pd.Series(arr, index=index if index is not None else x.index)
    # ndarray / DatetimeIndex / list-like -> ndarray out
    arr = host_array(x)
    if isinstance(arr, np.ndarray) and arr.dtype.kind == "M":
        return arr.astype(target)
    return np.asarray(arr, dtype=target)


def to_py_dates(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Convert datetime64 columns to python ``datetime.date`` object
    columns (ONE host numpy pass per column).

    The DB-write boundary convention (B-A2): keep dates as datetime64
    through ALL compute (cuDF-native groupby/sort/compare) and
    materialize python date objects only when rows are about to be
    written or compared against DB-sourced PK tuples.

    ``.dt.date`` is NOT implemented by cuDF (one per-element
    MixedTypeError per row) — never use it on large frames. This helper
    round-trips through a host numpy array instead: a single proxy
    dispatch per column, zero per-element calls.

    Returns the same DataFrame with the listed columns replaced
    in place (datetime64[ns]/[us] -> object dtype of datetime.date).
    """
    for c in columns:
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            arr = s.to_numpy().astype("datetime64[D]").astype(object)
            df[c] = arr
    return df


def sanitize_for_db_insert(
    df: pd.DataFrame,
    *,
    numeric_cols: list[str] | None = None,
    round_to: int | None = None,
    date_cols: list[str] | None = None,
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
        date_cols: columns that must emit python ``datetime.date`` objects
            (asyncpg DATE columns). They stay datetime64 in the frame and
            are converted host-side in the datetime64 branch — NEVER
            pre-convert object-date columns into the frame before calling
            this (cuDF cannot represent object-date columns; every
            subsequent frame op — even unrelated numeric column access —
            pays a MixedTypeError fast-path failure + fallback).

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
    #
    # cudf.pandas discipline (B-A3): dtype branches come from ONE
    # ``host_dtypes`` pass (per-column ``pd.api.types.is_*_dtype(df[c])``
    # each dispatches through the proxy and falls back on frames holding
    # object-date columns); data leaves the frame ONCE per column via
    # ``to_numpy`` + :func:`host_array` unwrap, and every downstream
    # numpy op (round/isfinite/astype/tolist) is then raw host numpy.
    if numeric_cols is not None:
        numeric_set = set(numeric_cols)
    else:
        numeric_set = {
            c for c, dt in zip(safe_columns(df), host_dtypes(df))
            if dt.kind in "fiu"
        }

    names = safe_columns(df)
    dt_map = dict(zip(names, host_dtypes(df)))
    col_lists: list[list] = []
    for c in names:
        dt = dt_map[c]
        if c in numeric_set and dt.kind == "f":
            # Plain to_numpy() takes the cudf fast path; requesting
            # dtype=float64 directly raises on missing values (one
            # ValueError fallback per column) — cast on the host instead.
            arr = host_array(df[c].to_numpy()).astype(np.float64)
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
            arr = host_array(df[c].to_numpy())
            bad = host_isna(arr)
            if bad.any():
                oa = arr.astype(object)
                oa[bad] = None
                col_lists.append(oa.tolist())
            else:
                col_lists.append(arr.tolist())
        elif dt.kind == "M":
            # datetime64 -> python datetime (asyncpg-native); NaT->None.
            # CRITICAL cudf.pandas quirk: Series.to_numpy() returns a
            # PROXIED ndarray whose .tolist() emits raw int64 ns values
            # (asyncpg then dies with "'int' object has no attribute
            # 'toordinal'"). Round-tripping through astype("datetime64[us]")
            # .astype(object) materializes REAL python datetime objects on
            # the host. [us] is lossless for PostgreSQL TIMESTAMP columns
            # (microsecond resolution).
            arr = host_array(df[c].to_numpy())
            if date_cols is not None and c in date_cols:
                # DATE columns: python datetime.date objects via a day-
                # resolution cast (host numpy, zero proxy dispatch).
                arr = arr.astype("datetime64[D]")
            else:
                arr = arr.astype("datetime64[us]")
            bad = np.isnat(arr)
            if bad.any():
                oa = arr.astype(object)
                oa[bad] = None
                col_lists.append(oa.tolist())
            else:
                col_lists.append(arr.astype(object).tolist())
        else:
            # Object/string columns: only NaN/None -> None (host numpy).
            arr = host_array(df[c].to_numpy())
            bad = host_isna(arr)
            if bad.any():
                oa = arr.astype(object)
                oa[bad] = None
                col_lists.append(oa.tolist())
            else:
                col_lists.append(arr.tolist())

    return [dict(zip(names, row)) for row in zip(*col_lists)]
