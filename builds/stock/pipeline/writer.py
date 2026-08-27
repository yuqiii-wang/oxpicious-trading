"""Row construction and DB writes for identity / basic_stats /
liquidity_margin / estimated-PE batches."""
from __future__ import annotations

from dataclasses import dataclass
import math
from operator import itemgetter

import numpy as np
import pandas as pd

from _common.build_commons import copy_or_upsert_split_async
from builds.stock._helpers import (
    _nan_to_none,
    _safe_columns,
    _to_db_series,
    dates_as_date_list,
    records_from_frame,
)


# ============================================================================
# stats.stock_identity
# ============================================================================
def build_identity_rows(
    combined_db: pd.DataFrame,
    etf_membership: dict,
) -> tuple[list[dict], int]:
    """Build stats.stock_identity rows; returns (rows, n_flagged_in_etf)."""
    _id_df = combined_db[["date", "code", "name"]].copy()
    _id_df["code"] = _id_df["code"].astype(str)
    # exchange + board come straight from the canonical CSV ``exchange`` /
    # ``board`` columns carried by every loader. Downloads data is trusted:
    # any missing or unexpected value means the download/conversion layer
    # produced an invalid row -> hard fail, never paper over it here.
    _ex = combined_db["exchange"].astype(str)
    _bad_ex = ~_ex.isin(["SZ", "SS", "BJ"])
    if bool(_bad_ex.any()):
        _n = int(_bad_ex.sum())
        _first_code = str(combined_db["code"][_bad_ex].iloc[0])
        raise ValueError(
            f"[STOCK-ID] unexpected exchange={_ex[_bad_ex].iloc[0]!r} "
            f"(code {_first_code}, {_n} rows) — downloads conversion is wrong"
        )
    _id_df["exchange"] = _ex
    _cols = _safe_columns(combined_db)
    if "board" not in _cols:
        raise ValueError(
            "[STOCK-ID] canonical pipeline lost the `board` column — "
            "loader bug, fix the loader instead of deriving a fallback"
        )
    _board = combined_db["board"].astype(str)
    _bad_board = (_board == "") | (_board == "None") | (_board == "nan")
    if bool(_bad_board.any()):
        _n = int(_bad_board.sum())
        _first_code = str(combined_db["code"][_bad_board].iloc[0])
        raise ValueError(
            f"[STOCK-ID] blank board for code {_first_code} ({_n} rows) — "
            "downloads must always emit exchange/board per row"
        )
    _id_df["board"] = _board
    _id_df["name"] = _id_df["name"].where(_id_df["name"].notna(), "")
    _id_df["name"] = _id_df["name"].astype(str)

    _etf_rows: list[tuple] = []
    for _d, _codes in etf_membership.items():
        _etf_rows.extend((_d, _c) for _c in _codes)
    if _etf_rows:
        # NO DataFrame construction from raw (date, code) tuples: under
        # cudf.pandas the proxied constructor walks every python-date
        # object attempting the GPU fast path — a ~14s stall for
        # full-market batches (measured: single __init__ call).
        # Instead flag membership via one vectorized isin over composite
        # string keys (date.isoformat()|code) on the host frame.
        _etf_keys: set[str] = {
            f"{_d.isoformat()}|{_c}" for _d, _c in _etf_rows
        }
        _keys = combined_db["date"].astype(str) + "|" + _id_df["code"]
        _id_df["is_in_index_or_etf"] = _keys.isin(_etf_keys)
    else:
        _id_df["is_in_index_or_etf"] = False
    _id_df["is_in_index_or_etf"] = _id_df["is_in_index_or_etf"].fillna(False).astype(bool)
    n_in_etf = int(_id_df["is_in_index_or_etf"].sum())
    identity_rows = _id_df[["date", "code", "exchange", "board", "name", "is_in_index_or_etf"]].to_dict(orient="records")
    return identity_rows, n_in_etf


async def write_identity(conn, identity_rows: list[dict]) -> None:
    """Upsert identity rows into stats.stock_identity."""
    if identity_rows:
        n_copied, n_upserted = await copy_or_upsert_split_async(
            conn, "stats.stock_identity", identity_rows, ["date", "code"]
        )
        total_inserted = n_copied + n_upserted
        if n_copied > 0 and n_upserted == 0:
            print(f"    [DB] Inserted {total_inserted:,} rows into "
                  f"stats.stock_identity via COPY (all new dates)",
                  flush=True)
        elif n_copied > 0:
            print(f"    [DB] Inserted {total_inserted:,} rows into "
                  f"stats.stock_identity ({n_copied:,} copied + "
                  f"{n_upserted:,} upserted)", flush=True)
        else:
            print(f"    [DB] Inserted {total_inserted:,} rows into "
                  f"stats.stock_identity via upsert (all historical)",
                  flush=True)
    else:
        print(f"    [DB] No new rows to insert into stats.stock_identity", flush=True)


# ============================================================================
# Row-list construction (basic_stats actual-PE + liquidity_margin + margin-only)
# ============================================================================
@dataclass
class InsertRows:
    """All row lists needed for the decoupled DB writes.

    basic_stats ownership split: OHLCV columns are owned exclusively by the
    OHLCV pass (write_basic_stats_ohlcv), PE/eps/is_pe_estimated by the PE
    passes (write_pe_only_conn) — neither can clobber the other."""
    ov_rows: list[dict]              # OHLCV-scoped basic_stats rows (NO pe/eps)
    snapshot_pe_rows: list[dict]     # rows whose pe came from daily snapshots
    liq_rows: list[dict]             # liquidity_margin rows (trading_* only)
    n_actual: int                    # rows with snapshot-derived pe
    n_missing: int                   # rows without any pe


def build_insert_rows(
    combined_db: pd.DataFrame,
) -> InsertRows:
    """Split combined rows into the decoupled DB-insert row lists.

    Liquidity rows are column-scoped (date/code/trading_shares/trading_amount
    only): margin columns are owned exclusively by the independent margin pass
    (upsert_margin_only_conn), never written here — so a COPY of new OHLCV
    rows cannot clobber real margin values with defaults, and the two passes
    stay order-independent. Same principle now applies to PE: the combined
    frame carries snapshot-derived 市盈率 only as SEPARATE pe rows; SSE PE
    files never enter the combined frame at all (independent pass in main).
    """
    # --- OHLCV-scoped basic_stats rows (never touch pe/eps/is_pe_estimated)
    _ov_cols = ["prev_close", "open", "high", "low", "close", "pct_change"]
    _ov_df = combined_db[["date", "code"] + _ov_cols + ["is_close_estimated"]].copy()
    _ov_df["code"] = _ov_df["code"].astype(str)
    for _c in _ov_cols:
        _ov_df[_c] = _to_db_series(_ov_df[_c])
    _ov_df["is_close_estimated"] = \
        _ov_df["is_close_estimated"].fillna(False).astype(bool)
    date_vals = dates_as_date_list(_ov_df["date"])
    ov_rows = records_from_frame(
        _ov_df.drop(columns=["date"]), ["code"] + _ov_cols + ["is_close_estimated"])
    for d, r in zip(date_vals, ov_rows):
        r["date"] = d

    # --- Snapshot-derived PE rows (separate upsert payload, pe ownership)
    actual_pe_mask = combined_db["pe"].notna()
    n_actual = int(actual_pe_mask.sum())
    n_missing = len(combined_db) - n_actual
    _sp_df = combined_db[["date", "code", "pe"]][actual_pe_mask].copy()
    _sp_df["code"] = _sp_df["code"].astype(str)
    _sp_df["pe"] = _to_db_series(_sp_df["pe"].astype(float))
    sp_date_vals = dates_as_date_list(_sp_df["date"])
    snapshot_pe_rows = records_from_frame(
        _sp_df.drop(columns=["date"]), ["code", "pe"])
    for d, r in zip(sp_date_vals, snapshot_pe_rows):
        r["date"] = d
        r["is_pe_estimated"] = False

    # --- liquidity_margin rows (trading_shares/amount only)
    _liq_cols = ["trading_shares", "trading_amount"]
    _liq_df = combined_db[["date", "code", "close"] + _liq_cols].copy()
    _liq_df["code"] = _liq_df["code"].astype(str)
    _has_close = _liq_df["close"].notna()
    _liq_df = _liq_df[_has_close].drop(columns=["close"])
    for _c in _liq_cols:
        _liq_df[_c] = _to_db_series(_liq_df[_c]).fillna(0)
    liq_dates = dates_as_date_list(_liq_df["date"])
    liq_rows = records_from_frame(
        _liq_df.drop(columns=["date"]), ["code"] + _liq_cols)
    for d, r in zip(liq_dates, liq_rows):
        r["date"] = d

    return InsertRows(
        ov_rows=ov_rows,
        snapshot_pe_rows=snapshot_pe_rows,
        liq_rows=liq_rows,
        n_actual=n_actual,
        n_missing=n_missing,
    )


# ============================================================================
# DB writes: OHLCV-scoped basic_stats + liquidity_margin (parallel) then
# the independent PE passes (snapshot/file/estimated — all via write_pe_only_conn)
# ============================================================================
async def write_basic_stats_ohlcv(
    pool,
    ov_rows: list[dict],
) -> None:
    """Column-scoped OHLCV upsert into stats.stock_basic_stats.

    Touches ONLY prev_close/open/high/low/close/pct_change/is_close_estimated
    on conflict (pe/eps/is_pe_estimated owned by the PE passes are never
    clobbered). Brand-new (code, date) keys insert cleanly; existing rows get
    a subset update. Must run AFTER write_identity (FK to stock_identity).
    """
    async with pool.acquire() as bs_conn:
        if not ov_rows:
            print(f"    [DB] No OHLCV rows to upsert into stats.stock_basic_stats",
                  flush=True)
            return
        cols = ["prev_close", "open", "high", "low", "close", "pct_change",
                "is_close_estimated"]
        tmp_cols_sql = ", ".join(
            f"{c} {'BOOLEAN' if c == 'is_close_estimated' else 'NUMERIC(18,4)'}"
            for c in cols
        )
        select_list = ", ".join(
            f"t.{c}" for c in ["date", "code"] + cols)
        set_list = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        async with bs_conn.transaction():
            await bs_conn.execute(
                f"CREATE TEMP TABLE _bs_ov (date DATE, code TEXT, {tmp_cols_sql})"
            )
            ins_q = (
                "INSERT INTO _bs_ov (date, code, "
                + ", ".join(cols) + ") VALUES ($1, $2, "
                + ", ".join(f"${i}" for i in range(3, 3 + len(cols))) + ")"
            )
            for i in range(0, len(ov_rows), 1000):
                await bs_conn.executemany(
                    ins_q,
                    [tuple(r[c] if c != "date" else r["date"] for c in
                           ["date", "code"] + cols)
                     for r in ov_rows[i:i + 1000]],
                )
            result = await bs_conn.execute(
                f"""
                INSERT INTO stats.stock_basic_stats
                    (date, code, {", ".join(cols)})
                SELECT {select_list}
                FROM _bs_ov t
                ON CONFLICT (code, date) DO UPDATE SET {set_list}
                """
            )
            await bs_conn.execute("DROP TABLE _bs_ov")
        parts = result.split()
        inserted_count = int(parts[-1]) if parts else 0
        print(f"    [DB] Upserted {inserted_count:,} OHLCV rows into "
              f"stats.stock_basic_stats (column-scoped: no pe/eps touch)",
              flush=True)


async def write_pe_only_conn(
    conn,
    pe_rows: list[dict],
    source_label: str = "PE",
) -> None:
    """Column-scoped PE upsert into stats.stock_basic_stats — THE single
    entry point for every PE pass (daily-snapshot pe, SSE PE files,
    estimated PE).

    Touches ONLY pe / eps / is_pe_estimated on conflict; OHLCV columns are
    never written here. eps is recomputed server-side from the stored close
    (close / pe rounded to 6 — mirrors _compute_eps_vec semantics); NULL
    when close is missing or pe <= 0. Self-seeds key-only identity rows so
    PE-only dates that no OHLCV batch has touched still satisfy the FK.
    """
    if not pe_rows:
        return
    # Whole-column extraction from the row dicts (no per-row dict walks)
    dates_c = list(map(itemgetter("date"), pe_rows))
    codes_c = list(map(itemgetter("code"), pe_rows))
    pes_c = list(map(itemgetter("pe"), pe_rows))
    flags_c = list(map(itemgetter("is_pe_estimated"), pe_rows))
    rows = list(zip(dates_c, codes_c, pes_c, map(bool, flags_c)))
    async with conn.transaction():
        await conn.execute(
            "CREATE TEMP TABLE _pe_upsert "
            "(date DATE, code TEXT, pe NUMERIC(18,4), is_pe_estimated BOOLEAN)"
        )
        for i in range(0, len(rows), 1000):
            await conn.executemany(
                "INSERT INTO _pe_upsert (date, code, pe, is_pe_estimated) "
                "VALUES ($1, $2, $3, $4)",
                rows[i:i + 1000],
            )
        # FK self-seed: PE-only (date, code) pairs may exist in no other table
        seed_result = await conn.execute(
            "INSERT INTO stats.stock_identity (date, code) "
            "SELECT DISTINCT t.date, t.code FROM _pe_upsert t "
            "ON CONFLICT (date, code) DO NOTHING"
        )
        n_seeded = int(seed_result.split()[-1]) if seed_result else 0
        result = await conn.execute(
            """
            INSERT INTO stats.stock_basic_stats
                (date, code, pe, eps, is_pe_estimated)
            SELECT t.date, t.code, t.pe,
                   CASE WHEN bs.close IS NOT NULL AND t.pe IS NOT NULL
                             AND t.pe > 0
                        THEN round(bs.close / t.pe, 6) END,
                   t.is_pe_estimated
            FROM _pe_upsert t
            LEFT JOIN stats.stock_basic_stats bs
              ON bs.date = t.date AND bs.code = t.code
            ON CONFLICT (code, date) DO UPDATE SET
              pe = EXCLUDED.pe,
              eps = EXCLUDED.eps,
              is_pe_estimated = EXCLUDED.is_pe_estimated
            """
        )
        await conn.execute("DROP TABLE _pe_upsert")
    parts = result.split()
    n_upserted = int(parts[-1]) if parts else 0
    # Positional whole-column count (flags_c is the 4th extracted column)
    n_flagged = sum(map(bool, flags_c))
    msg = (f"    [DB] Upserted {n_upserted:,} PE rows into "
           f"stats.stock_basic_stats ({source_label}: "
           f"{n_flagged:,} is_pe_estimated=true)")
    if n_seeded:
        msg += f" | seeded {n_seeded:,} key-only identity rows"
    print(msg + "", flush=True)


async def upsert_margin_only_conn(
    conn,
    margin_only_rows: list[dict],
) -> None:
    """Column-scoped margin upsert into stats.stock_liquidity_margin.

    The INDEPENDENT margin pass entry point: touches ONLY the 6 margin
    columns on conflict (trading_shares/amount preserved). Before the
    insert, seeds any missing (date, code) keys into stats.stock_identity
    key-only (server defaults fill name=''/is_in_index_or_etf=false) so
    the FK join cannot drop rows — a later OHLCV batch enriches identity.
    Shares one connection, creates+drops its own temp table in one txn."""
    if not margin_only_rows:
        return
    print(f"    [DB] Preparing {len(margin_only_rows):,} "
          f"margin-only rows for upsert (FK filtering via "
          f"temp table)…", flush=True)
    async with conn.transaction():
        await conn.execute(
            "CREATE TEMP TABLE _margin_upsert ("
            "  date DATE, code TEXT, "
            "  rz_buy NUMERIC(24,4), rz_balance NUMERIC(24,4), "
            "  rq_sell_qty NUMERIC(24,4), "
            "  rq_balance_qty NUMERIC(24,4), "
            "  rq_balance_amt NUMERIC(24,4), "
            "  total_balance NUMERIC(24,4)"
            ") ON COMMIT DROP"
        )
        temp_values = [
            (r["date"], r["code"],
             r["rz_buy"], r["rz_balance"],
             r["rq_sell_qty"], r["rq_balance_qty"],
             r["rq_balance_amt"], r["total_balance"])
            for r in margin_only_rows
        ]
        insert_query = (
            "INSERT INTO _margin_upsert "
            "(date, code, rz_buy, rz_balance, rq_sell_qty, "
            " rq_balance_qty, rq_balance_amt, total_balance) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)"
        )
        for i in range(0, len(temp_values), 1000):
            await conn.executemany(
                insert_query, temp_values[i:i + 1000]
            )
        # Self-seed identity keys (server defaults fill the rest) so the
        # FK INNER JOIN below cannot silently drop margin rows for
        # (date, code) pairs no OHLCV batch has touched yet.
        seed_result = await conn.execute(
            "INSERT INTO stats.stock_identity (date, code) "
            "SELECT DISTINCT t.date, t.code FROM _margin_upsert t "
            "ON CONFLICT (date, code) DO NOTHING"
        )
        n_seeded = int(seed_result.split()[-1]) if seed_result else 0
        if n_seeded > 0:
            print(f"    [DB] Seeded {n_seeded:,} key-only identity rows "
                  f"(name/exchange enriched later by OHLCV batches)",
                  flush=True)
        result = await conn.execute(
            "INSERT INTO stats.stock_liquidity_margin "
            "(date, code, rz_buy, rz_balance, rq_sell_qty, "
            " rq_balance_qty, rq_balance_amt, total_balance) "
            "SELECT t.date, t.code, t.rz_buy, t.rz_balance, "
            "       t.rq_sell_qty, t.rq_balance_qty, "
            "       t.rq_balance_amt, t.total_balance "
            "FROM _margin_upsert t "
            "INNER JOIN stats.stock_identity si "
            "  ON si.date = t.date AND si.code = t.code "
            "ON CONFLICT (date, code) DO UPDATE SET "
            "  rz_buy = EXCLUDED.rz_buy, "
            "  rz_balance = EXCLUDED.rz_balance, "
            "  rq_sell_qty = EXCLUDED.rq_sell_qty, "
            "  rq_balance_qty = EXCLUDED.rq_balance_qty, "
            "  rq_balance_amt = EXCLUDED.rq_balance_amt, "
            "  total_balance = EXCLUDED.total_balance"
        )
        parts = result.split()
        inserted_count = int(parts[-1]) if parts else 0

    n_with_margin = sum(
        1 for r in margin_only_rows
        if (r.get("rz_balance") or 0) > 0
    )
    print(f"    [DB] Upserted {inserted_count:,} margin-only "
          f"rows into stats.stock_liquidity_margin (6 margin "
          f"cols, trading_shares/amount preserved; "
          f"{n_with_margin:,} with non-zero rz_balance)",
          flush=True)


def build_margin_upsert_rows(margin_df: pd.DataFrame) -> list[dict]:
    """All margin rows as column-scoped upsert dicts for the independent
    margin pass (upsert_margin_only_conn): 6 margin cols only, never
    touches trading_shares/amount.

    Emission goes through records_from_frame (one numpy transfer per
    column) — to_dict(orient="records") here produced ~1 cudf fallback
    PER ROW (ndarray.item ValueError, 3,519 lines per market-wide run).
    """
    if hasattr(margin_df, "to_pandas"):  # GPU frame → host at DB boundary
        margin_df = margin_df.to_pandas()
    _margin_cols = ["rz_buy", "rz_balance", "rq_sell_qty", "rq_balance_qty",
                    "rq_balance_amt", "total_balance"]
    out_cols = ["date", "code"] + _margin_cols
    _mdf = margin_df[out_cols].copy()
    _mdf["code"] = _mdf["code"].astype(str)
    for _c in _margin_cols:
        _mdf[_c] = _to_db_series(_mdf[_c]).fillna(0).astype(float)
    # date column stays datetime64; records_from_frame converts each row
    # to a plain value via one numpy array pass — normalize dates to
    # datetime.date objects asyncpg accepts for DATE columns.
    date_vals = dates_as_date_list(_mdf["date"])
    rows = records_from_frame(
        _mdf.drop(columns=["date"]), ["code"] + _margin_cols)
    for d, r in zip(date_vals, rows):
        r["date"] = d
    return rows


async def write_liquidity_margin(
    pool,
    liq_rows: list[dict],
) -> None:
    """Write column-scoped OHLCV liquidity rows (trading_shares/amount
    only — margin cols are owned by the independent margin pass)."""
    async with pool.acquire() as lm_conn:
        if not liq_rows:
            print(f"    [DB] No OHLCV rows to insert into "
                  f"stats.stock_liquidity_margin", flush=True)
            return
        n_copied, n_upserted = await copy_or_upsert_split_async(
            lm_conn, "stats.stock_liquidity_margin", liq_rows, ["date", "code"]
        )
        via = "COPY" if n_copied > 0 and n_upserted == 0 else \
              f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else \
              "upsert"
        print(f"    [DB] Inserted {len(liq_rows):,} liquidity rows into "
              f"stats.stock_liquidity_margin "
              f"(trading_shares/amount only) via {via}", flush=True)


# ============================================================================
# Pass 2: PE passes (snapshot/file payloads + DB-driven estimation)
# ============================================================================
def build_pe_upsert_rows(
    pe_df: pd.DataFrame,
    is_pe_estimated: bool = False,
) -> list[dict]:
    """PE DataFrame ({date, code, pe}) → write_pe_only_conn payload.

    Host-side emission via one numpy transfer per column (never
    itertuples/to_dict — under cudf.pandas each element extraction is a
    slow-path fallback). Rows with NULL/NaN pe are dropped (nothing to
    upsert)."""
    if hasattr(pe_df, "to_pandas"):  # GPU frame → host at DB boundary
        pe_df = pe_df.to_pandas()
    _df = pe_df[["date", "code", "pe"]].copy()
    _df["code"] = _df["code"].astype(str)
    _df["pe"] = _to_db_series(_df["pe"].astype(float))
    _df = _df[_df["pe"].notna()]
    date_vals = dates_as_date_list(_df["date"])
    pes = _nan_to_none(np.asarray(_df["pe"], dtype=float).tolist())
    codes = [str(c) for c in np.asarray(_df["code"]).tolist()]
    return [
        {"date": d, "code": c, "pe": p,
         "is_pe_estimated": bool(is_pe_estimated)}
        for d, c, p in zip(date_vals, codes, pes)
    ]


def build_estimated_pe_rows(
    missing_rows: list[tuple],
    estimated_pe_map: dict,
) -> list[dict]:
    """DB-sourced missing-PE tuples [(date, code, close)] + baseline map →
    estimated-PE payload. Only rows WITH a usable baseline produce a row —
    no-baseline keys simply keep their default (pe NULL,
    is_pe_estimated=false) row created by the OHLCV pass."""
    out: list[dict] = []
    for d, code, _close in missing_rows:
        pe = estimated_pe_map.get((d, code))
        if pe is None:
            continue
        out.append({
            "date": d, "code": code, "pe": float(pe),
            "is_pe_estimated": True,
        })
    return out
