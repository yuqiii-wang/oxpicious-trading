"""Entry point for analyze.options.

Run via ``python -m analyze.options``.

Pipeline:
  0. Populate analysis.options_expiry_identity with distinct
     (date, option_type, underlying_code, expiry_date) tuples.
  1. Compute per-expiry-group rolling skewness (OI-weighted moneyness)
     stats and write to analysis.options_skewness_stats (PK
     (date, option_type, underlying_code, expiry_date), FK ->
     analysis.options_expiry_identity):
       - ``--force``: DELETE all + chunked COPY-insert.
       - default:     chunked upsert (ON CONFLICT DO UPDATE on PK).
       - Includes count_skewness_curve_crossed_spot: cumulative count of
         sign changes in (skewness − 1) per expiry group.
  2. Compute per-expiry-group OI stats and write to
     analysis.options_oi_stats (same PK/FK pattern).
  3. Compute per-expiry-group options wall zones (strength-scored
     zone with lifecycle) and write to analysis.options_walls
     (PK includes wall_type).
  4. Compute per-expiry-group IV skew stats (ATM IV, 25-delta wings,
     risk reversal, smile skewness) into analysis.options_iv_skew_stats
     and the iv_smile skewness rolling stats into
     options_skewness_stats (skew_type='iv_smile').
  5. Compute the greek skew rolling stats — PAIR-level CALL-vs-PUT
     contrasts per industry anchors — into options_skewness_stats with
     skew_type='greek_delta' (delta-weighted put/call ratio),
     'greek_gamma' (GEX-style gamma balance) and 'greek_vega' (OTM-wing
     vega balance). theta/rho have no standard positioning skew and are
     not computed (compute/ package: one module per greek).
"""
from __future__ import annotations


# resource pre-check -- exit early when sys/GPU memory is insufficient
from _common.pre_check import pre_check

pre_check()
import argparse
import asyncio
import os
import sys
import time

# Ensure project root is on sys.path so ``_common`` is importable when run
# directly via ``python -m analyze.options`` or as a script.
sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from _common.build_commons import (  # noqa: E402
    setup_utf8_stdout,
    get_db_connection_async,
    print_build_header,
    print_wall_time,
    add_force_arg,
)
from _common.db_commons import (  # noqa: E402
    copy_or_upsert_split_async,
    copy_insert_async,
)
from _common.df_utils import to_py_dates  # noqa: E402

setup_utf8_stdout()

# cudf.pandas activation — must run before pandas first import
from _common.df_utils._activate import activate
activate()

import pandas as pd  # noqa: E402

from analyze._common import (  # noqa: E402
    sanitize_for_db_insert,
    upsert_analysis_identity,
)
from analyze.options.config import (  # noqa: E402
    SKEWNESS_TABLE_NAME,
    SKEWNESS_ANALYSIS_NAME,
    SKEWNESS_DESCRIPTION,
    SKEWNESS_NUMERIC_COLS,
    SKEWNESS_RESULT_COLUMNS,
    SKEWNESS_PK_COLUMNS,
    SKEW_TYPE_IV_SMILE,
    SKEW_TYPE_MONEYNESS,
    GREEK_NAMES,
    EXPIRY_IDENTITY_TABLE,
    EXPIRY_PK_COLUMNS,
    IV_SKEW_TABLE_NAME,
    IV_SKEW_ANALYSIS_NAME,
    IV_SKEW_DESCRIPTION,
    IV_SKEW_NUMERIC_COLS,
    IV_SKEW_RESULT_COLUMNS,
    OI_TABLE_NAME,
    OI_ANALYSIS_NAME,
    OI_DESCRIPTION,
    OI_NUMERIC_COLS,
    OI_RESULT_COLUMNS,
    WALLS_TABLE_NAME,
    WALLS_ANALYSIS_NAME,
    WALLS_DESCRIPTION,
    WALLS_NUMERIC_COLS,
    WALLS_RESULT_COLUMNS,
)
from analyze.options.fetch import (  # noqa: E402
    fetch_options_skewness_rows,
    fetch_missing_skewness_groups,
    fetch_expiry_identity_rows,
    fetch_options_walls_rows,
    fetch_missing_walls_groups,
    fetch_iv_skew_rows,
    fetch_missing_iv_skew_groups,
)
from analyze.options.compute import (  # noqa: E402
    compute_options_skewness_stats,
    compute_options_walls,
    compute_options_iv_skew_stats,
    compute_options_iv_smile_corr_stats,
    GREEK_SKEW_COMPUTERS,
)


_CHUNK_SIZE = 10000


async def _fk_filter(conn, result_df: pd.DataFrame) -> pd.DataFrame:
    """Anti-join result rows against the options_expiry_identity key set.

    All options_* output tables carry FK (date, option_type,
    underlying_code, expiry_date) -> options_expiry_identity. Source-filter
    drift between the identity pipeline (_SKEWNESS_VALID_WHERE) and the
    per-pipeline fetches (e.g. _IV_SKEW_VALID_WHERE) can emit result rows
    the identity never registered; COPY would crash with
    ForeignKeyViolationError mid-write, so drop them deterministically
    (set membership on the identity key tuples).
    """
    ident_rows = await conn.fetch(
        f"SELECT date, option_type, underlying_code, expiry_date "
        f"FROM {EXPIRY_IDENTITY_TABLE}"
    )
    if not ident_rows:
        return result_df
    ident_keys = set(tuple(r) for r in ident_rows)
    pk_df = result_df[EXPIRY_PK_COLUMNS].copy()
    pk_df = to_py_dates(
        pk_df,
        [c for c in EXPIRY_PK_COLUMNS
         if pd.api.types.is_datetime64_any_dtype(pk_df[c])],
    )
    mask = pd.MultiIndex.from_frame(pk_df).isin(ident_keys)
    n_before = len(result_df)
    result_df = result_df.loc[mask].reset_index(drop=True)
    if len(result_df) != n_before:
        print(f"  FK filter: dropped {n_before - len(result_df):,} of "
              f"{n_before:,} rows not in options_expiry_identity",
              flush=True)
    return result_df


async def _write_rows(
    conn,
    result_df: pd.DataFrame,
    *,
    table_name: str,
    numeric_cols: list[str],
    force: bool,
    target_pairs: set | None,
    pk_columns: list[str],
    force_delete_where: str | None = None,
    round_to: int = 2,
) -> int:
    """Write result rows to a target table.

    - force: DELETE (optionally scoped by force_delete_where) + chunked
      COPY-insert.
    - incremental: filter to target_pairs + chunked upsert.

    Returns:
        Number of rows written.
    """
    if result_df.empty:
        print("  no rows to write", flush=True)
        return 0

    # ---- FK safety: drop rows absent from options_expiry_identity ------
    # All options_* output tables EXCEPT the identity table itself carry
    # FK (underlying_code, date, option_type, expiry_date) ->
    # options_expiry_identity. Source-filter drift between the identity
    # pipeline (_SKEWNESS_VALID_WHERE) and the per-pipeline fetches
    # (e.g. _IV_SKEW_VALID_WHERE) can emit result rows the identity never
    # registered (observed: stale 2026-03-30 expiry ETF contracts still
    # quoted on 2026-08-24..26). COPY would crash with
    # ForeignKeyViolationError mid-write; filter deterministically
    # instead (anti-join against the identity key set).
    #
    # The identity table must NEVER self-anti-join: in --force mode the
    # pipeline deletes identity content BEFORE the write, so a self-join
    # compares fresh rows (possibly new expiry conventions) against the
    # stale pre-delete key set and silently drops almost everything.
    if table_name != EXPIRY_IDENTITY_TABLE:
        result_df = await _fk_filter(conn, result_df)

    if force:
        if force_delete_where:
            print(f"  Deleting rows ({force_delete_where}) from "
                  f"{table_name}...", flush=True)
            await conn.execute(
                f"DELETE FROM {table_name} WHERE {force_delete_where}"
            )
        else:
            print(f"  Deleting existing rows from {table_name}...", flush=True)
            await conn.execute(f"DELETE FROM {table_name}")
    else:
        if target_pairs is not None and len(target_pairs) == 0:
            print("  up to date; nothing to insert.", flush=True)
            return 0

        if target_pairs is not None:
            n_before = len(result_df)
            # Vectorized PK membership filter (B-A4): materialize the PK
            # date columns as python dates (ONE numpy pass each — the
            # target_pairs tuples hold datetime.date objects), then a
            # single MultiIndex.isin instead of per-row apply.
            pk_df = result_df[pk_columns].copy()
            pk_df = to_py_dates(
                pk_df,
                [c for c in pk_columns
                 if pd.api.types.is_datetime64_any_dtype(pk_df[c])],
            )
            mask = pd.MultiIndex.from_frame(pk_df).isin(target_pairs)
            result_df = result_df.loc[mask].reset_index(drop=True)
            print(f"  Incremental filter: {len(result_df):,} of "
                  f"{n_before:,} rows are in target pairs", flush=True)

    if result_df.empty:
        print("  no rows to write after filter", flush=True)
        return 0

    n_chunks = (len(result_df) + _CHUNK_SIZE - 1) // _CHUNK_SIZE
    total = 0

    print(f"  {'COPY' if force else 'Upsert'}ing {len(result_df):,} rows "
          f"in {n_chunks} chunks...", flush=True)

    for i in range(n_chunks):
        chunk = result_df.iloc[
            i * _CHUNK_SIZE : (i + 1) * _CHUNK_SIZE
        ].copy()

        rows = sanitize_for_db_insert(
            chunk,
            numeric_cols=numeric_cols,
            round_to=round_to,
        )
        if not rows:
            continue

        if force:
            n = await copy_insert_async(conn, table_name, rows)
        else:
            n_copied, n_upserted = await copy_or_upsert_split_async(
                conn, table_name, rows, key_columns=pk_columns,
            )
            n = n_copied + n_upserted
        total += n
        via = "COPY" if force else (
            "COPY" if n_copied > 0 and n_upserted == 0 else
            f"COPY+upsert ({n_copied}+{n_upserted})" if n_copied > 0 else
            "upsert"
        )
        print(f"    chunk {i + 1}/{n_chunks}: "
              f"{via} {n:,} rows "
              f"(cumulative {total:,})", flush=True)

    print(f"  wrote {total:,} rows total", flush=True)
    return total


async def _run_expiry_identity_pipeline(
    conn,
    force: bool,
    sec_type: str | None = None,
) -> int:
    """Populate analysis.options_expiry_identity.

    When force=True, first delete all referencing tables to avoid FK
    constraint violations.

    Returns number of rows written.
    """
    print("\n  Populating expiry identity table...", flush=True)

    if force:
        print("    Force mode: clearing dependent tables first...", flush=True)
        await conn.execute("DELETE FROM analysis.options_walls")
        await conn.execute("DELETE FROM analysis.options_skewness_stats")
        await conn.execute("DELETE FROM analysis.options_iv_skew_stats")
        await conn.execute("DELETE FROM analysis.options_oi_stats")

    rows = await fetch_expiry_identity_rows(conn, sec_type)
    print(f"    {len(rows):,} distinct expiry groups", flush=True)

    if not rows:
        print("    no data; skipping.", flush=True)
        return 0

    # Create DataFrame from tuples (date objects already materialized in
    # fetch via to_py_dates — no .dt.date round-trip needed here).
    df = pd.DataFrame(rows, columns=EXPIRY_PK_COLUMNS)

    target_pairs = None
    if not force:
        existing = set()
        try:
            existing_rows = await conn.fetch(
                f"SELECT date, option_type, underlying_code, expiry_date "
                f"FROM {EXPIRY_IDENTITY_TABLE}"
            )
            existing = set(
                (r["date"], r["option_type"],
                 r["underlying_code"], r["expiry_date"])
                for r in existing_rows
            )
        except Exception:
            pass
        target_pairs = set(rows) - existing
        if len(target_pairs) == 0:
            print("    -> expiry_identity is up to date; nothing to do.",
                  flush=True)
            return 0

    n = await _write_rows(
        conn, df,
        table_name=EXPIRY_IDENTITY_TABLE,
        numeric_cols=[],
        force=force,
        target_pairs=target_pairs,
        pk_columns=EXPIRY_PK_COLUMNS,
    )
    return n


async def _run_skewness_pipeline(
    conn,
    force: bool,
    sec_type: str | None = None,
) -> int:
    """Run the options_skewness_stats pipeline (skew_type='oi_moneyness').

    Returns number of rows written.
    """
    target_pairs: set | None = None
    if not force:
        print("\n  Detecting missing expiry groups "
              "for skewness stats (oi_moneyness)...", flush=True)
        missing_list = await fetch_missing_skewness_groups(
            conn, sec_type, skew_type=SKEW_TYPE_MONEYNESS,
        )
        target_pairs = {(*t, SKEW_TYPE_MONEYNESS) for t in missing_list}
        print(f"    -> {len(target_pairs):,} missing expiry groups",
              flush=True)
        if len(target_pairs) == 0:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return 0

    print("\n  [1/3] Fetching option contract rows for skewness...",
          flush=True)
    df = await fetch_options_skewness_rows(conn, sec_type)
    print(f"    {len(df):,} contract-date rows", flush=True)
    if df.empty:
        print("    no data; skipping.", flush=True)
        return 0

    print("\n  [2/3] Computing skewness rolling stats...", flush=True)
    result_df = compute_options_skewness_stats(df)
    print(f"    {len(result_df):,} expiry-group result rows", flush=True)

    print("\n  [3/3] Writing to DB...", flush=True)
    n = await _write_rows(
        conn, result_df,
        table_name=SKEWNESS_TABLE_NAME,
        numeric_cols=SKEWNESS_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=SKEWNESS_PK_COLUMNS,
        round_to=4,
    )

    print("\n  -> Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=SKEWNESS_ANALYSIS_NAME,
        detail_name="options_skewness_stats",
        description=SKEWNESS_DESCRIPTION,
    )

    return n


async def _run_oi_pipeline(
    conn,
    force: bool,
    sec_type: str | None = None,
) -> int:
    """Run the options_oi_stats pipeline.

    Computes put/call OI ratio correlation stats per expiry group.
    Returns number of rows written.
    """
    from analyze.options.fetch import fetch_oi_rows
    from analyze.options.compute import compute_options_oi_stats

    target_pairs: set | None = None
    if not force:
        print("\n  Detecting missing expiry groups "
              "for OI stats...", flush=True)
        # Same detection as skewness, but checked against the OI table
        missing_list = await fetch_missing_skewness_groups(
            conn, sec_type, table_name=OI_TABLE_NAME,
        )
        target_pairs = set(missing_list)
        print(f"    -> {len(target_pairs):,} missing expiry groups",
              flush=True)
        if len(target_pairs) == 0:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return 0

    print("\n  [1/3] Fetching option contract rows for OI...",
          flush=True)
    df = await fetch_oi_rows(conn, sec_type)
    print(f"    {len(df):,} contract-date rows", flush=True)
    if df.empty:
        print("    no data; skipping.", flush=True)
        return 0

    print("\n  [2/3] Computing OI put/call ratio correlation stats...", flush=True)
    result_df = compute_options_oi_stats(df)
    print(f"    {len(result_df):,} expiry-group result rows", flush=True)

    print("\n  [3/3] Writing to DB...", flush=True)
    n = await _write_rows(
        conn, result_df,
        table_name=OI_TABLE_NAME,
        numeric_cols=OI_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=EXPIRY_PK_COLUMNS,
    )

    print("\n  -> Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=OI_ANALYSIS_NAME,
        detail_name="options_oi_stats",
        description=OI_DESCRIPTION,
    )

    return n


async def _run_walls_pipeline(
    conn,
    force: bool,
    sec_type: str | None = None,
) -> int:
    """Run the options_walls pipeline.

    Computes per-expiry-group wall zones (wall_type='zone': the
    dominant OI cluster per side with strength score and lifecycle).
    Returns number of rows written.
    """
    # PK for walls includes wall_type
    WALLS_PK_COLUMNS = EXPIRY_PK_COLUMNS + ["wall_type"]

    target_pairs: set | None = None
    if not force:
        print("\n  Detecting missing expiry groups "
              "for walls...", flush=True)
        missing_list = await fetch_missing_walls_groups(conn, sec_type)
        target_pairs = set(missing_list)
        print(f"    -> {len(target_pairs):,} missing expiry-group+wall-type pairs",
              flush=True)
        if len(target_pairs) == 0:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return 0

    print("\n  [1/3] Fetching option contract rows for walls...",
          flush=True)
    df = await fetch_options_walls_rows(conn, sec_type)
    print(f"    {len(df):,} contract-date rows", flush=True)
    if df.empty:
        print("    no data; skipping.", flush=True)
        return 0

    print("\n  [2/3] Computing options wall levels...", flush=True)
    result_df = compute_options_walls(df)
    print(f"    {len(result_df):,} expiry-group wall result rows", flush=True)

    print("\n  [3/3] Writing to DB...", flush=True)
    n = await _write_rows(
        conn, result_df,
        table_name=WALLS_TABLE_NAME,
        numeric_cols=WALLS_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=WALLS_PK_COLUMNS,
        round_to=6,  # mass_share / strength_score die at 2 decimals
    )

    print("\n  -> Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=WALLS_ANALYSIS_NAME,
        detail_name="options_walls",
        description=WALLS_DESCRIPTION,
    )

    return n


async def _run_iv_skew_pipeline(
    conn,
    force: bool,
    sec_type: str | None = None,
) -> int:
    """Run the options_iv_skew_stats pipeline.

    Computes per-expiry-group implied-volatility skew stats (ATM IV,
    25-delta wings, risk reversal, put/call skew, smile skewness + rolling
    suite on risk_reversal_25d) AND the IV smile skewness rolling stats
    written to options_skewness_stats with skew_type='iv_smile' (shared
    skewness stats table, data sources separated by skew_type).
    Returns number of rows written to options_iv_skew_stats.
    """
    # Two missing-group checks: one per output table.
    target_pairs: set | None = None
    corr_target_pairs: set | None = None
    if not force:
        print("\n  Detecting missing expiry groups "
              "for IV skew stats...", flush=True)
        missing_list = await fetch_missing_iv_skew_groups(conn, sec_type)
        target_pairs = set(missing_list)
        print(f"    -> {len(target_pairs):,} missing expiry groups",
              flush=True)

        print("  Detecting missing expiry groups for iv_smile "
              "skewness stats...", flush=True)
        corr_missing = await fetch_missing_iv_skew_groups(
            conn, sec_type,
            table_name=SKEWNESS_TABLE_NAME,
            skew_type=SKEW_TYPE_IV_SMILE,
        )
        corr_target_pairs = {(*t, SKEW_TYPE_IV_SMILE) for t in corr_missing}
        print(f"    -> {len(corr_target_pairs):,} missing expiry groups",
              flush=True)

        if len(target_pairs) == 0 and len(corr_target_pairs) == 0:
            print("    -> DB is up to date; nothing to do.", flush=True)
            return 0

    print("\n  [1/4] Fetching option contract rows for IV skew...",
          flush=True)
    df = await fetch_iv_skew_rows(conn, sec_type)
    print(f"    {len(df):,} contract-date rows", flush=True)
    if df.empty:
        print("    no data; skipping.", flush=True)
        return 0

    print("\n  [2/4] Computing IV skew stats...", flush=True)
    result_df = compute_options_iv_skew_stats(df)
    print(f"    {len(result_df):,} expiry-group result rows", flush=True)

    print("\n  [3/4] Writing IV skew stats to DB...", flush=True)
    n = await _write_rows(
        conn, result_df,
        table_name=IV_SKEW_TABLE_NAME,
        numeric_cols=IV_SKEW_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=EXPIRY_PK_COLUMNS,
    )

    # ---- iv_smile skewness rolling stats (shared skewness table) -------
    print("\n  [4/4] Computing iv_smile skewness rolling stats...",
          flush=True)
    corr_df = compute_options_iv_smile_corr_stats(df)
    print(f"    {len(corr_df):,} expiry-group result rows", flush=True)
    await _write_rows(
        conn, corr_df,
        table_name=SKEWNESS_TABLE_NAME,
        numeric_cols=SKEWNESS_NUMERIC_COLS,
        force=force,
        target_pairs=corr_target_pairs,
        pk_columns=SKEWNESS_PK_COLUMNS,
        force_delete_where=f"skew_type = '{SKEW_TYPE_IV_SMILE}'",
        round_to=4,
    )

    print("\n  -> Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=IV_SKEW_ANALYSIS_NAME,
        detail_name="options_iv_skew_stats",
        description=IV_SKEW_DESCRIPTION,
    )

    return n


async def _run_greek_skew_pipeline(
    conn,
    force: bool,
    sec_type: str | None = None,
) -> int:
    """Run the greek skew pipelines (skew_type='greek_<name>').

    For each greek WITH an industry-standard positioning-skew metric
    (delta/gamma/vega — see analyze/options/compute/), computes the
    PAIR-level CALL-vs-PUT contrast rolling stats and writes them to
    options_skewness_stats with skew_type='greek_<name>' (shared
    skewness stats table, data sources separated by skew_type). The
    pair-level value is duplicated on the CALL and PUT rows.
    Returns total number of rows written across all greeks.
    """
    total = 0

    # Per-greek missing-group detection against the shared skewness table.
    target_pairs_by_greek: dict[str, set | None] = {}
    if not force:
        for g in GREEK_NAMES:
            skew_type = f"greek_{g}"
            print(f"\n  Detecting missing expiry groups for {skew_type} "
                  f"skewness stats...", flush=True)
            missing = await fetch_missing_iv_skew_groups(
                conn, sec_type,
                table_name=SKEWNESS_TABLE_NAME,
                skew_type=skew_type,
            )
            target_pairs_by_greek[g] = {(*t, skew_type) for t in missing}
            print(f"    -> {len(target_pairs_by_greek[g]):,} missing "
                  f"expiry groups", flush=True)
        if all(len(tp) == 0 for tp in target_pairs_by_greek.values()):
            print("    -> DB is up to date; nothing to do.", flush=True)
            return 0

    print("\n  [1/2] Fetching option contract rows for greek skew...",
          flush=True)
    df = await fetch_iv_skew_rows(conn, sec_type)
    print(f"    {len(df):,} contract-date rows", flush=True)
    if df.empty:
        print("    no data; skipping.", flush=True)
        return 0

    for gi, g in enumerate(GREEK_NAMES, start=2):
        skew_type = f"greek_{g}"
        print(f"\n  [{gi}/{len(GREEK_NAMES) + 1}] Computing {skew_type} "
              f"rolling stats...", flush=True)
        result_df = GREEK_SKEW_COMPUTERS[g](df)
        print(f"    {len(result_df):,} expiry-group result rows", flush=True)

        n = await _write_rows(
            conn, result_df,
            table_name=SKEWNESS_TABLE_NAME,
            numeric_cols=SKEWNESS_NUMERIC_COLS,
            force=force,
            target_pairs=target_pairs_by_greek.get(g),
            pk_columns=SKEWNESS_PK_COLUMNS,
            force_delete_where=f"skew_type = '{skew_type}'",
            round_to=4,
        )
        total += n

    print("\n  -> Upserting analysis.analysis_identity registry...",
          flush=True)
    await upsert_analysis_identity(
        conn,
        name=SKEWNESS_ANALYSIS_NAME,
        detail_name="options_skewness_stats",
        description=SKEWNESS_DESCRIPTION,
    )

    return total


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Options analysis pipelines. Computes per-expiry-group "
                    "rolling skewness (OI-weighted moneyness) stats, "
                    "per-expiry-group OI correlation stats, per-expiry-group "
                    "options wall zones (strength-scored zone with "
                    "lifecycle), and "
                    "per-expiry-group IV skew stats (risk reversal etc.).",
    )
    add_force_arg(ap)
    ap.add_argument(
        "--sec-type",
        choices=["index", "etf"],
        default=None,
        help="Filter by underlying target type (index or etf).",
    )
    args = ap.parse_args()
    force = args.force
    sec_type = args.sec_type

    t0 = time.time()
    print_build_header(
        "ANALYZE OPTIONS (expiry-group skewness + OI stats + walls + IV skew)",
        tables=(f"{EXPIRY_IDENTITY_TABLE}, {SKEWNESS_TABLE_NAME}, "
                f"{OI_TABLE_NAME}, {WALLS_TABLE_NAME}, {IV_SKEW_TABLE_NAME}"),
        sec_type=sec_type or "all",
        mode="FORCE (full recompute)" if force
             else "incremental (missing groups only)",
    )

    conn = await get_db_connection_async()
    try:
        # ---- Pipeline 0: populate options_expiry_identity (FK lookup) -----
        print("\n" + "=" * 60)
        print("PIPELINE 0: options_expiry_identity (FK lookup)")
        print("=" * 60)
        n_id = await _run_expiry_identity_pipeline(conn, force, sec_type)

        # ---- Pipeline 1: options_skewness_stats --------------------------
        print("\n" + "=" * 60)
        print("PIPELINE 1: options_skewness_stats (expiry-group skewness)")
        print("=" * 60)
        n1 = await _run_skewness_pipeline(conn, force, sec_type)

        # ---- Pipeline 2: options_oi_stats -------------------------------
        print("\n" + "=" * 60)
        print("PIPELINE 2: options_oi_stats (expiry-group OI)")
        print("=" * 60)
        n2 = await _run_oi_pipeline(conn, force, sec_type)

        # ---- Pipeline 3: options_walls ----------------------------------
        print("\n" + "=" * 60)
        print("PIPELINE 3: options_walls (zone wall zones)")
        print("=" * 60)
        n3 = await _run_walls_pipeline(conn, force, sec_type)

        # ---- Pipeline 4: options_iv_skew_stats --------------------------
        print("\n" + "=" * 60)
        print("PIPELINE 4: options_iv_skew_stats (IV-based skew)")
        print("=" * 60)
        n4 = await _run_iv_skew_pipeline(conn, force, sec_type)

        # ---- Pipeline 5: greek skew (options_skewness_stats) -----------
        print("\n" + "=" * 60)
        print("PIPELINE 5: options_skewness_stats (greek_* skew types)")
        print("=" * 60)
        n5 = await _run_greek_skew_pipeline(conn, force, sec_type)

        total = n_id + n1 + n2 + n3 + n4 + n5
        print(f"\n  TOTAL: {total:,} rows written "
              f"(expiry_identity={n_id:,}, "
              f"skewness={n1:,}, oi={n2:,}, walls={n3:,}, "
              f"iv_skew={n4:,}, greek_skew={n5:,})", flush=True)
        print_wall_time(t0)
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=10)
        except (asyncio.TimeoutError, Exception):
            pass


if __name__ == "__main__":
    from _common.post_check import post_check
    try:
        asyncio.run(main())
    finally:
        post_check()
