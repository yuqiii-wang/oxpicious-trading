"""Step 7 — sec_composition insert (missing (code, snapshot_date) pairs only)."""
from __future__ import annotations

import datetime

from typing import Optional

import numpy as np
import pandas as pd

from _common.build_commons import copy_or_upsert_split_async, rec_col
from builds._commons.row_emission import dates_as_date_list

# Canonical suffixed ETF code ("NNNNNN.SZ"/"NNNNNN.SS") — composition CSVs
# are written with whole suffixed etf_code by the download conversion.
VALID_ETF_RE = r"^\d{6}\.(SS|SZ)$"
# Canonical suffixed constituent code — stock_identity/stock_basic_stats all
# use suffixed codes, so sec_composition.stock_code must carry the suffix too.
VALID_STOCK_RE = r"^\d{6}\.(SS|SZ|BJ|HK|SH)$"


async def insert_composition(
    conn,
    comp_long: Optional[pd.DataFrame],
    code_filter: str | None,
    forced_date: datetime.date | None = None,
) -> None:
    """Build ETF holdings rows from comp_long and upsert missing snapshots.

    Codes arrive canonical and suffixed in the CSVs — read whole; rows
    failing validation are dropped. ``forced_date`` (--date mode) keeps
    that snapshot date's rows as write candidates even when already stored
    (upsert overwrites; no deletes).
    """
    print("\n[7/7] Inserting ETF composition data (missing snapshots only) …", flush=True)

    if code_filter:
        comp_existing_rows = await conn.fetch(
            "SELECT DISTINCT code, snapshot_date FROM stats.sec_composition WHERE code = $1",
            code_filter,
        )
    else:
        comp_existing_rows = await conn.fetch(
            "SELECT DISTINCT code, snapshot_date FROM stats.sec_composition"
        )
    # Composite string keys via whole-column zip pairing → vectorized isin
    existing_comp_str_keys = [
        c + "|" + f"{d:%Y-%m-%d}"
        for c, d in zip(rec_col(comp_existing_rows, "code"),
                        rec_col(comp_existing_rows, "snapshot_date"))]
    print(f"    [DB] {len(existing_comp_str_keys):,} existing (code, snapshot_date) pairs in stats.sec_composition", flush=True)

    holdings_rows = []

    if comp_long is not None and len(comp_long) > 0:
        comp_eq = comp_long[comp_long["cash_sub_flag"] != "必须"].copy()
        if len(comp_eq) > 0:
            # shares read as float64 by the composition dtype map (one-pass)
            comp_eq["_shares"] = comp_eq["shares"].astype(float).fillna(0.0)
            comp_eq["_w"] = comp_eq["_shares"].abs()
            # Valid stock codes only (vectorized). CSVs carry SUFFIXED
            # stock_code — keep whole (no stripping); the suffix is required
            # for joins against stock_identity / stock_basic_stats.
            comp_eq["stock_code"] = comp_eq["stock_code"].astype(str).str.strip()
            comp_eq = comp_eq[
                comp_eq["stock_code"].str.match(VALID_STOCK_RE)
            ]
            # Group weight totals; skip all-cash/zero-weight snapshots
            gk = ["etf_code", "trade_date"]
            comp_eq["_total_w"] = comp_eq.groupby(gk)["_w"].transform("sum")
            comp_eq = comp_eq[comp_eq["_total_w"] > 0]
            # trade_date is already datetime64 (build_composition parsed it
            # and dropped NaT) — to_datetime(errors="coerce") on a Series is
            # a cudf fallback (non-scalar arg)
            comp_eq = comp_eq[comp_eq["trade_date"].notna()]
            # Codes arrive canonical + suffixed — validate whole, keep as-is.
            comp_eq = comp_eq[
                comp_eq["etf_code"].astype(str).str.strip().str.match(VALID_ETF_RE)
            ]
            comp_eq["code"] = comp_eq["etf_code"].astype(str).str.strip()
            # Rank holdings within each (etf, snapshot) by weight desc
            comp_eq = comp_eq.sort_values(
                gk + ["_w"], ascending=[True, True, False], kind="mergesort",
            ).reset_index(drop=True)
            comp_eq["rank"] = comp_eq.groupby(gk).cumcount() + 1
            comp_eq["weight_pct"] = comp_eq["_w"] / comp_eq["_total_w"] * 100.0
            comp_eq["stock_name"] = comp_eq["stock_name"].fillna("").astype(str)
            # Drop snapshots already in the DB — vectorized string-key build:
            # whole-column concat (no zip of python lists).
            snap_keys = comp_eq["code"].astype(str) + "|" + \
                comp_eq["trade_date"].dt.strftime("%Y-%m-%d")
            stale = snap_keys.isin(existing_comp_str_keys)
            if forced_date is not None:
                # --date refresh: the forced snapshot date is re-upserted
                # even when already stored (upsert overwrites, no deletes).
                stale = stale & (
                    comp_eq["trade_date"] != pd.Timestamp(forced_date))
            comp_eq = comp_eq[~stale].reset_index(drop=True)

            n_etfs_kept = int(comp_eq["code"].nunique()) if len(comp_eq) else 0
            if len(comp_eq) > 0:
                # Zip-dict assembly (no to_dict — cudf.pandas fallback per row)
                snap_l = dates_as_date_list(comp_eq["trade_date"])
                code_l = np.asarray(comp_eq["code"], dtype=object).tolist()
                rank_l = np.asarray(comp_eq["rank"]).tolist()
                sc_l = np.asarray(comp_eq["stock_code"], dtype=object).tolist()
                sn_l = np.asarray(comp_eq["stock_name"]).tolist()
                wp_l = np.asarray(comp_eq["weight_pct"]).tolist()
                holdings_rows.extend({
                    "snapshot_date": s, "code": c, "source_type": "etf",
                    "rank": rk, "stock_code": sc, "stock_name": str(sn),
                    "weight_pct": w,
                }
                    for s, c, rk, sc, sn, w
                    in zip(snap_l, code_l, rank_l, sc_l, sn_l, wp_l)
                )
            print(f"    [DB] Built {len(holdings_rows):,} sec_composition rows (full comp) "
                  f"from {n_etfs_kept} ETFs (skipped existing)", flush=True)

    if holdings_rows:
        n_copied, n_upserted = await copy_or_upsert_split_async(
            conn, "stats.sec_composition", holdings_rows,
            ["code", "snapshot_date", "rank"],
            date_column="snapshot_date",
        )
        total = n_copied + n_upserted
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
              f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else "upsert"
        print(f"    [DB] Inserted {total:,} rows into stats.sec_composition via {via}", flush=True)
    else:
        print("    [DB] No new rows to insert into stats.sec_composition", flush=True)
