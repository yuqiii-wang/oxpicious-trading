"""ETF PE aggregation via harmonic weighting by composition.

PE is a RATIO, not an additive quantity. The financially correct
aggregation from constituent stock PE weighted by market-cap weight is
the HARMONIC mean:

    PE_etf = SUM(w_i) / SUM(w_i / PE_i)

This sums earnings (linear, correct) rather than PE ratios. Loss-making
constituents (NULL PE in stock_basic_stats) are excluded from both
numerator and denominator.

Uses the cuDF router (should_use_gpu) for the merge + groupby-agg
steps, which operate on ~20M+ rows (constituents × dates) — well above
the GPU breakeven threshold.
"""
import re

import numpy as np
import pandas as pd

from _common.df_utils import should_use_gpu

# Regex to strip exchange suffixes for cross-table joins.
_SUFFIX_RE = re.compile(r"\.(SS|SZ|SH|BJ|HK)$")


def _strip_suffix(code: str) -> str:
    if not code:
        return code
    return _SUFFIX_RE.sub("", str(code).upper())


def _normalize_codes(df: pd.DataFrame, col: str) -> None:
    """Strip exchange suffix from a code column in-place."""
    if col in df.columns:
        df[col] = df[col].apply(_strip_suffix)


async def fetch_stock_pe(conn, stock_codes=None, dates=None):
    """Fetch per-(date, code) PE from stats.stock_basic_stats.

    Args:
        stock_codes: optional list of bare 6-digit stock codes (no suffix).
            If None, fetches all (large — ~6.8M rows).
        dates: optional list of datetime.date to filter. If None, all dates.

    Returns DataFrame with columns: date, code (bare, no suffix), pe.
    """
    conditions = ["pe IS NOT NULL", "pe > 0"]
    params = []

    if stock_codes is not None and stock_codes:
        conditions.append(
            "REGEXP_REPLACE(code, '\\.(SS|SZ|SH|BJ|HK)$', '') = ANY($1::text[])"
        )
        params.append(sorted(stock_codes))

    if dates is not None and dates:
        params.append(sorted(dates))
        conditions.append(f"date = ANY(${len(params)}::date[])")

    where = " AND ".join(conditions)
    rows = await conn.fetch(f"""
        SELECT date, code, pe
        FROM stats.stock_basic_stats
        WHERE {where}
        ORDER BY code, date ASC
    """, *params)

    if not rows:
        return pd.DataFrame(columns=["date", "code", "pe"])

    df = pd.DataFrame([dict(r) for r in rows])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["pe"] = pd.to_numeric(df["pe"], errors="coerce")
    _normalize_codes(df, "code")
    df = df.dropna(subset=["pe"])
    df = df[df["pe"] > 0]
    return df


def compute_etf_pe_harmonic(
    etf_dates_df: pd.DataFrame,
    composition_df: pd.DataFrame,
    stock_pe_df: pd.DataFrame,
    verbose: bool = True,
) -> pd.DataFrame:
    """Compute harmonic-weighted PE per (etf_code, date).

    PE_etf = SUM(w_i) / SUM(w_i / PE_i)

    Uses the latest composition snapshot (temporal extrapolation — same
    snapshot for all dates, mirroring the index dividend_yield pattern).

    Args:
        etf_dates_df: DataFrame with columns code (etf code with suffix),
            date. Defines the (etf, date) pairs to compute PE for.
        composition_df: DataFrame with columns etf_code, stock_code,
            weight_fraction. Latest snapshot per ETF.
        stock_pe_df: DataFrame with columns date, code (bare stock code),
            pe. Per-date stock PE.
        verbose: print progress + cuDF router decisions.

    Returns:
        DataFrame with columns: code (etf code with suffix), date, pe.
    """
    if etf_dates_df.empty or composition_df.empty or stock_pe_df.empty:
        if verbose:
            print("    [ETF-PE] empty input — skipping PE aggregation", flush=True)
        return pd.DataFrame(columns=["code", "date", "pe"])

    # Prepare: strip suffixes from etf_codes in etf_dates_df and composition.
    # Keep dates as datetime64 (NOT .dt.date — Python date objects break cuDF).
    etf_dates = etf_dates_df[["code", "date"]].copy()
    etf_dates["etf_code_bare"] = etf_dates["code"].apply(_strip_suffix)
    etf_dates["date"] = pd.to_datetime(etf_dates["date"])

    comp = composition_df.copy()
    comp["etf_code_bare"] = comp["etf_code"].apply(_strip_suffix)
    comp["stock_code_bare"] = comp["stock_code"].apply(_strip_suffix)
    comp = comp[["etf_code_bare", "stock_code_bare", "weight_fraction"]]

    stock_pe = stock_pe_df[["date", "code", "pe"]].copy()
    stock_pe["date"] = pd.to_datetime(stock_pe["date"])

    # Step 1: cross-join composition with the dates that need PE.
    # Result: one row per (etf, constituent, date) — the Cartesian product
    # of composition pairs × dates. This is the large intermediate (~20M rows)
    # that benefits from GPU.
    unique_etf_dates = etf_dates[["etf_code_bare", "date"]].drop_duplicates()

    if verbose:
        print(f"    [ETF-PE] {len(comp):,} (etf, stock) composition pairs, "
              f"{len(unique_etf_dates):,} (etf, date) pairs, "
              f"{len(stock_pe):,} stock PE rows", flush=True)

    # Merge composition with etf_dates to get (etf, stock, date) triples
    # then merge with stock_pe on (stock_code, date) to get PE per constituent.
    # The merge is the compute-intensive step — use cuDF if worthwhile.
    merge_left = comp.merge(unique_etf_dates, on="etf_code_bare", how="inner")

    if verbose:
        print(f"    [ETF-PE] {len(merge_left):,} (etf, stock, date) triples "
              f"before stock PE join", flush=True)

    # The merge with stock_pe is the heavy operation
    if should_use_gpu(merge_left, op_type="merge"):
        print(f"    [cuDF router] {len(merge_left):,} rows — merge (GPU-worthy)", flush=True)

    merged = merge_left.merge(
        stock_pe,
        left_on=["stock_code_bare", "date"],
        right_on=["code", "date"],
        how="inner",
    )
    merged["w_over_pe"] = merged["weight_fraction"] / merged["pe"]
    result = merged.groupby(
        ["etf_code_bare", "date"], sort=False
    ).agg(
        sum_w=("weight_fraction", "sum"),
        sum_w_over_pe=("w_over_pe", "sum"),
    ).reset_index()
    if verbose:
        print(f"    [ETF-PE] pandas merge+groupby done: {len(result):,} (etf, date) PE values",
              flush=True)

    # PE_etf = SUM(w) / SUM(w/pe)
    result["pe"] = np.where(
        result["sum_w_over_pe"] > 0,
        result["sum_w"] / result["sum_w_over_pe"],
        np.nan,
    )

    # Map back to full etf codes with suffixes
    code_map = etf_dates[["code", "etf_code_bare"]].drop_duplicates("etf_code_bare")
    result = result.merge(code_map, on="etf_code_bare", how="inner")
    result = result[["code", "date", "pe"]]

    if verbose:
        n_non_null = result["pe"].notna().sum()
        print(f"    [ETF-PE] {n_non_null:,} non-null PE values computed "
              f"(out of {len(result):,} etf-date pairs)", flush=True)

    return result


def extract_latest_composition(comp_long: pd.DataFrame) -> pd.DataFrame:
    """Extract the latest composition snapshot per ETF from comp_long.

    Used when the ETF build has comp_long in-memory (from build_composition)
    and hasn't inserted it into the DB yet. Falls back to querying the DB
    via fetch_latest_etf_composition otherwise.

    Returns DataFrame with columns: etf_code, stock_code, weight_fraction.
    """
    if comp_long is None or comp_long.empty:
        return pd.DataFrame(columns=["etf_code", "stock_code", "weight_fraction"])

    # Filter to equity holdings only (cash_sub_flag != "必须")
    comp_eq = comp_long[comp_long["cash_sub_flag"] != "必须"].copy()
    if comp_eq.empty:
        return pd.DataFrame(columns=["etf_code", "stock_code", "weight_fraction"])

    comp_eq["_shares"] = pd.to_numeric(comp_eq["shares"], errors="coerce").fillna(0.0)
    comp_eq["_w"] = comp_eq["_shares"].abs()

    # For each ETF, take the latest snapshot date
    latest_dates = comp_eq.groupby("etf_code")["trade_date"].max()
    comp_latest = comp_eq.merge(
        latest_dates.rename("_latest_date").reset_index(),
        left_on="etf_code", right_on="etf_code", how="inner",
    )
    comp_latest = comp_latest[comp_latest["trade_date"] == comp_latest["_latest_date"]]

    # Compute weight_fraction = shares / total_shares_per_etf
    totals = comp_latest.groupby("etf_code")["_w"].sum()
    comp_latest = comp_latest.merge(
        totals.rename("_total").reset_index(),
        on="etf_code", how="left",
    )
    comp_latest["weight_fraction"] = np.where(
        comp_latest["_total"] > 0,
        comp_latest["_w"] / comp_latest["_total"],
        0.0,
    )

    # Filter to valid stock codes
    comp_latest["stock_code_bare"] = comp_latest["stock_code"].apply(
        lambda s: str(s).split(".")[0].zfill(6) if s else ""
    )
    comp_latest = comp_latest[
        comp_latest["stock_code_bare"].str.match(r"^\d{6}$")
    ]

    return comp_latest[["etf_code", "stock_code", "weight_fraction"]].rename(
        columns={"etf_code": "etf_code"}
    )
