"""Extract all SZSE index codes available in the downloaded daily xlsx exports.

Scans ``temps/szse_trend/szse_trend_index_*.xlsx`` (the tab7 daily trend
export containing ~177 indices per day) and ``temps/szse_archive/szse_index_*.xlsx``
(the archive endpoint export) and aggregates every unique
``(index_code, index_name)`` pair across all dated files.

Output CSV: ``temps/szse_index_codes.csv`` with columns:
    index_code, index_name, first_date, last_date, n_files, sources

This is a discovery/reference script — it tells us which SZSE index codes
are available in the daily exports so the SZSE downloader's
``INDEX_CODES_TO_KEEP`` filter and the csindex ``CSINDEX_SKIP_CODES``
exclusion list can be kept in sync with what SZSE actually publishes.

Run from WSL:
    python -m temp_scripts._extract_szse_index_codes
    # or directly:
    python temp_scripts/_extract_szse_index_codes.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd

# Resolve project root regardless of CWD
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPS_DIR = _PROJECT_ROOT / "temps"

# xlsx file sources: (subdir, glob_prefix, source_label)
SOURCES = [
    (TEMPS_DIR / "szse_trend",   "szse_trend_index_", "trend"),
    (TEMPS_DIR / "szse_archive", "szse_index_",       "archive"),
]

DATE_RE = re.compile(r"(\d{8})\.(?:xlsx|xls)$", re.IGNORECASE)


def _extract_date_from_name(path: Path) -> str:
    m = DATE_RE.search(path.name)
    return m.group(1) if m else ""


def _scan_dir(src_dir: Path, prefix: str, source_label: str,
              agg: Dict[str, dict]) -> int:
    """Scan one directory for matching xlsx files and merge into *agg*.

    Returns the number of files successfully parsed.
    """
    if not src_dir.is_dir():
        print(f"[skip] directory not found: {src_dir}")
        return 0

    files = sorted(src_dir.glob(f"{prefix}*.xlsx"))
    # Exclude 0-byte markers and obvious non-data files
    files = [f for f in files if f.stat().st_size >= 1024]
    print(f"[scan] {source_label}: {len(files)} xlsx files in {src_dir}")

    n_parsed = 0
    for f in files:
        try:
            df = pd.read_excel(f, sheet_name=0, dtype=object, engine="openpyxl")
        except Exception as e:
            print(f"  [warn] failed to read {f.name}: {e}")
            continue

        if df is None or df.empty:
            continue

        # Locate the code and name columns by substring match (headers are
        # Chinese: 指数代码 / 指数简称). Some archive files may use 证券代码.
        code_col = None
        name_col = None
        for col in df.columns:
            s = str(col)
            if code_col is None and ("指数代码" in s or "证券代码" in s):
                code_col = col
            if name_col is None and ("指数简称" in s or "证券简称" in s):
                name_col = col
        if code_col is None:
            print(f"  [warn] no index code column in {f.name}: {list(df.columns)}")
            continue

        file_date = _extract_date_from_name(f)

        for _, row in df.iterrows():
            raw_code = str(row[code_col]).strip() if pd.notna(row[code_col]) else ""
            # Normalize to 6-digit bare code
            code = raw_code.replace(".SZ", "").replace(".sz", "").strip()
            if not code or not code.isdigit():
                continue
            code = code.zfill(6)
            if len(code) != 6:
                continue
            name = str(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else ""

            entry = agg.get(code)
            if entry is None:
                agg[code] = {
                    "index_code": code,
                    "index_name": name,
                    "first_date": file_date,
                    "last_date": file_date,
                    "n_files": 1,
                    "sources": source_label,
                }
            else:
                entry["n_files"] += 1
                if file_date:
                    if (not entry["first_date"]) or file_date < entry["first_date"]:
                        entry["first_date"] = file_date
                    if (not entry["last_date"]) or file_date > entry["last_date"]:
                        entry["last_date"] = file_date
                if source_label not in entry["sources"]:
                    entry["sources"] = entry["sources"] + "," + source_label
                # Prefer a non-empty name
                if not entry["index_name"] and name:
                    entry["index_name"] = name
        n_parsed += 1

    print(f"  [done] {source_label}: parsed {n_parsed} files")
    return n_parsed


def main() -> int:
    agg: Dict[str, dict] = {}
    total_files = 0
    for src_dir, prefix, label in SOURCES:
        total_files += _scan_dir(src_dir, prefix, label, agg)

    if not agg:
        print("[error] no index codes extracted — check temps/ dirs exist")
        return 1

    rows = sorted(agg.values(), key=lambda r: r["index_code"])
    out_df = pd.DataFrame(rows, columns=[
        "index_code", "index_name", "first_date", "last_date", "n_files", "sources",
    ])

    out_path = TEMPS_DIR / "szse_index_codes.csv"
    out_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print()
    print(f"[ok] wrote {len(out_df)} unique SZSE index codes -> {out_path}")
    print(f"     scanned {total_files} xlsx files total")
    print()
    # Highlight the codes the user asked about
    for target in ("399348", "399346", "399001", "399006", "399237"):
        hit = out_df[out_df["index_code"] == target]
        if not hit.empty:
            r = hit.iloc[0]
            print(f"  {r['index_code']}  {r['index_name']:<12}  "
                  f"first={r['first_date']} last={r['last_date']} "
                  f"files={r['n_files']} src={r['sources']}")
        else:
            print(f"  {target}  NOT FOUND in any xlsx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
