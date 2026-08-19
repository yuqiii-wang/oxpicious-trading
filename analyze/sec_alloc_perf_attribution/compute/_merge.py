"""Step 3 (merge subject with benchmarks) and Step 4 (attach shared weights)."""
from __future__ import annotations

from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
#  Step 3: inner-join one subject's closes with all benchmarks on date
# ---------------------------------------------------------------------------
def merge_subject_with_benchmarks(
    subject_closes: pd.DataFrame,
    index_closes: pd.DataFrame,
    sec_type: str,
) -> Optional[pd.DataFrame]:
    """Inner-merge one subject's closes with all benchmarks on date.

    Returns None when the merge is empty (no shared dates or, for index
    subjects, only the self-pair exists). For sec_type='index', the
    self-pair (code == benchmark_code) is excluded.
    """
    subject_codes = subject_closes["code"].unique()
    if len(subject_codes) != 1:
        raise ValueError(
            f"merge_subject_with_benchmarks expects exactly one subject, "
            f"got {len(subject_codes)}"
        )
    subject_code = subject_codes[0]
    sub = subject_closes[subject_closes["code"] == subject_code].copy()
    merged = sub.merge(index_closes, on="date", how="inner")
    if merged.empty:
        return None

    if sec_type == "index":
        merged = merged[merged["code"] != merged["benchmark_code"]]
        if merged.empty:
            return None

    merged["sec_type"] = sec_type
    return merged


# ---------------------------------------------------------------------------
#  Step 4: vectorized lookup of precomputed (subject, benchmark) overlap weights
# ---------------------------------------------------------------------------
def attach_shared_weights(
    merged: pd.DataFrame,
    shared_weights: dict,
    subject_code: str,
) -> pd.DataFrame:
    """Attach code_sec_shared_weight + benchmark_sec_shared_weight columns.

    ``shared_weights`` is a dict from fetch_shared_weights():
        {(subject_code, benchmark_code): (code_wt, bench_wt)}

    Lookup is vectorized via ``Series.map`` returning a tuple per row,
    then split into two columns. Shared weights come from the latest
    composition snapshot — same for all dates.
    """
    def _lookup_wt(benchmark_code):
        pair = shared_weights.get((subject_code, benchmark_code))
        return pair if pair is not None else (None, None)

    wt = merged["benchmark_code"].map(_lookup_wt)
    merged["code_sec_shared_weight"] = [w[0] for w in wt]
    merged["benchmark_sec_shared_weight"] = [w[1] for w in wt]
    return merged
