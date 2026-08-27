"""xlsx/csv download persistence & reading.

CSV-preferred reads (with byte-level code pre-filtering for single-code
builds), xlsx→csv conversion with canonical-code schema, numeric-string
normalization, and atomic byte writes.
"""
from __future__ import annotations

import io
import logging
import re
import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd

from downloads._common.filescan import MIN_VALID_BYTES
from downloads._common.codes import canonicalize_code_column, filter_by_code

warnings.filterwarnings("ignore", message="Workbook contains no default style")


def convert_xlsx_to_csv(
    xlsx_path: Path,
    *,
    sheet_name: Any = 0,
    csv_path: Optional[Path] = None,
    encoding: str = "utf-8-sig",
    logger: Optional[logging.Logger] = None,
    log_tag: str = "",
    exchange: str = "",
    code_filter: Optional[List[str]] = None,
    sec_type: str = "auto",
) -> Optional[Path]:
    """Convert an xlsx file to CSV.

    *exchange* names the source exchange (e.g. "SZ") and triggers
    :func:`canonicalize_code_column` — the CSV gets the canonical
    "NNNNNN.XX" code plus ``exchange``/``board``/``sec_type`` columns so
    downstream loaders need no per-row string normalization. *sec_type*
    ("auto"/"stock"/"etf"/"index") is forwarded to the canonicalizer —
    pass an explicit type for single-type exports (e.g. SZSE tab2 = etf).
    *code_filter*, when provided, keeps only rows whose 证券代码 / 指数代码
    value (normalized to a 6-digit string) is in the list — used to extract
    a subset of rows (e.g. only 399001 / 399006 from a full-index export)
    into the CSV. The xlsx itself is left untouched.
    """
    if not xlsx_path.exists() or not xlsx_path.is_file():
        if logger:
            logger.warning(
                "%sconvert_xlsx_to_csv: xlsx not found: %s", log_tag, xlsx_path,
            )
        return None

    suffix = xlsx_path.suffix.lower()
    if suffix not in (".xlsx", ".xls"):
        if logger:
            logger.warning(
                "%sconvert_xlsx_to_csv: unsupported extension %s for %s",
                log_tag, suffix, xlsx_path.name,
            )
        return None

    if csv_path is None:
        csv_path = xlsx_path.with_suffix(".csv")

    try:
        df = pd.read_excel(xlsx_path, sheet_name=sheet_name, dtype=object)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(df, dict):
            first_sheet = next(iter(df.values()))
            first_sheet = normalize_dataframe_numbers(first_sheet)
            if code_filter:
                first_sheet = filter_by_code(first_sheet, code_filter)
            if exchange:
                # canonicalize_code_column returns the transformed copy
                # (or None when the code column is absent) — never use
                # `or` here: bool(DataFrame) raises for len > 1
                canon = canonicalize_code_column(
                    first_sheet, exchange.upper(),
                    sec_type=sec_type,
                )
                if canon is not None:
                    first_sheet = canon
            first_sheet.to_csv(csv_path, index=False, encoding=encoding)
            n_sheets = len(df)
            rows = len(first_sheet)
        else:
            df = normalize_dataframe_numbers(df)
            if code_filter:
                df = filter_by_code(df, code_filter)
            if exchange:
                canon = canonicalize_code_column(
                    df, exchange.upper(),
                    sec_type=sec_type,
                )
                if canon is not None:
                    df = canon
            df.to_csv(csv_path, index=False, encoding=encoding)
            n_sheets = 1
            rows = len(df)
    except Exception as e:
        if logger:
            logger.error(
                "%sconvert_xlsx_to_csv failed for %s: %s",
                log_tag, xlsx_path.name, e,
            )
        return None

    if logger:
        sz = csv_path.stat().st_size if csv_path.exists() else 0
        logger.info(
            "%sconverted %s -> %s (sheets=%d rows=%d csv_bytes=%d)",
            log_tag, xlsx_path.name, csv_path.name, n_sheets, rows, sz,
        )
    return csv_path


def ensure_canonical_csv(
    csv_path: Path,
    exchange: str,
    *,
    code_col: str = "证券代码",
    sec_type: str = "auto",
    logger: Optional[logging.Logger] = None,
    log_tag: str = "",
) -> bool:
    """Rewrite *csv_path* in place with the canonical code schema (idempotent).

    Skips files that already carry BOTH the ``exchange`` and ``sec_type``
    columns (use the migration script with --force to redo). Placeholder-only
    files (e.g. "没有找到符合条件的数据！") are left untouched. Returns True
    when the file was rewritten.
    """
    if isinstance(csv_path, str):
        csv_path = Path(csv_path)
    if not csv_path.exists() or csv_path.suffix.lower() != ".csv":
        return False

    # cheap header peek — idempotency check without a full read
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
            header = fh.readline().rstrip("\r\n")
    except OSError:
        return False
    if not header:
        return False
    header_cols = next(__import__("csv").reader([header]))
    if "exchange" in header_cols and "sec_type" in header_cols:
        return False  # already canonical
    if code_col not in header_cols:
        return False  # no code column (index/options exports etc.)

    try:
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False, na_values=[""])
    except Exception as e:
        if logger:
            logger.warning("%sensure_canonical_csv: read failed for %s (%s)",
                           log_tag, csv_path.name, e)
        return False

    out = canonicalize_code_column(df, exchange, code_col=code_col, sec_type=sec_type)
    if out is None:
        return False
    if not (out["exchange"] != "").any():
        return False  # placeholder / no valid code rows — leave as-is

    # write via csv module from host lists (keeps utf-8-sig BOM convention;
    # avoids the cudf to_csv encoding fallback warning)
    import csv as _csv
    cols = np.asarray(out.columns, dtype=object).tolist()
    # NaN → "": the read above used na_values=[""], so empty cells came back
    # as float NaN — csv.writer would serialize them as literal "nan"
    # strings, creating mixed-type columns downstream.
    col_vals = {
        c: ["" if (v is None or (isinstance(v, float) and v != v)) else v
            for v in np.asarray(out[c], dtype=object).tolist()]
        for c in cols
    }
    tmp_path = csv_path.with_suffix(".csv.tmp")
    with open(tmp_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = _csv.writer(fh)
        writer.writerow(cols)
        writer.writerows(zip(*[col_vals[c] for c in cols]))
    tmp_path.replace(csv_path)
    if logger:
        logger.info("%scanonicalized %s (exchange=%s sec_type=%s)",
                    log_tag, csv_path.name, exchange, sec_type)
    return True


RE_NUMERIC_PATTERN = re.compile(r"^[+-]?[\d,._\s]+(?:[,.]\d+)?$")


def normalize_numeric_string(val: str) -> Optional[float]:
    s = str(val).strip()
    if not s:
        return None
    if not RE_NUMERIC_PATTERN.match(s):
        return None

    original = s
    s = s.replace(" ", "")

    if s == "":
        return None

    has_comma = "," in s
    has_dot = "." in s
    has_underscore = "_" in s

    s = s.replace("_", "")

    if has_comma and has_dot:
        last_comma = s.rfind(",")
        last_dot = s.rfind(".")
        if last_dot > last_comma:
            decimal_sep = "."
            thousands_sep = ","
        else:
            decimal_sep = ","
            thousands_sep = "."
        s = s.replace(thousands_sep, "")
        s = s.replace(decimal_sep, ".")
    elif has_comma and not has_dot:
        parts = s.split(",")
        if len(parts) > 1 and len(parts[-1]) <= 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        # Dot-only (or bare) numeric strings: STRICT CN convention — "." is
        # ALWAYS a decimal separator (CN sources never use dot-as-thousands;
        # stripping it turned "1.4120" into 14120). Dots are stripped only
        # when ≥2 dot-separated groups of exactly 3 digits exist — an
        # unambiguous legacy-thousands pattern like "1.234.567".
        parts = s.split(".")
        if len(parts) > 2 and all(len(p) == 3 for p in parts[1:]) and parts[0]:
            s = s.replace(".", "")

    try:
        return float(s)
    except ValueError:
        return None


def normalize_dataframe_numbers(df: pd.DataFrame, *, threshold: float = 0.9) -> pd.DataFrame:
    result = df.copy()
    for col in np.asarray(result.columns).tolist():
        # is_string_dtype is GPU-clean and True for both object and cudf
        # StringDtype columns (the old `dtype != object and str(dtype) !=
        # "str"` check warned twice per string column under cudf.pandas)
        if not pd.api.types.is_string_dtype(result[col]):
            continue
        col_lower = str(col).lower()
        if "code" in col_lower or "代码" in str(col) or "编码" in str(col):
            continue

        # One clean host transfer + one Python pass. Series.apply with
        # normalize_numeric_string cannot be JIT-compiled by cudf.pandas
        # ("user defined function compilation failed" fallback on every
        # xlsx fallback read), and the old success-rate scan looped over
        # every value twice.
        host_vals = np.asarray(result[col], dtype=object).tolist()
        norm_vals = [normalize_numeric_string(v) for v in host_vals]
        # pure-Python NA check (v != v is only true for NaN) — pd.isna on
        # host scalars routes through cudf.pandas and warns per call
        total_count = sum(1 for v in host_vals if not (v is None or v != v))
        if total_count == 0:
            continue

        success_count = sum(1 for v in norm_vals if v is not None)
        success_rate = success_count / total_count
        if success_rate >= threshold:
            result[col] = [float("nan") if v is None else v for v in norm_vals]

    return result


# ---------------------------------------------------------------------------
# Raw table-row cleaning (CFFEX archive/trend exports)
# ---------------------------------------------------------------------------
# Tokens that mean "no data". Written CSVs must keep them as EMPTY cells so
# pandas auto-inference lands numeric columns on float64 at read time — the
# downstream builds parse plainly (no dynamic dtype repair).
NULL_CELL_TOKENS = frozenset({
    "--", "-", "—", "–", "null", "NULL", "None", "nan", "NaN",
})


def clean_table_cell(val: Any) -> str:
    """Normalize one raw table cell: strip whitespace, null tokens → ""."""
    s = str(val).strip()
    if s in NULL_CELL_TOKENS:
        return ""
    return s


def clean_table_rows(rows: List[List[Any]]) -> List[List[str]]:
    """Apply :func:`clean_table_cell` to every cell of every row."""
    return [[clean_table_cell(c) for c in row] for row in rows]


def safe_write_bytes(
    out_file: Path,
    content: bytes,
    *,
    min_bytes: int = MIN_VALID_BYTES,
    logger: Optional[logging.Logger] = None,
    log_tag: str = "",
    auto_convert: bool = True,
    exchange: str = "",
    sec_type: str = "auto",
) -> bool:
    """Save *content* to *out_file*.

    For ``.xlsx``/``.xls`` files, the CSV conversion is also triggered
    unless *auto_convert* is False — in which case the caller is expected
    to invoke :func:`convert_xlsx_to_csv` itself (e.g. with a code_filter
    that this auto-conversion path does not apply). *exchange* /
    *sec_type* are forwarded to the auto-conversion so freshly downloaded
    files get the canonical schema immediately.
    """
    if len(content) < min_bytes:
        if logger:
            logger.warning(
                "%s content too small (%d bytes), skipping save", log_tag, len(content),
            )
        return False
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "wb") as f:
        f.write(content)
    if logger:
        sz = out_file.stat().st_size
        logger.info("%s saved %s (%d bytes)", log_tag, out_file.name, sz)

    if auto_convert:
        suffix = out_file.suffix.lower()
        if suffix in (".xlsx", ".xls"):
            convert_xlsx_to_csv(
                out_file,
                sheet_name=0,
                logger=logger,
                log_tag=log_tag,
                exchange=exchange,
                sec_type=sec_type,
            )

    return True


def _strip_bom_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename the first column when its name carries a leading BOM.

    Source CSVs are utf-8 with a leading BOM; reading as plain utf-8 (the
    GPU-friendly path — an ``encoding`` kwarg forces a cudf CPU fallback)
    leaves "\ufeff" on the first column name.
    """
    cols = np.asarray(df.columns).tolist()
    if cols and cols[0][:1] == "\ufeff":
        df = df.rename(columns={cols[0]: cols[0][1:]})
    return df


def read_csv_gpu_safe(
    csv_path: "Path | str",
    *,
    dtype: Any = None,
    code: Optional[str] = None,
) -> pd.DataFrame:
    """GPU-native single-file CSV read — the canonical loader for source CSVs.

    Semantics (consolidated forward from builds/stock helpers._safe_read_csv):

    * Plain pd.read_csv WITHOUT ``encoding``/``thousands`` kwargs so
      cudf.pandas parses on GPU. BOM is stripped after read via
      :func:`_strip_bom_columns`.
    * ``keep_default_na=False, na_values=[""]`` — only empty fields become
      NA (mirrors the old DictReader semantics).
    * When *code* is set, the file is byte-prefiltered BEFORE parsing
      (:func:`read_csv_code_filtered_bytes`): only the header plus lines
      whose bytes contain the code token reach the parser, so whole-market
      daily snapshots never get parsed during --code builds. The result is
      a superset of exact column-equality filters callers apply afterwards;
      if pre-filtering is unsafe or fails, the normal full read runs.
    * Empty / header-only files (0 bytes, BOM/whitespace-only, or a single
      header line with no data rows) return an empty DataFrame: cudf's GPU
      CSV reader raises EmptyDataError on zero data rows, emitting a noisy
      fallback line before the CPU re-parse (which callers then see as a
      0-row frame → skip anyway).

    Returns an empty DataFrame (never raises) for unreadable files.
    """
    if isinstance(csv_path, str):
        csv_path = Path(csv_path)

    if code is not None:
        buf = read_csv_code_filtered_bytes(csv_path, code)
        if buf is not None:
            # Empty-DataFrame sentinel: whole file scanned, zero rows for
            # the code — definitive no-match answer, no parse needed.
            if isinstance(buf, pd.DataFrame):
                return buf
            try:
                # compression=None: the buffer is our own uncompressed
                # bytes; leaving 'infer' makes cudf warn per parse
                # ("Auto detection of compression type … buffer types").
                df = pd.read_csv(buf, dtype=dtype, keep_default_na=False,
                                 na_values=[""], compression=None)
                return _strip_bom_columns(df)
            except Exception:
                pass  # unsafe/odd parse → fall through to the full-path read

    try:
        with open(csv_path, "rb") as fh:
            head = fh.read(8192)
    except OSError:
        return pd.DataFrame()
    if len(head) < 8192:
        # Whole file seen: empty or header-only when nothing survives a
        # BOM/trailing-whitespace strip or no line follows the header.
        content = head.lstrip(b"\xef\xbb\xbf").rstrip()
        if not content or b"\n" not in content:
            return pd.DataFrame()
    df = pd.read_csv(csv_path, dtype=dtype, keep_default_na=False,
                     na_values=[""])
    return _strip_bom_columns(df)


def read_csv_code_filtered_bytes(
    csv_path: Path | str,
    code: str,
) -> Union[bytes, pd.DataFrame, None]:
    """Byte-level pre-filter: header + only lines containing ``code``.

    Whole-market daily snapshot CSVs must be parsed in full just to keep
    one stock's row; scanning raw BYTES first (the canonical code token
    like "000651.SZ" is pure ASCII regardless of the file's text encoding)
    lets the parser see a handful of lines instead of thousands. This
    removes both the GPU/CPU parse cost and most of the I/O decode cost
    for single-code (--code) builds.

    Returns RAW ``bytes`` — NOT ``io.BytesIO``. BytesIO is an Iterator
    (io.IOBase), and cudf.pandas' fast-path argument converter forces a
    bare-``Exception()`` slow-path fallback for every consumable/Iterator
    argument BEFORE cudf ever parses (buffer could be half-consumed on a
    retry). Plain immutable bytes carry no such hazard: they take the
    cudf GPU read path with zero fallbacks (empirically verified).

    Returns
    -------
    bytes
        The (BOM-preserving) header line plus every data line whose bytes
        contain the code token, joined with newlines.
    pd.DataFrame
        EMPTY frame when the file was safely scanned and ZERO lines match
        the code — the definitive "no row for this code" answer; callers
        short-circuit without touching the parser at all.
    None
        When line filtering is unsafe or impossible: unreadable/empty file,
        or an ODD number of double quotes in the file (an embedded newline
        inside a quoted field would make naive splitting silently drop
        rows — fall back to a full parse instead).

    Notes
    -----
    Substring matching is deliberately a SUPERSET of an exact column-value
    match: callers still apply their exact equality filter after the parse,
    so a stray mention of the code inside another column costs nothing but
    can never leak wrong rows.
    """
    try:
        with open(csv_path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if not data:
        return None
    if data.count(b'"') % 2 != 0:
        # Unbalanced quotes → quoted fields may embed newlines; the naive
        # byte-line split would corrupt such records. Full-parse fallback.
        return None

    lines = data.split(b"\n")
    header = lines[0]
    token = code.encode("ascii")
    kept = [ln for ln in lines[1:] if token in ln]
    if not kept:
        # Scanned the whole file: this snapshot genuinely has no row for
        # the code — an empty frame short-circuits the caller cleanly.
        return pd.DataFrame()
    return b"\n".join([header] + kept)


def read_csv_preferred(
    xlsx_path: Path,
    *,
    dtype: Any = None,
    sheet_name: Any = 0,
    encoding: str = "utf-8-sig",
    min_csv_bytes: int = 64,
    convert_on_fallback: bool = True,
    logger: Optional[logging.Logger] = None,
    log_tag: str = "",
    code: Optional[str] = None,
    **read_kwargs: Any,
) -> Optional[pd.DataFrame]:
    """Read CSV if present alongside xlsx_path, else fall back to xlsx.

    Prefers the CSV intermediary for speed and lower memory use. On xlsx
    fallback, optionally triggers convert_xlsx_to_csv so the CSV exists
    on next read.

    Parameters
    ----------
    xlsx_path : Path
        Canonical xlsx file path (used to derive csv path = with_suffix(".csv")).
    dtype : dtype or dict of {col: dtype}, optional
        Column dtype overrides forwarded to read_csv / read_excel.
    sheet_name : str or int or list, default 0
        Sheet selector for the xlsx fallback path only.
    encoding : str, default "utf-8-sig"
        CSV encoding used by both read and (re)write.
    min_csv_bytes : int, default 64
        Treat a CSV smaller than this as corrupt/invalid and fall back to xlsx.
    convert_on_fallback : bool, default True
        If True and we had to read from xlsx, also write the companion CSV so
        subsequent reads hit the fast path.
    logger / log_tag : forwarded to convert_xlsx_to_csv when triggered.
    code : str, optional
        Canonical security code (e.g. "000651.SZ"). When set, the companion
        CSV is byte-prefiltered to that code's lines BEFORE parsing
        (see read_csv_code_filtered_bytes) — single-code builds never
        parse whole-market snapshots. If the byte pre-filter deems the
        file unsafe/absent, the normal full read runs as before.
    **read_kwargs
        Additional kwargs forwarded to read_csv/read_excel.
    """
    if isinstance(xlsx_path, str):
        xlsx_path = Path(xlsx_path)
    csv_path = xlsx_path.with_suffix(".csv")

    csv_ok = False
    if csv_path.exists():
        try:
            sz = csv_path.stat().st_size
        except OSError:
            sz = 0
        if sz >= min_csv_bytes:
            csv_ok = True

    if csv_ok and code is not None:
        # Byte-level pre-filter path (no xlsx involvement): parse only the
        # header + rows mentioning ``code``. An empty-DataFrame result means
        # the whole file was scanned and the code genuinely absent — return
        # it as-is (no parse at all). None/parse-failure falls through to
        # the standard CSV read below.
        try:
            buf = read_csv_code_filtered_bytes(csv_path, code)
            if buf is not None:
                if isinstance(buf, pd.DataFrame):
                    return buf
                # compression=None: buffer is our own uncompressed bytes —
                # silences cudf's per-parse AUTO-detection warning.
                df = pd.read_csv(buf, dtype=dtype,
                                 compression=None, **read_kwargs)
                return _strip_bom_columns(df)
        except Exception as e:
            if logger:
                logger.warning(
                    "%sread_csv_preferred: code prefilter failed for %s (%s); "
                    "falling back to full read",
                    log_tag, csv_path.name, e,
                )

    if csv_ok:
        try:
            if encoding in (None, "utf-8", "utf-8-sig"):
                # GPU-friendly read: cudf.pandas read_csv rejects `encoding`
                # AND `low_memory` (either forces a CPU fallback on EVERY
                # file). utf-8-sig files are read as plain utf-8 and the
                # BOM is stripped from the first column name afterwards.
                # low_memory only tunes the pandas C-parser chunk size —
                # dropping it changes nothing but lets cudf take over.
                df = pd.read_csv(
                    csv_path,
                    dtype=dtype,
                    **read_kwargs,
                )
                return _strip_bom_columns(df)
            return pd.read_csv(
                csv_path,
                dtype=dtype,
                encoding=encoding,
                low_memory=False,
                **read_kwargs,
            )
        except Exception as e:
            if logger:
                logger.warning(
                    "%sread_csv_preferred: csv read failed for %s (%s); "
                    "falling back to xlsx",
                    log_tag, csv_path.name, e,
                )

    if not xlsx_path.exists() or not xlsx_path.is_file():
        if logger:
            logger.warning(
                "%sread_csv_preferred: xlsx not found: %s", log_tag, xlsx_path,
            )
        return None

    try:
        df = pd.read_excel(
            xlsx_path,
            sheet_name=sheet_name,
            dtype=dtype,
            **read_kwargs,
        )
    except Exception as e:
        if logger:
            logger.warning(
                "%sread_csv_preferred: xlsx read failed for %s: %s",
                log_tag, xlsx_path.name, e,
            )
        return None

    if convert_on_fallback:
        convert_xlsx_to_csv(
            xlsx_path,
            sheet_name=sheet_name,
            csv_path=csv_path,
            encoding=encoding,
            logger=logger,
            log_tag=log_tag,
        )

    return df


def read_build_csv(
    csv_path: Path | str,
    *,
    dtype: Any = None,
    code: Optional[str] = None,
    **read_kwargs: Any,
) -> Optional[pd.DataFrame]:
    """Build-side canonical CSV read — CSV ONLY, never xlsx.

    The downloads conversion guarantees every scanned source file has its
    .csv companion written canonically; a build must therefore NEVER read
    xlsx (that was a leftover legacy fallback). A missing/empty CSV is a
    DOWNLOADS BUG — fix the downloader, not the build.

    Parameters
    ----------
    csv_path : Path | str
        Canonical csv file path (read as-is).
    dtype : optional
        Column dtype overrides forwarded to read_csv.
    code : str, optional
        When set, byte-prefilter to that code's lines BEFORE parsing
        (see read_csv_code_filtered_bytes) so single-code builds never
        parse whole-market snapshots.
    **read_kwargs
        Forwarded to pd.read_csv (no ``encoding`` kwarg is passed by
        callers — that would force a cudf CPU fallback on every file).

    Returns
    -------
    Optional[pd.DataFrame]
        Parsed frame, an EMPTY frame when the code pre-filter found zero
        matching lines, or None when the CSV is missing/too small/failed
        to parse (caller counts it like any other unreadable source).
    """
    path = Path(csv_path)
    if not path.exists() or not path.is_file():
        return None
    try:
        if path.stat().st_size < 64:
            return None
    except OSError:
        return None

    if code is not None:
        buf = read_csv_code_filtered_bytes(path, code)
        if buf is not None:
            if isinstance(buf, pd.DataFrame):
                return buf
            # compression=None: buffer is our own uncompressed bytes —
            # silences cudf's per-parse AUTO-detection warning. A parse
            # failure here is NOT silently absorbed into a full read:
            # that would mask a broken loader and silently 100x the I/O.
            return _strip_bom_columns(
                pd.read_csv(buf, dtype=dtype, compression=None,
                            **read_kwargs))

    df = pd.read_csv(path, dtype=dtype, **read_kwargs)
    return _strip_bom_columns(df)
