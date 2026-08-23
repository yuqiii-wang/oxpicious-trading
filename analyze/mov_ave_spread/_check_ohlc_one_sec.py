"""One-off check: run the mov_ave_spreads_detail_ohlc anchor computation
for ONE real security (longest-history code of SEC_TYPE) and verify:

  A. Code semantics on the freshly computed output:
       high_Wd     == CLOSE          at high_date_Wd     (top-high anchor)
       low_Wd      == CLOSE          at low_date_Wd      (top-low anchor)
       high_2nd_Wd == INTRADAY HIGH  at high_2nd_date_Wd (2nd-high anchor)
       low_2nd_Wd  == INTRADAY LOW   at low_2nd_date_Wd  (2nd-low anchor)
  B. DB agreement: freshly computed columns vs the rows already stored in
     analysis.mov_ave_spreads_detail_ohlc for that code (PK: sec_type,
     code, date) — mismatches indicate stale rows written by an older
     pipeline version (the incremental repair check only catches rows
     with NULL date columns, not value-semantics drift).

Run:
  python -m analyze.mov_ave_spread._check_ohlc_one_sec
"""
from __future__ import annotations

import asyncio
import os
import sys

# Ensure project root is on sys.path when run via python -m.
sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import numpy as np
import pandas as pd

from _common.build_commons import get_db_connection_async
from analyze.mov_ave_spread.config import OHLC_TABLE, OHLC_WINDOWS
from analyze.mov_ave_spread.ohlc import compute_ohlc_columns

SEC_TYPE = "index"

# Source fetch per sec_type — mirrors analyze.mov_ave_spread.fetch
# (_fetch_sql_for_sec_type), restricted to ONE code, keeping only the
# columns the OHLC step needs (price/close, open, high, low).
SOURCE_SQL = {
    "index": """
        SELECT b.code, b.date, b.close AS price, b.open, b.high, b.low
        FROM stats.index_identity i
        JOIN stats.index_basic_stats b ON b.date = i.date AND b.code = i.code
        WHERE b.code = $1
        ORDER BY b.date ASC
    """,
    "etf": """
        SELECT b.code, b.date,
               COALESCE(a.adj_close, b.close) AS price,
               COALESCE(a.adj_open,  b.open)  AS open,
               COALESCE(a.adj_high,  b.high)  AS high,
               COALESCE(a.adj_low,   b.low)   AS low
        FROM stats.etf_identity i
        JOIN stats.etf_basic_stats b ON b.date = i.date AND b.code = i.code
        LEFT JOIN stats.etf_adjustment a ON a.date = i.date AND a.code = i.code
        WHERE b.code = $1
        ORDER BY b.date ASC
    """,
    "stock": """
        SELECT b.code, b.date, b.close AS price, b.open, b.high, b.low
        FROM stats.stock_identity i
        JOIN stats.stock_basic_stats b ON b.date = i.date AND b.code = i.code
        WHERE b.code = $1 AND b.close IS NOT NULL
        ORDER BY b.date ASC
    """,
}

# anchor tag -> source array whose value the column must carry.
ANCHOR_SRC = {
    "high": "price",      # top-high anchor value = CLOSE
    "low": "price",       # top-low anchor value  = CLOSE
    "high_2nd": "high",   # 2nd-high anchor value = INTRADAY HIGH
    "low_2nd": "low",     # 2nd-low anchor value  = INTRADAY LOW
}

TAGS = tuple(ANCHOR_SRC)


async def main() -> None:
    conn = await get_db_connection_async()
    try:
        # ---- pick the longest-history code for this sec_type ----------
        r = await conn.fetchrow(
            f"SELECT code, COUNT(*) AS n FROM {OHLC_TABLE} "
            f"WHERE sec_type = $1 GROUP BY code ORDER BY n DESC LIMIT 1",
            SEC_TYPE,
        )
        if r is None:
            print(f"no {SEC_TYPE} rows in {OHLC_TABLE}; nothing to check")
            return
        code: str = r["code"]
        print(f"checking sec_type={SEC_TYPE} code={code} "
              f"({int(r['n']):,} existing ohlc rows)")

        # ---- fetch the same source columns the pipeline uses ----------
        rows = await conn.fetch(SOURCE_SQL[SEC_TYPE], code)
        src = pd.DataFrame([dict(x) for x in rows])
        src["sec_type"] = SEC_TYPE
        src["date"] = pd.to_datetime(src["date"]).dt.date
        for c in ("price", "open", "high", "low"):
            src[c] = pd.to_numeric(src[c], errors="coerce")
        print(f"source rows: {len(src):,} "
              f"({src['date'].min()} -> {src['date'].max()})")

        # ---- run the production computation on the full history -------
        out = compute_ohlc_columns(src.copy())

        # ---- [A] semantic checks (vectorized per anchor column) -------
        n = len(out)
        src_of = {
            "price": out["price"].to_numpy(dtype=np.float64),
            "high": out["high"].to_numpy(dtype=np.float64),
            "low": out["low"].to_numpy(dtype=np.float64),
        }
        date_to_pos = pd.Series(
            np.arange(n, dtype=np.int64), index=pd.DatetimeIndex(out["date"])
        )

        sem_fail: list[str] = []
        sem_examples: list[str] = []
        print("\n[A] semantic checks on freshly computed columns "
              "(anchor value vs source value at anchor date):")
        for w in OHLC_WINDOWS:
            parts: list[str] = []
            for tag in TAGS:
                apos = out[f"{tag}_date_{w}d"].map(date_to_pos)
                m = apos.notna().to_numpy()
                idx = apos[m].to_numpy(dtype=np.int64)
                expect = src_of[ANCHOR_SRC[tag]][idx]
                got = out.loc[m, f"{tag}_{w}d"].to_numpy(dtype=np.float64)
                both = ~np.isnan(got) & ~np.isnan(expect)
                nan_pair = np.isnan(got) & np.isnan(expect)
                mis = both & ~np.isclose(got, expect, rtol=0, atol=1e-9)
                # "bad" = value differs (both non-NaN) OR one side NaN
                bad = int(np.sum(mis) + np.sum(~np.isnan(got) & np.isnan(expect))
                          + np.sum(np.isnan(got) & ~np.isnan(expect)))
                parts.append(
                    f"{tag}:{int(m.sum())}nn/{bad}bad"
                    f"({int(np.sum(nan_pair))}nan-pair)"
                )
                if bad:
                    sem_fail.append(
                        f"w={w}d {tag}: {bad} value mismatches "
                        f"({int(np.sum(mis))} real-diff, "
                        f"{int(np.sum(nan_pair))} both-NaN)"
                    )
                    bad_rows = np.flatnonzero(m)[
                        mis | (np.isnan(got) != np.isnan(expect))
                    ]
                    for i in bad_rows[:2]:
                        anchor_pos = int(date_to_pos[out[f"{tag}_date_{w}d"].iloc[i]])
                        sem_examples.append(
                            f"  row_date={out['date'].iloc[i]} {tag}_{w}d: "
                            f"got={out[f'{tag}_{w}d'].iloc[i]!r} "
                            f"anchor_date={out[f'{tag}_date_{w}d'].iloc[i]!r} "
                            f"expect(src[{anchor_pos}])="
                            f"{src_of[ANCHOR_SRC[tag]][anchor_pos]!r}"
                        )
                if nan_pair.any() and len(sem_examples) < 16:
                    for i in np.flatnonzero(m)[nan_pair][:3]:
                        anchor_pos = int(date_to_pos[out[f"{tag}_date_{w}d"].iloc[i]])
                        sem_examples.append(
                            f"  NAN-PAIR row_date={out['date'].iloc[i]} "
                            f"{tag}_{w}d: got={out[f'{tag}_{w}d'].iloc[i]!r} "
                            f"anchor_date={out[f'{tag}_date_{w}d'].iloc[i]!r} "
                            f"src[{anchor_pos}].{ANCHOR_SRC[tag]}="
                            f"{src_of[ANCHOR_SRC[tag]][anchor_pos]!r} "
                            f"src[{anchor_pos}].price="
                            f"{src_of['price'][anchor_pos]!r}"
                        )
            print(f"  {w:>4}d  " + "  ".join(parts))
        if sem_fail:
            print("  SEMANTIC FAILURES:")
            for f in sem_fail:
                print("   -", f)
            for e in sem_examples[:12]:
                print(e)
        else:
            print("  -> OK: top anchors = CLOSE, 2nd anchors = intraday "
                  "HIGH/LOW on their anchor dates, for all 7 windows")

        # ---- [B] compare against the DB rows --------------------------
        print("\n[B] DB agreement (freshly computed vs stored rows):")
        db_rows = await conn.fetch(
            f"SELECT * FROM {OHLC_TABLE} "
            f"WHERE sec_type = $1 AND code = $2 ORDER BY date ASC",
            SEC_TYPE, code,
        )
        if not db_rows:
            print("  -> no DB rows for this code")
            return
        db = pd.DataFrame([dict(x) for x in db_rows])
        db["date"] = pd.to_datetime(db["date"]).dt.date
        print(f"db rows: {len(db):,} "
              f"({db['date'].min()} -> {db['date'].max()})")

        src_dates = set(src["date"])
        db_dates = set(db["date"])
        print(f"dates in source missing from DB: {len(src_dates - db_dates)}; "
              f"DB dates absent from source: {len(db_dates - src_dates)}")

        val_cols = [f"{tag}_{w}d" for w in OHLC_WINDOWS for tag in TAGS]
        date_cols = [f"{tag}_date_{w}d" for w in OHLC_WINDOWS for tag in TAGS]
        keep = ["date"] + val_cols + date_cols
        merged = out[keep].merge(db[keep], on="date", suffixes=("_py", "_db"))

        n_val_mis = 0
        n_date_mis = 0
        n_only_py = 0
        n_only_db = 0
        examples: list[str] = []
        for w in OHLC_WINDOWS:
            parts = []
            for tag in TAGS:
                vcol = f"{tag}_{w}d"
                a = pd.to_numeric(merged[f"{vcol}_py"], errors="coerce")
                b = pd.to_numeric(merged[f"{vcol}_db"], errors="coerce")
                both = a.notna() & b.notna()
                mis = both & ~np.isclose(
                    a.to_numpy(dtype=np.float64),
                    b.to_numpy(dtype=np.float64),
                    rtol=0, atol=1e-6,
                )
                dcol = f"{tag}_date_{w}d"
                da = pd.to_datetime(merged[f"{dcol}_py"])
                dd = pd.to_datetime(merged[f"{dcol}_db"])
                dmis = (da.notna() & dd.notna() & (da != dd)).to_numpy()
                only_py = int((a.notna() & b.isna()).sum())
                only_db = int((a.isna() & b.notna()).sum())
                n_val_mis += int(mis.sum())
                n_date_mis += int(dmis.sum())
                n_only_py += only_py
                n_only_db += only_db
                parts.append(f"{tag}:{int(mis.sum())}v/{int(dmis.sum())}d"
                             f"/+{only_py}/-{only_db}")
                if (mis.sum() or dmis.sum()) and len(examples) < 8:
                    for i in np.flatnonzero(mis.to_numpy() | dmis)[:2]:
                        examples.append(
                            f"  {merged['date'].iloc[i]} {vcol}: "
                            f"py={a.iloc[i]!r} db={b.iloc[i]!r} "
                            f"py_date={da.iloc[i]!r} db_date={dd.iloc[i]!r}"
                        )
            print(f"  {w:>4}d  " + "  ".join(parts))
        print(f"  totals: value mismatches={n_val_mis}, "
              f"date mismatches={n_date_mis}, "
              f"py-only (DB NULL)={n_only_py}, "
              f"db-only (py NULL)={n_only_db}")
        if examples:
            print("  examples:")
            for e in examples:
                print(e)
        if n_val_mis == 0 and n_date_mis == 0 and n_only_py == 0 and n_only_db == 0:
            print("  -> OK: DB rows match the fresh computation exactly")
        else:
            print("  -> MISMATCH: stored rows differ from the current "
                  "code semantics — these rows are stale and need "
                  "recompute (delete + rerun)")
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    asyncio.run(main())
