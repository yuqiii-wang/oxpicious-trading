"""build_exchange_trading_amt step — populates stats.exchange_trading_amt.

stats.exchange_trading_amt (per-(date, exchange) aggregate trading turnover):
  Each exchange is proxied by ONE hardcoded representative broad-market
  index whose stats.index_basic_stats.trading_amount is taken as that
  exchange's total_trading_amount on each date:

    SZ (SZSE) -> 399001  (深证成指)
    SS (SSE)  -> 000001  (上证指数)

  Exchange labels follow the stats.sec_classification.exchange convention
  (SZ/SS/...), NOT the verbose SZSE/SSE names, so the table joins cleanly
  to sec_classification.

  One row per (date, exchange) where the representative index has a
  NON-NULL trading_amount. Rows where the representative index has no
  trading_amount (estimated-close gap fills in index_basic_stats) are
  skipped — they carry no meaningful turnover.

  PK is (date, exchange); missing-data detection is therefore per
  (date, exchange) via find_missing_keys, so an exchange with a later
  data start (e.g. 399001 begins 2020-01-02 vs 000001 on 2020-01-01) is
  tracked independently rather than masked by the other exchange.

Incremental mode (default): only (date, exchange) pairs present in the
representative indices' index_basic_stats but missing from
stats.exchange_trading_amt are (re)computed and upserted.

--date mode (forced_date set): the missing-pair skip is bypassed — the
forced date is recomputed even when already present (existing rows are
refreshed through the upsert write path; no truncation, no deletes). The
forced date must exist in the representative indices' index_basic_stats
(with a NON-NULL trading_amount), else the run exits(1) (forced_date_scope).

Force mode (force=True): truncate the table first, then full recompute.
"""
import datetime
from typing import Optional

import pandas as pd

from _common.build_commons import (
    copy_or_upsert_split_async,
    truncate_table_async,
    find_missing_keys,
    forced_date_scope,
    rec_col,
    rec_cols,
)
from builds._commons.row_emission import records_from_frame

TABLE = "stats.exchange_trading_amt"

# Hardcoded exchange -> representative broad-market index mapping.
# Per build directive: SZSE represented by 399001 (深证成指), SSE by 000001
# (上证指数). total_trading_amount is taken directly from that index's
# stats.index_basic_stats.trading_amount on each date. Exchange codes match
# the stats.sec_classification.exchange convention (SZ/SS), NOT SZSE/SSE.
EXCHANGE_INDEX_MAP = [
    ("SZ", "399001"),
    ("SS", "000001"),
]


async def build_exchange_trading_amt(conn, force: bool = False,
                                     forced_date: Optional[datetime.date] = None) -> None:
    """Populate stats.exchange_trading_amt.

    Incremental when force=False (missing (date, exchange) pairs only);
    full recompute when force=True (truncate first). With forced_date
    (--date mode), only that date is recomputed — the missing-pair skip is
    bypassed (existing rows are refreshed via the upsert path) and no
    truncation happens.
    """
    # ---- Step 1: fetch the full desired row set -------------------
    # Pull (date, exchange, index_code, total_trading_amount) for every
    # date where the representative index has a NON-NULL trading_amount.
    # Skipping NULL-amount rows keeps the table clean (no meaningless
    # turnover rows for estimated-close gaps) and makes the missing-key
    # set match exactly what gets inserted.
    print("\n[EXCH_AMT] Fetching representative-index trading_amounts "
          "from stats.index_basic_stats...", flush=True)
    sql_rows = """
        SELECT ec.exchange, ibs.date, ec.index_code,
               ibs.trading_amount AS total_trading_amount
        FROM (VALUES ('SZ', '399001'), ('SS', '000001'))
             AS ec(exchange, index_code)
        JOIN stats.index_basic_stats ibs
            ON ibs.code = ec.index_code
        WHERE ibs.trading_amount IS NOT NULL
        ORDER BY ec.exchange, ibs.date
    """
    rows = await conn.fetch(sql_rows)
    # Whole-column frame — all downstream filtering/emission is vectorized
    df_all = pd.DataFrame(rec_cols(rows))
    print(f"    -> {len(df_all):,} desired (date, exchange) rows across "
          f"{df_all['exchange'].nunique()} exchanges", flush=True)

    # ---- Step 2: detect missing pairs or truncate -----------------
    if force:
        print(f"\n[EXCH_AMT] Force mode: truncating {TABLE}...", flush=True)
        await truncate_table_async(conn, TABLE)
        target_df = df_all
    elif forced_date is not None:
        # --date mode: bypass the missing-pair skip — the forced date is
        # ALWAYS recomputed (existing rows refreshed via the upsert path);
        # exits(1) when the date has no representative-index data.
        target = forced_date_scope(
            set(rec_col(rows, "date")), forced_date,
            source_label="stats.index_basic_stats dates",
        )
        print(f"\n[EXCH_AMT] [DATE MODE] forcing recompute of {forced_date} "
              f"(missing-pair skip bypassed)", flush=True)
        target_df = df_all[df_all["date"].isin(target)].reset_index(drop=True)
    else:
        print(f"\n[EXCH_AMT] Detecting missing (date, exchange) pairs...",
              flush=True)
        source_keys = set(zip(rec_col(rows, "date"), rec_col(rows, "exchange")))
        missing_keys = await find_missing_keys(
            conn, TABLE, ["date", "exchange"], source_keys
        )
        print(f"    -> {len(missing_keys)} of {len(source_keys)} "
              f"(date, exchange) pairs missing from {TABLE}", flush=True)
        if not missing_keys:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return
        # Vectorized membership filter via composite string keys (never a
        # per-row tuple-in-set scan)
        missing_set = {f"{d}|{e}" for d, e in missing_keys}
        composite = df_all["date"].astype(str) + "|" + df_all["exchange"].astype(str)
        target_df = df_all[composite.isin(missing_set)].reset_index(drop=True)

    # ---- Step 3: upsert ------------------------------------------
    print(f"\n[EXCH_AMT] Upserting into {TABLE}...", flush=True)
    EMIT_COLS = ["date", "exchange", "index_code", "total_trading_amount"]
    if len(target_df) == 0:
        print("    -> no data to insert.", flush=True)
    else:
        data = records_from_frame(target_df, EMIT_COLS)
        n_copied, n_upserted = await copy_or_upsert_split_async(
            conn, TABLE, data, key_columns=["date", "exchange"],
        )
        total = n_copied + n_upserted
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
              f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
              "upsert"
        print(f"    -> upserted {total:,} rows via {via}", flush=True)
