"""Post-step 7 — per-ETF quality metrics upsert into sec_classification."""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.build_commons import bulk_upsert_async, rec_cols
from _common.df_utils import safe_columns
from builds._commons.row_emission import records_from_frame


async def upsert_quality_metrics(conn, uni_df: pd.DataFrame, merged: pd.DataFrame) -> None:
    """Compute + upsert type='etf' quality rows (classification cols preserved
    on conflict via the (code, parent_index_code) key)."""
    # Avg trading volume per code — groupby → merge into the output frame
    # (vectorized; never a host dict hop).
    avg_vol = None
    if "trading_shares" in safe_columns(merged):
        avg_vol = merged.groupby("code", as_index=False)["trading_shares"].mean()

    # Existing PKs from sec_classification drive parent_index_code — also a
    # merge.  A code should own one 'etf' classification row; dedupe on it.
    existing_pk_rows = await conn.fetch(
        "SELECT DISTINCT ON (code) code, parent_index_code "
        "FROM stats.sec_classification WHERE type = 'etf' ORDER BY code"
    )
    parent_df = pd.DataFrame(rec_cols(existing_pk_rows))

    codes_mask = uni_df["code"].str.len() > 0
    out = pd.DataFrame({
        "code": uni_df["code"][codes_mask],
        "name": uni_df["name"][codes_mask],
        "first_date": uni_df["first_date"][codes_mask],
        "last_date": uni_df["last_date"][codes_mask],
        "n_days": uni_df["n_ohlcv_days"][codes_mask].astype(int),
        "has_margin": uni_df["n_margin_days"][codes_mask] > 0,
    }).reset_index(drop=True)
    if out.empty:
        print("    [DB] No ETF quality rows to insert into stats.sec_classification", flush=True)
        return

    if avg_vol is not None and len(avg_vol):
        out = out.merge(
            avg_vol.rename(columns={"trading_shares": "avg_shares"}),
            on="code", how="left",
        )
    else:
        out["avg_shares"] = pd.Series(dtype="float64")
    out["avg_shares"] = out["avg_shares"].fillna(0.0).astype(float)

    if len(parent_df):
        out = out.merge(parent_df, on="code", how="left")
    if "parent_index_code" not in safe_columns(out):
        out["parent_index_code"] = ""
    else:
        out["parent_index_code"] = out["parent_index_code"].fillna("").astype(str)
    out["type"] = "etf"
    # DB boundary only: ISO date strings → Python date objects. Series
    # .dt.date is a cudf fallback and poisons the frame — one numpy
    # transfer per column instead, injected at emission.
    first_l = np.asarray(
        pd.to_datetime(out["first_date"], format="%Y-%m-%d"),
        dtype="datetime64[D]").astype(object).tolist()
    last_l = np.asarray(
        pd.to_datetime(out["last_date"], format="%Y-%m-%d"),
        dtype="datetime64[D]").astype(object).tolist()
    out = out.drop(columns=["first_date", "last_date"])

    quality_rows = records_from_frame(out, [
        "code", "name", "type", "parent_index_code", "n_days",
        "has_margin", "avg_shares",
    ])
    quality_rows = [
        {**r, "first_date": fd, "last_date": ld}
        for r, fd, ld in zip(quality_rows, first_l, last_l)
    ]
    if quality_rows:
        inserted = await bulk_upsert_async(
            conn, "stats.sec_classification", quality_rows,
            ["code", "parent_index_code"],
        )
        print(f"    [DB] Upserted {inserted:,} ETF quality rows into stats.sec_classification", flush=True)
    else:
        print("    [DB] No ETF quality rows to insert into stats.sec_classification", flush=True)
