"""Step 4 — merge OHLCV+margin, corp-action adjustment + moving averages."""
from __future__ import annotations

import datetime

import pandas as pd

from _common.build_commons import rec_col
from _common.df_utils import compute_moving_averages, compute_emas, safe_columns

from builds.etf.split_adjustment import apply_split_adjustment
from builds.etf.composition import build_composition

MARGIN_VALUE_COLS: list = [
    "rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty",
    "rq_balance_amt", "total_balance",
]


def prepare_features(
    ohlcv_df: pd.DataFrame,
    margin_df: pd.DataFrame,
    adj_seeds: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Merge OHLCV + margin and compute adjustment/MA columns.

    Needs FULL per-code history — the caller must pass the combined
    historical+new frame. When the DB-history fetch was window-truncated
    (B3 trailing-window fetch), *adj_seeds* carries each code's stored
    cum_split_factor / cum_dividend_per_share from the row immediately
    before the window so corp-action adjustment stays continuous with the
    pre-window history (cum factors are forward products — seeding the
    first row reproduces full-history values exactly).
    """
    print("\n[4/7] Merging OHLCV + margin, applying corp-action adjustment + MAs …", flush=True)
    if len(margin_df):
        merged = ohlcv_df.merge(margin_df, on=["date", "code"], how="left", validate="m:1")
    else:
        merged = ohlcv_df.copy()
        for c in MARGIN_VALUE_COLS:
            merged[c] = 0.0
    for c in MARGIN_VALUE_COLS:
        if c in safe_columns(merged):
            # margin columns are float64 at every source (CSV dtype maps +
            # SQL ::float8 casts) — only the left-join NaNs need filling
            merged[c] = merged[c].fillna(0.0)

    _fill_sse_rq_balance_amt(merged)

    # Corp-action adjustment then grouped rolling MAs / EMAs
    # apply_split_adjustment() guarantees [code,date] sort + contiguous
    # index on return (sort+reset inside) — no re-sort needed here.
    merged = apply_split_adjustment(merged, verbose=True, adj_seeds=adj_seeds)
    compute_moving_averages(
        merged, group_key="code", value_col="adj_close",
        windows=[5, 20, 60, 120, 255],
    )
    compute_emas(
        merged, group_key="code", value_col="adj_close",
        spans=[6, 10, 20, 60, 120, 255],
    )
    print(f"    → MA columns added: ma5, ma5_ratio, ma20, ma60, ma120, ma255; "
          f"EMA columns added: ema6, ema10, ema20, ema60, ema120, ema255", flush=True)
    return merged


def _fill_sse_rq_balance_amt(merged: pd.DataFrame) -> None:
    """Compute rq_balance_amt in place for SSE ETF rows (quantity × mid price).

    Whole-column ``where`` replacement — no boolean .loc row addressing.
    """
    cols = safe_columns(merged)
    if not ("rq_balance_qty" in cols and "rq_balance_amt" in cols):
        return
    missing_rq_amt = (
        merged["exchange"].eq("SS")
        & (merged["rq_balance_amt"] == 0)
        & (merged["rq_balance_qty"] > 0)
    )
    if missing_rq_amt.any():
        mid_price = (merged["open"] + merged["close"]) / 2.0
        implied = merged["rq_balance_qty"] * mid_price
        merged["rq_balance_amt"] = merged["rq_balance_amt"].where(
            ~missing_rq_amt, implied)
        print(f"    → Filled rq_balance_amt for {int(missing_rq_amt.sum()):,} SSE ETF rows", flush=True)


async def load_composition(
    conn, code_filter: str | None, force: bool,
    forced_date: datetime.date | None = None,
) -> tuple:
    """Build composition frames, restricted to the target code when set.

    GATE-FIRST (B1): the (code, snapshot_date) pairs already in
    stats.sec_composition are fetched BEFORE any CSV is read, and files
    whose snapshot is already stored are never opened (a nightly run then
    parses 1-2 files instead of 16,299 — ~172s → ~0s). Consequence: the
    returned comp_long only covers NEW snapshots, so
    build_pe_scope unions comp_latest with the stored latest composition
    from the DB for the codes not parsed this run. comp_universe likewise
    covers only parsed codes — build_universe's composition columns
    (n_comp_dates / n_holdings_latest) degrade to 0 for the rest (display/
    console-only columns; sec_classification quality metrics don't use
    them).

    ``forced_date`` (--date mode) lifts the DB gate for that one snapshot
    date: its CSVs are re-read and its snapshots re-upserted by
    insert_composition (no deletes).

    Composition CSVs carry whole suffixed ``etf_code`` — the data filter
    compares codes directly.  Filenames end with the BARE code
    (``szse_etf_comp_YYYYMMDD_<code>.csv``); only that file-glob needs the
    suffix stripped (filename convention, not data).

    Returns: (comp_long, comp_universe).
    """
    existing_ymd_keys: set | None = None
    if not force:
        if code_filter:
            comp_existing_rows = await conn.fetch(
                "SELECT code, snapshot_date FROM stats.sec_composition "
                "WHERE source_type = 'etf' AND code = $1",
                code_filter,
            )
        else:
            comp_existing_rows = await conn.fetch(
                "SELECT code, snapshot_date FROM stats.sec_composition "
                "WHERE source_type = 'etf'"
            )
        # "bare6|YYYYMMDD" composite keys — matches _filename_comp_key;
        # whole-column pairing (rec_col), never per-row iteration.
        existing_ymd_keys = {
            str(c).split(".")[0] + "|" + f"{d:%Y%m%d}"
            for c, d in zip(rec_col(comp_existing_rows, "code"),
                            rec_col(comp_existing_rows, "snapshot_date"))
        }
        print(f"    [COMP] {len(existing_ymd_keys):,} existing (code, snapshot_date) "
              f"pairs in stats.sec_composition", flush=True)
        if forced_date is not None:
            # --date refresh: lift the gate for the forced snapshot date
            # only, so its composition CSVs are re-read + re-upserted.
            forced_ymd = f"|{forced_date:%Y%m%d}"
            n_gated = len(existing_ymd_keys)
            existing_ymd_keys = {
                k for k in existing_ymd_keys if not k.endswith(forced_ymd)}
            print(f"    [DATE MODE] Composition gate lifted for snapshot "
                  f"{forced_date}: {n_gated - len(existing_ymd_keys)} stored "
                  f"pair(s) re-readable", flush=True)

    print("\n    Building composition …", flush=True)
    comp_long, comp_universe = build_composition(
        verbose=True, code=code_filter, existing_ymd_keys=existing_ymd_keys)

    if code_filter and comp_long is not None and len(comp_long) > 0:
        comp_long = comp_long[
            comp_long["etf_code"].astype(str).str.strip().eq(code_filter)
        ].reset_index(drop=True)
        print(f"    [CODE FILTER] Composition rows restricted to {code_filter}: {len(comp_long):,}", flush=True)
    return comp_long, comp_universe
