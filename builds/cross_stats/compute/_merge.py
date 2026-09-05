"""Pair-grain merge: subject x benchmarks join + shared-weight attach."""
from __future__ import annotations

from typing import Optional

import pandas as pd


def merge_subject_with_benchmarks(
    subject_closes: pd.DataFrame,
    index_closes: pd.DataFrame,
    sec_type: str,
) -> Optional[pd.DataFrame]:
    """Inner-merge one subject's closes with all benchmarks on date.

    None when the merge is empty (no shared dates or, for sec_type=
    'index', only the self-pair exists — self-pairs excluded).
    """
    subject_codes = subject_closes["code"].unique()
    if len(subject_codes) != 1:
        raise ValueError(
            f"merge_subject_with_benchmarks expects exactly one subject, "
            f"got {len(subject_codes)}"
        )
    subject_code = subject_codes[0]
    sub = subject_closes[subject_closes["code"] == subject_code]
    merged = sub.merge(index_closes, on="date", how="inner")
    if merged.empty:
        return None

    if sec_type == "index":
        merged = merged[merged["code"] != merged["benchmark_code"]]
        if merged.empty:
            return None

    merged["sec_type"] = sec_type
    return merged


def build_weights_frame(shared_weights: dict) -> pd.DataFrame:
    """(code, benchmark_code) -> weights lookup frame, built ONCE.

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
    """Attach code_sec_shared_weight + benchmark_sec_shared_weight.

    Absent pairs → NaN (NULL); zero-overlap pairs stay explicit (0, 0) —
    matching the weights dict semantics exactly.
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
