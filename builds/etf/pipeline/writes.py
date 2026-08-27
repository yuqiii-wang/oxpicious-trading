"""Step 6 — filter to write-candidate rows and upsert the 5 split tables."""
from __future__ import annotations

import numpy as np
import pandas as pd

from _common.build_commons import copy_or_upsert_split_async
from _common.df_utils import safe_columns
from builds._commons.row_emission import dates_as_date_list, records_from_frame


def filter_missing_rows(
    merged: pd.DataFrame,
    *,
    exists: np.ndarray,
    split_mask: np.ndarray,
    in_range: np.ndarray,
    pe_null_hit: np.ndarray,
) -> tuple[pd.DataFrame, int]:
    """Keep = missing ∪ corp-resync ∪ PE-backfill rows (∩ date range).

    PE-null rows are kept only when the incremental PE computation actually
    produced a value.  Dates stay datetime64 here — Python dates are
    produced at the emission boundary in write_split_tables (an
    object-date column poisons every upstream GPU op).

    Returns: (merged_missing, n_resync_codes).
    """
    print("\n[6/7] Filtering to missing (date, code) pairs and inserting …", flush=True)

    hit = pd.Series(split_mask, index=merged.index)
    n_resync_codes = int(merged["code"][hit].nunique())
    if n_resync_codes:
        print(f"    [CORP-RESYNC] {n_resync_codes} codes with corp-action "
              f"events — re-upserting ALL their rows (not just missing)", flush=True)
    if pe_null_hit.any():
        print(f"    [PE-BACKFILL] {int(pe_null_hit.sum()):,} existing rows with NULL PE "
              f"— re-upserting those that got a value", flush=True)

    keep = (~exists | split_mask) & in_range
    keep |= pe_null_hit & merged["pe"].notna().to_numpy() & in_range

    n_total = len(merged)
    out = merged[keep].reset_index(drop=True)
    out = out.copy()
    # NOTE: date stays datetime64 here — object-date columns poison every
    # subsequent GPU op (each access = one MixedTypeError CPU fallback).
    # Python dates are produced at the emission boundary in
    # write_split_tables via a single numpy transfer.
    print(f"    [DB] {len(out):,} rows to upsert "
          f"(out of {n_total:,} total, missing + corp-action resync)", flush=True)
    return out, n_resync_codes


def _compute_eps_vec(close: pd.Series, pe: pd.Series) -> pd.Series:
    """Float-native EPS with NaN on miss — nan_to_none in records_from_frame
    converts NaN→None at emission (no object column on the frame)."""
    mask = close.notna() & pe.notna() & (pe > 0)
    return (close.astype(float) / pe.astype(float)).where(mask).round(6)


async def write_split_tables(conn, merged_missing: pd.DataFrame, force: bool) -> None:
    """Build per-table row lists and COPY/upsert into the 5 split tables."""
    if len(merged_missing) == 0 and not force:
        print("    [INFO] etf_identity is up to date — no new OHLCV/margin rows to insert", flush=True)
        return

    src = merged_missing.drop_duplicates(subset=["date", "code"], keep="last").copy()
    src["code"] = src["code"].astype(str)

    # The frame keeps datetime64 dates throughout — an object-date column
    # poisons every subsequent cudf op (one MixedTypeError CPU fallback
    # per access). Python dates are emitted as a parallel list and zipped
    # into the row dicts by _emit_rows below.
    date_l = dates_as_date_list(src["date"])

    def _emit_rows(cols: list[str]) -> list[dict]:
        recs = records_from_frame(src, [c for c in cols if c != "date"])
        return [{"date": d, **r} for d, r in zip(date_l, recs)]

    # exchange comes straight from the canonical CSV column carried through
    # the whole pipeline (never re-derived from code suffixes). Downloads
    # data is trusted: an unexpected value is a pipeline bug -> hard fail.
    _bad_ex = ~src["exchange"].isin(["SZ", "SS", "SH"])
    if bool(_bad_ex.any()):
        _ex_val = str(src["exchange"][_bad_ex].iloc[0])
        raise ValueError(
            f"[ETF-WRITE] unexpected exchange={_ex_val!r} in canonical CSV "
            f"({int(_bad_ex.sum())} rows) — downloads conversion is wrong"
        )

    # identity + basic + tech + adjustment + liquidity row batches.
    # Columns stay float/str/datetime64 on the frame — the former
    # NaN→None pre-transforms created object columns that poisoned every
    # subsequent cudf op; records_from_frame already sweeps NaN→None at
    # emission (pure host-side).
    src["name"] = src["name"].fillna("").astype(str)
    identity_rows = _emit_rows(["date", "code", "exchange", "name"])

    cols = safe_columns(src)
    basic_cols = ["prev_close", "open", "high", "low", "close", "pct_change"]
    src["eps"] = _compute_eps_vec(src["close"], src["pe"].astype(float))
    if "is_close_estimated" in cols:
        src["is_close_estimated"] = src["is_close_estimated"].fillna(False).astype(bool)
    else:
        src["is_close_estimated"] = False
    basic_rows = _emit_rows(
        ["date", "code"] + [c for c in basic_cols if c in cols]
        + ["pe", "eps", "is_close_estimated"])

    tech_cols = ["ma5", "ma5_ratio", "ma20", "ma60", "ma120", "ma255",
                 "ema6", "ema10", "ema20", "ema60", "ema120", "ema255"]
    tech_rows = _emit_rows(
        ["date", "code"] + [c for c in tech_cols if c in cols])

    adj_cols = ["cum_split_factor", "is_split_event_day", "action_type",
                "implied_dividend_per_share", "cum_dividend_per_share",
                "adj_prev_close", "adj_open", "adj_high", "adj_low", "adj_close"]
    adj_rows = _emit_rows(
        ["date", "code"] + [c for c in adj_cols if c in cols])

    liq_cols = ["trading_shares", "trading_amount", "rz_buy", "rz_balance",
                "rq_sell_qty", "rq_balance_qty", "rq_balance_amt", "total_balance"]
    for c in liq_cols:
        if c in cols:
            src[c] = src[c].fillna(0.0)  # semantic: missing margin = 0
    liq_rows = _emit_rows(
        ["date", "code"] + [c for c in liq_cols if c in cols])

    pk_cols = ["date", "code"]
    split_tables = [
        ("stats.etf_identity",         identity_rows),
        ("stats.etf_basic_stats",       basic_rows),
        ("stats.etf_tech_stats",       tech_rows),
        ("stats.etf_adjustment",        adj_rows),
        ("stats.etf_liquidity_margin",  liq_rows),
    ]
    for tbl, rows in split_tables:
        if rows:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, tbl, rows, pk_cols)
            total = n_copied + n_upserted
            via = "COPY" if n_copied > 0 and n_upserted == 0 else \
                  f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else "upsert"
            print(f"    [DB] Inserted {total:,} rows into {tbl} via {via}", flush=True)
        else:
            print(f"    [DB] No new rows to insert into {tbl}", flush=True)
