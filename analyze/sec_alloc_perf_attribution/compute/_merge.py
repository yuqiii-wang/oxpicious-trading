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
def build_weights_frame(shared_weights: dict) -> pd.DataFrame:
    """Build the (code, benchmark_code) -> weights lookup frame ONCE.

    ``Series.map`` with a python UDF never compiles on GPU (one
    [cudf fallback] per call); a left hash-merge on (code,
    benchmark_code) is cudf-native.
    """
    rows = [
        (sc, bc, cw, bw)
        for (sc, bc), (cw, bw) in shared_weights.items()
    ]
    return pd.DataFrame(
        rows,
        columns=["code", "benchmark_code",
                 "code_sec_shared_weight", "benchmark_sec_shared_weight"],
    )


def attach_shared_weights(
    merged: pd.DataFrame,
    weights_frame: pd.DataFrame,
    subject_code: str,
) -> pd.DataFrame:
    """Attach code_sec_shared_weight + benchmark_sec_shared_weight columns.

    ``weights_frame`` comes from build_weights_frame(shared_weights) —
    built once per run, merged here per subject on (code,
    benchmark_code).  Shared weights come from the latest composition
    snapshot — same for all dates.  Absent pairs become NaN (NULL),
    zero-overlap pairs stay explicit (0, 0) — matching the old
    Series.map semantics exactly.
    """
    sub_wt = weights_frame[weights_frame["code"] == subject_code][
        ["benchmark_code", "code_sec_shared_weight",
         "benchmark_sec_shared_weight"]
    ]
    merged = merged.drop(
        columns=["code_sec_shared_weight",
                 "benchmark_sec_shared_weight"], errors="ignore"
    )
    merged = merged.merge(sub_wt, on="benchmark_code", how="left")
    return merged
