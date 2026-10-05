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
       - Includes pre-expiry contrarian metrics on the gap
         (skewness − neutral): cross_count_20d (neutral crossings in
         the trailing 20 sessions), days_since_last_cross (sessions
         since the last crossing) and gap_side_share_20d (share of the
         trailing 20 sessions at/above neutral) per expiry group.
  2. Compute per-expiry OI level/changes into analysis.options_oi_stats
     (same PK pattern; keyed by REAL expiry date — the dedicated
     per-expiry table, no open-expiry collapse, no FK).
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
  6. Compute the daily per-underlying 30-day model-free implied-vol
     index (CBOE VIX methodology off raw settlement prices) into
     analysis.options_vol_index (PK (underlying_code, date), no FK —
     date-granular, not expiry-granular).
  7. Compute the per-(underlying, date) options-implied bands into
     analysis.options_bands (nearest-to-spot eligible wall zone per
     side across all expiries, same-day INSIDE/APPROACH/BREACH/CROSSED
     states, prior-session-zone APPROACH/BREACH events, 5-session IV
     expected-move context; PK (underlying_code, date), no FK).
  8. Compute the materialized oi_moneyness series (per (underlying,
     date, expiry-month) OI-weighted moneyness aggregates) into
     analysis.options_moneyness_series and the per (underlying, date)
     spot close into analysis.options_spot_daily — the precomputed read
     model behind /api/szse-options/oi-moneyness-series (exact parity
     with the former live 5-way-join SQL; both no FK).
"""
from __future__ import annotations


# Runtime bootstrap — pre-check → silence warnings → cudf.pandas hook →
# UTF-8 stdout. MUST run before the pandas-importing modules below.
from _common.data_pipeline import bootstrap_runtime

bootstrap_runtime()

from _common.db_commons import (
    copy_or_upsert_split_async,
    copy_insert_async,
    chunked_purge_async,
    _wait_for_replica_lag_async,
    DEFAULT_MAX_REPLICA_LAG_MB,
)
from _common.df_utils import host_array

import numpy as np
import pandas as pd

from analyze._common import (
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
    VOL_INDEX_TABLE_NAME,
    VOL_INDEX_ANALYSIS_NAME,
    VOL_INDEX_DESCRIPTION,
    VOL_INDEX_NUMERIC_COLS,
    VOL_INDEX_PK_COLUMNS,
    VOL_INDEX_RESULT_COLUMNS,
    BANDS_TABLE_NAME,
    BANDS_ANALYSIS_NAME,
    BANDS_DESCRIPTION,
    BANDS_NUMERIC_COLS,
    BANDS_PK_COLUMNS,
    BANDS_RESULT_COLUMNS,
    MONEYNESS_TABLE_NAME,
    MONEYNESS_ANALYSIS_NAME,
    MONEYNESS_DESCRIPTION,
    MONEYNESS_NUMERIC_COLS,
    MONEYNESS_PK_COLUMNS,
    MONEYNESS_RESULT_COLUMNS,
    SPOT_TABLE_NAME,
    SPOT_ANALYSIS_NAME,
    SPOT_DESCRIPTION,
    SPOT_NUMERIC_COLS,
    SPOT_PK_COLUMNS,
    SPOT_RESULT_COLUMNS,
)
from analyze.options.fetch import (  # noqa: E402
    fetch_options_skewness_rows,
    fetch_missing_skewness_groups,
    fetch_expiry_identity_rows,
    fetch_options_walls_rows,
    fetch_missing_walls_groups,
    fetch_iv_skew_rows,
    fetch_missing_iv_skew_groups,
    fetch_vol_index_rows,
    fetch_missing_vol_index_dates,
    fetch_missing_bands_dates,
    fetch_moneyness_rows,
    fetch_missing_moneyness_groups,
    fetch_missing_spot_dates,
)
from analyze.options.compute import (  # noqa: E402
    compute_options_skewness_stats,
    compute_options_walls,
    compute_options_bands,
    compute_options_iv_skew_stats,
    compute_options_iv_smile_corr_stats,
    compute_options_vol_index,
    compute_options_moneyness_series,
    GREEK_SKEW_COMPUTERS,
)

from _common.log_setup import setup_logging
from _common.data_analysis import DataAnalysis

logger = setup_logging("options")


_CHUNK_SIZE = 10000


def _pk_mask_host(df: pd.DataFrame, pk_columns: list[str], keys: set) -> np.ndarray:
    """Boolean mask of the rows whose pk_columns tuple is in ``keys``.

    Host-pure membership (replaces to_py_dates + MultiIndex.from_frame
    + Index.isin — each logged a cudf fallback: the astype pair on the
    proxy ndarray, the MixedTypeError ctor on object-date frames, and
    the transfer-blocked isin). One host transfer per column, date
    columns materialized as python dates to match the DB-sourced key
    tuples, then a python tuple-set probe.
    """
    cols = []
    for c in pk_columns:
        arr = host_array(df[c].to_numpy())
        if arr.dtype.kind == "M":
            arr = arr.astype("datetime64[D]").astype(object)
        cols.append(arr.tolist())
    return np.fromiter(
        (tuple(vals) in keys for vals in zip(*cols)),
        dtype=bool, count=len(cols[0]),
    )


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
    mask = _pk_mask_host(result_df, EXPIRY_PK_COLUMNS, ident_keys)
    n_before = len(result_df)
    result_df = result_df.loc[mask].reset_index(drop=True)
    if len(result_df) != n_before:
        logger.info(f"  FK filter: dropped {n_before - len(result_df):,} of "
              f"{n_before:,} rows not in options_expiry_identity")
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
        logger.info("  no rows to write")
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
    # options_vol_index is exempt too: it is date-granular (PK
    # (underlying_code, date), no expiry dimension, no FK).
    # options_oi_stats is exempt as well: it is the dedicated
    # per-REAL-expiry table — its open-group rows carry the real expiry
    # dates, which the collapsed identity intentionally does not contain
    # (see 16_options.sql).
    # options_moneyness_series / options_spot_daily are exempt too:
    # month-group and date-granular keys (no FK — see
    # 25_options_moneyness.sql).
    if table_name not in (
        EXPIRY_IDENTITY_TABLE, VOL_INDEX_TABLE_NAME, OI_TABLE_NAME,
        BANDS_TABLE_NAME, MONEYNESS_TABLE_NAME, SPOT_TABLE_NAME,
    ):
        result_df = await _fk_filter(conn, result_df)

    if force:
        if force_delete_where:
            logger.info(f"  Deleting rows ({force_delete_where}) from "
                  f"{table_name}...")
            await chunked_purge_async(
                conn, table_name, where_sql=force_delete_where,
                key_column="underlying_code",
            )
        else:
            logger.info(f"  Deleting existing rows from {table_name}...")
            await chunked_purge_async(
                conn, table_name, where_sql="TRUE",
                key_column="underlying_code",
            )
    else:
        if target_pairs is not None and len(target_pairs) == 0:
            logger.info("  up to date; nothing to insert.")
            return 0

        if target_pairs is not None:
            n_before = len(result_df)
            # Host-pure PK membership filter: target_pairs tuples hold
            # python date objects from asyncpg; materialize the frame's
            # date columns the same way and probe the tuple set.
            mask = _pk_mask_host(result_df, pk_columns, target_pairs)
            result_df = result_df.loc[mask].reset_index(drop=True)
            logger.info(f"  Incremental filter: {len(result_df):,} of "
                  f"{n_before:,} rows are in target pairs")

    if result_df.empty:
        logger.info("  no rows to write after filter")
        return 0

    n_chunks = (len(result_df) + _CHUNK_SIZE - 1) // _CHUNK_SIZE
    total = 0

    logger.info(f"  {'COPY' if force else 'Upsert'}ing {len(result_df):,} rows "
          f"in {n_chunks} chunks...")

    for i in range(n_chunks):
        # Between commit chunks: wait out standby replay lag before
        # generating more WAL (the bulk-write rule).
        await _wait_for_replica_lag_async(conn, DEFAULT_MAX_REPLICA_LAG_MB)
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
        logger.info(f"    chunk {i + 1}/{n_chunks}: "
              f"{via} {n:,} rows "
              f"(cumulative {total:,})")

    logger.info(f"  wrote {total:,} rows total")
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
    logger.info("\n  Populating expiry identity table...")

    if force:
        logger.info("    Force mode: clearing dependent tables first...")
        await conn.execute("DELETE FROM analysis.options_walls")
        await conn.execute("DELETE FROM analysis.options_skewness_stats")
        await conn.execute("DELETE FROM analysis.options_iv_skew_stats")
        await conn.execute("DELETE FROM analysis.options_oi_stats")
        # Not FK-dependent, but --force means a full rebuild of every
        # options analysis table.
        await conn.execute("DELETE FROM analysis.options_vol_index")
        await conn.execute("DELETE FROM analysis.options_bands")
        await conn.execute("DELETE FROM analysis.options_moneyness_series")
        await conn.execute("DELETE FROM analysis.options_spot_daily")

    rows = await fetch_expiry_identity_rows(conn, sec_type)
    logger.info(f"    {len(rows):,} distinct expiry groups")

    if not rows:
        logger.info("    no data; skipping.")
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
            logger.info("    -> expiry_identity is up to date; nothing to do.")
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
        logger.info("\n  Detecting missing expiry groups "
              "for skewness stats (oi_moneyness)...")
        missing_list = await fetch_missing_skewness_groups(
            conn, sec_type, skew_type=SKEW_TYPE_MONEYNESS,
        )
        target_pairs = {(*t, SKEW_TYPE_MONEYNESS) for t in missing_list}
        logger.info(f"    -> {len(target_pairs):,} missing expiry groups")
        if len(target_pairs) == 0:
            logger.info("    -> DB is up to date; nothing to do.")
            return 0

    logger.info("\n  [1/3] Fetching option contract rows for skewness...")
    df = await fetch_options_skewness_rows(conn, sec_type)
    logger.info(f"    {len(df):,} contract-date rows")
    if df.empty:
        logger.info("    no data; skipping.")
        return 0

    logger.info("\n  [2/3] Computing skewness rolling stats...")
    result_df = compute_options_skewness_stats(df)
    logger.info(f"    {len(result_df):,} expiry-group result rows")

    logger.info("\n  [3/3] Writing to DB...")
    n = await _write_rows(
        conn, result_df,
        table_name=SKEWNESS_TABLE_NAME,
        numeric_cols=SKEWNESS_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=SKEWNESS_PK_COLUMNS,
        round_to=4,
    )

    logger.info("\n  -> Upserting analysis.analysis_identity registry...")
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

    Computes per-expiry OI level/changes (oi_total, oi_delta_5d/20d,
    oi_max_20d) per REAL expiry date — the dedicated per-expiry table,
    no open-expiry collapse. Returns number of rows written.
    """
    from analyze.options.fetch import fetch_oi_rows, fetch_missing_oi_groups
    from analyze.options.compute import compute_options_oi_stats

    target_pairs: set | None = None
    if not force:
        logger.info("\n  Detecting missing expiry groups "
              "for OI stats...")
        # Per-real-expiry detection (no collapse — see 16_options.sql).
        missing_list = await fetch_missing_oi_groups(conn, sec_type)
        target_pairs = set(missing_list)
        logger.info(f"    -> {len(target_pairs):,} missing expiry groups")
        if len(target_pairs) == 0:
            logger.info("    -> DB is up to date; nothing to do.")
            return 0

    logger.info("\n  [1/3] Fetching option contract rows for OI...")
    df = await fetch_oi_rows(conn, sec_type)
    logger.info(f"    {len(df):,} contract-date rows")
    if df.empty:
        logger.info("    no data; skipping.")
        return 0

    logger.info("\n  [2/3] Computing per-expiry OI level/changes...")
    result_df = compute_options_oi_stats(df)
    logger.info(f"    {len(result_df):,} expiry-group result rows")

    logger.info("\n  [3/3] Writing to DB...")
    n = await _write_rows(
        conn, result_df,
        table_name=OI_TABLE_NAME,
        numeric_cols=OI_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=EXPIRY_PK_COLUMNS,
    )

    logger.info("\n  -> Upserting analysis.analysis_identity registry...")
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
        logger.info("\n  Detecting missing expiry groups "
              "for walls...")
        missing_list = await fetch_missing_walls_groups(conn, sec_type)
        target_pairs = set(missing_list)
        logger.info(f"    -> {len(target_pairs):,} missing expiry-group+wall-type pairs")
        if len(target_pairs) == 0:
            logger.info("    -> DB is up to date; nothing to do.")
            return 0

    logger.info("\n  [1/3] Fetching option contract rows for walls...")
    df = await fetch_options_walls_rows(conn, sec_type)
    logger.info(f"    {len(df):,} contract-date rows")
    if df.empty:
        logger.info("    no data; skipping.")
        return 0

    logger.info("\n  [2/3] Computing options wall levels...")
    result_df = compute_options_walls(df)
    logger.info(f"    {len(result_df):,} expiry-group wall result rows")

    logger.info("\n  [3/3] Writing to DB...")
    n = await _write_rows(
        conn, result_df,
        table_name=WALLS_TABLE_NAME,
        numeric_cols=WALLS_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=WALLS_PK_COLUMNS,
        round_to=6,  # mass_share / strength_score die at 2 decimals
    )

    logger.info("\n  -> Upserting analysis.analysis_identity registry...")
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
        logger.info("\n  Detecting missing expiry groups "
              "for IV skew stats...")
        missing_list = await fetch_missing_iv_skew_groups(conn, sec_type)
        target_pairs = set(missing_list)
        logger.info(f"    -> {len(target_pairs):,} missing expiry groups")

        logger.info("  Detecting missing expiry groups for iv_smile "
              "skewness stats...")
        corr_missing = await fetch_missing_iv_skew_groups(
            conn, sec_type,
            table_name=SKEWNESS_TABLE_NAME,
            skew_type=SKEW_TYPE_IV_SMILE,
        )
        corr_target_pairs = {(*t, SKEW_TYPE_IV_SMILE) for t in corr_missing}
        logger.info(f"    -> {len(corr_target_pairs):,} missing expiry groups")

        if len(target_pairs) == 0 and len(corr_target_pairs) == 0:
            logger.info("    -> DB is up to date; nothing to do.")
            return 0

    logger.info("\n  [1/4] Fetching option contract rows for IV skew...")
    df = await fetch_iv_skew_rows(conn, sec_type)
    logger.info(f"    {len(df):,} contract-date rows")
    if df.empty:
        logger.info("    no data; skipping.")
        return 0

    logger.info("\n  [2/4] Computing IV skew stats...")
    result_df = compute_options_iv_skew_stats(df)
    logger.info(f"    {len(result_df):,} expiry-group result rows")

    logger.info("\n  [3/4] Writing IV skew stats to DB...")
    n = await _write_rows(
        conn, result_df,
        table_name=IV_SKEW_TABLE_NAME,
        numeric_cols=IV_SKEW_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=EXPIRY_PK_COLUMNS,
    )

    # ---- iv_smile skewness rolling stats (shared skewness table) -------
    logger.info("\n  [4/4] Computing iv_smile skewness rolling stats...")
    corr_df = compute_options_iv_smile_corr_stats(df)
    logger.info(f"    {len(corr_df):,} expiry-group result rows")
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

    logger.info("\n  -> Upserting analysis.analysis_identity registry...")
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
            logger.info(f"\n  Detecting missing expiry groups for {skew_type} "
                  f"skewness stats...")
            missing = await fetch_missing_iv_skew_groups(
                conn, sec_type,
                table_name=SKEWNESS_TABLE_NAME,
                skew_type=skew_type,
            )
            target_pairs_by_greek[g] = {(*t, skew_type) for t in missing}
            logger.info(f"    -> {len(target_pairs_by_greek[g]):,} missing "
                  f"expiry groups")
        if all(len(tp) == 0 for tp in target_pairs_by_greek.values()):
            logger.info("    -> DB is up to date; nothing to do.")
            return 0

    logger.info("\n  [1/2] Fetching option contract rows for greek skew...")
    df = await fetch_iv_skew_rows(conn, sec_type)
    logger.info(f"    {len(df):,} contract-date rows")
    if df.empty:
        logger.info("    no data; skipping.")
        return 0

    for gi, g in enumerate(GREEK_NAMES, start=2):
        skew_type = f"greek_{g}"
        logger.info(f"\n  [{gi}/{len(GREEK_NAMES) + 1}] Computing {skew_type} "
              f"rolling stats...")
        result_df = GREEK_SKEW_COMPUTERS[g](df)
        logger.info(f"    {len(result_df):,} expiry-group result rows")

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

    logger.info("\n  -> Upserting analysis.analysis_identity registry...")
    await upsert_analysis_identity(
        conn,
        name=SKEWNESS_ANALYSIS_NAME,
        detail_name="options_skewness_stats",
        description=SKEWNESS_DESCRIPTION,
    )

    return total


async def _run_vol_index_pipeline(
    conn,
    force: bool,
    sec_type: str | None = None,
) -> int:
    """Run the options_vol_index pipeline.

    Computes the daily per-underlying 30-day model-free implied-vol index
    (CBOE VIX methodology off raw settlement prices; see
    compute/vix.py) and writes it to analysis.options_vol_index.
    Returns number of rows written.
    """
    target_pairs: set | None = None
    if not force:
        logger.info("\n  Detecting missing (underlying, date) pairs for "
              "vol index...")
        missing_list = await fetch_missing_vol_index_dates(conn, sec_type)
        target_pairs = set(missing_list)
        logger.info(f"    -> {len(target_pairs):,} missing pairs")
        if len(target_pairs) == 0:
            logger.info("    -> DB is up to date; nothing to do.")
            return 0

    logger.info("\n  [1/3] Fetching option contract rows for vol index...")
    df = await fetch_vol_index_rows(conn, sec_type)
    logger.info(f"    {len(df):,} contract-date rows")
    if df.empty:
        logger.info("    no data; skipping.")
        return 0

    logger.info("\n  [2/3] Computing 30d model-free vol index...")
    result_df = compute_options_vol_index(df)
    logger.info(f"    {len(result_df):,} (underlying, date) result rows")

    logger.info("\n  [3/3] Writing vol index to DB...")
    n = await _write_rows(
        conn, result_df,
        table_name=VOL_INDEX_TABLE_NAME,
        numeric_cols=VOL_INDEX_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=VOL_INDEX_PK_COLUMNS,
        round_to=6,
    )

    logger.info("\n  -> Upserting analysis.analysis_identity registry...")
    await upsert_analysis_identity(
        conn,
        name=VOL_INDEX_ANALYSIS_NAME,
        detail_name="options_vol_index",
        description=VOL_INDEX_DESCRIPTION,
    )

    return n


async def _run_bands_pipeline(
    conn,
    force: bool,
    sec_type: str | None = None,
) -> int:
    """Run the options_bands pipeline.

    Computes the per-(underlying_code, date) options-implied bands:
    nearest-to-spot eligible wall zone per side (recomputed via the
    walls compute), same-day INSIDE/APPROACH/BREACH/CROSSED states,
    prior-session-zone APPROACH/BREACH events, and the 5-session IV
    expected-move context (em5_pct / ret5_pct / exhausted_5d).
    Returns number of rows written.
    """
    target_pairs: set | None = None
    if not force:
        logger.info("\n  Detecting missing (underlying, date) pairs for "
              "bands...")
        missing_list = await fetch_missing_bands_dates(conn, sec_type)
        target_pairs = set(missing_list)
        logger.info(f"    -> {len(target_pairs):,} missing pairs")
        if len(target_pairs) == 0:
            logger.info("    -> DB is up to date; nothing to do.")
            return 0

    logger.info("\n  [1/4] Fetching option contract rows for bands "
          "(walls source)...")
    walls_df = await fetch_options_walls_rows(conn, sec_type)
    logger.info(f"    {len(walls_df):,} contract-date rows")
    if walls_df.empty:
        logger.info("    no data; skipping.")
        return 0

    logger.info("\n  [2/4] Fetching option contract rows for bands "
          "(IV source)...")
    iv_df = await fetch_iv_skew_rows(conn, sec_type)
    logger.info(f"    {len(iv_df):,} contract-date rows")

    logger.info("\n  [3/4] Computing options bands...")
    result_df = compute_options_bands(walls_df, iv_df)
    logger.info(f"    {len(result_df):,} (underlying, date) result rows")

    logger.info("\n  [4/4] Writing bands to DB...")
    n = await _write_rows(
        conn, result_df,
        table_name=BANDS_TABLE_NAME,
        numeric_cols=BANDS_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=BANDS_PK_COLUMNS,
        round_to=4,
    )

    logger.info("\n  -> Upserting analysis.analysis_identity registry...")
    await upsert_analysis_identity(
        conn,
        name=BANDS_ANALYSIS_NAME,
        detail_name="options_bands",
        description=BANDS_DESCRIPTION,
    )

    return n


async def _run_moneyness_pipeline(
    conn,
    force: bool,
    sec_type: str | None = None,
) -> int:
    """Run the materialized moneyness series pipeline.

    Computes the per (underlying_code, date, expiry_month) oi_moneyness
    aggregates (analysis.options_moneyness_series — exact parity with the
    former live /oi-moneyness-series SQL) AND the per (underlying_code,
    date) spot close (analysis.options_spot_daily — the endpoint's spot
    axis). Returns total number of rows written across both tables.
    """
    target_pairs: set | None = None
    spot_target_pairs: set | None = None
    if not force:
        logger.info("\n  Detecting missing (underlying, date, month) "
              "groups for the moneyness series...")
        missing_list = await fetch_missing_moneyness_groups(conn, sec_type)
        target_pairs = set(missing_list)
        logger.info(f"    -> {len(target_pairs):,} missing month groups")

        logger.info("  Detecting missing (underlying, date) pairs for "
              "spot daily...")
        spot_missing = await fetch_missing_spot_dates(conn, sec_type)
        spot_target_pairs = set(spot_missing)
        logger.info(f"    -> {len(spot_target_pairs):,} missing dates")

        if len(target_pairs) == 0 and len(spot_target_pairs) == 0:
            logger.info("    -> DB is up to date; nothing to do.")
            return 0

    logger.info("\n  [1/3] Fetching option contract rows for the "
          "moneyness series (LEFT-join basis)...")
    df = await fetch_moneyness_rows(conn, sec_type)
    logger.info(f"    {len(df):,} contract-date rows")
    if df.empty:
        logger.info("    no data; skipping.")
        return 0

    logger.info("\n  [2/3] Computing moneyness series aggregates...")
    groups_df, spot_df = compute_options_moneyness_series(df)
    logger.info(f"    {len(groups_df):,} month-group rows, "
          f"{len(spot_df):,} spot rows")

    logger.info("\n  [3/3] Writing to DB...")
    n_groups = await _write_rows(
        conn, groups_df,
        table_name=MONEYNESS_TABLE_NAME,
        numeric_cols=MONEYNESS_NUMERIC_COLS,
        force=force,
        target_pairs=target_pairs,
        pk_columns=MONEYNESS_PK_COLUMNS,
        round_to=8,  # e_m at full NUMERIC(12,8) precision (sums stay 4dp)
    )
    n_spot = await _write_rows(
        conn, spot_df,
        table_name=SPOT_TABLE_NAME,
        numeric_cols=SPOT_NUMERIC_COLS,
        force=force,
        target_pairs=spot_target_pairs,
        pk_columns=SPOT_PK_COLUMNS,
        round_to=4,  # the settlement source column's exact precision
    )

    logger.info("\n  -> Upserting analysis.analysis_identity registry...")
    await upsert_analysis_identity(
        conn,
        name=MONEYNESS_ANALYSIS_NAME,
        detail_name="options_moneyness_series",
        description=MONEYNESS_DESCRIPTION,
    )
    await upsert_analysis_identity(
        conn,
        name=SPOT_ANALYSIS_NAME,
        detail_name="options_spot_daily",
        description=SPOT_DESCRIPTION,
    )

    return n_groups + n_spot


class OptionsAnalysis(DataAnalysis):
    """``python -m analyze.options`` — eight sequential sub-pipelines.

    There is no per-sec_type loop here: the single --sec-type filter
    (or None = all) is threaded into every sub-pipeline.
    """

    title = "ANALYZE OPTIONS (expiry-group skewness + OI stats + walls + IV skew + bands + moneyness series)"
    component = "options"

    def add_arguments(self, parser) -> None:
        self.add_force_arg(parser)
        parser.add_argument(
            "--sec-type",
            choices=["index", "etf"],
            default=None,
            help="Filter by underlying target type (index or etf).",
        )

    def header_fields(self) -> dict:
        return {
            "tables": (f"{EXPIRY_IDENTITY_TABLE}, {SKEWNESS_TABLE_NAME}, "
                       f"{OI_TABLE_NAME}, {WALLS_TABLE_NAME}, "
                       f"{IV_SKEW_TABLE_NAME}, {BANDS_TABLE_NAME}, "
                       f"{MONEYNESS_TABLE_NAME}, {SPOT_TABLE_NAME}"),
            "sec_type": self.args.sec_type or "all",
            "mode": "FORCE (full recompute)" if self.args.force
                    else "incremental (missing groups only)",
        }

    async def run(self) -> None:
        conn = self.conn
        force = self.args.force
        sec_type = self.args.sec_type

        # ---- Pipeline 0: populate options_expiry_identity (FK lookup) -----
        logger.info("\n" + "=" * 60)
        logger.info("PIPELINE 0: options_expiry_identity (FK lookup)")
        logger.info("=" * 60)
        n_id = await _run_expiry_identity_pipeline(conn, force, sec_type)

        # ---- Pipeline 1: options_skewness_stats --------------------------
        logger.info("\n" + "=" * 60)
        logger.info("PIPELINE 1: options_skewness_stats (expiry-group skewness)")
        logger.info("=" * 60)
        n1 = await _run_skewness_pipeline(conn, force, sec_type)

        # ---- Pipeline 2: options_oi_stats -------------------------------
        logger.info("\n" + "=" * 60)
        logger.info("PIPELINE 2: options_oi_stats (expiry-group OI)")
        logger.info("=" * 60)
        n2 = await _run_oi_pipeline(conn, force, sec_type)

        # ---- Pipeline 3: options_walls ----------------------------------
        logger.info("\n" + "=" * 60)
        logger.info("PIPELINE 3: options_walls (zone wall zones)")
        logger.info("=" * 60)
        n3 = await _run_walls_pipeline(conn, force, sec_type)

        # ---- Pipeline 4: options_iv_skew_stats --------------------------
        logger.info("\n" + "=" * 60)
        logger.info("PIPELINE 4: options_iv_skew_stats (IV-based skew)")
        logger.info("=" * 60)
        n4 = await _run_iv_skew_pipeline(conn, force, sec_type)

        # ---- Pipeline 5: greek skew (options_skewness_stats) -----------
        logger.info("\n" + "=" * 60)
        logger.info("PIPELINE 5: options_skewness_stats (greek_* skew types)")
        logger.info("=" * 60)
        n5 = await _run_greek_skew_pipeline(conn, force, sec_type)

        # ---- Pipeline 6: options_vol_index ------------------------------
        logger.info("\n" + "=" * 60)
        logger.info("PIPELINE 6: options_vol_index (30d model-free vol index)")
        logger.info("=" * 60)
        n6 = await _run_vol_index_pipeline(conn, force, sec_type)

        # ---- Pipeline 7: options_bands -----------------------------------
        logger.info("\n" + "=" * 60)
        logger.info("PIPELINE 7: options_bands (options-implied bands)")
        logger.info("=" * 60)
        n7 = await _run_bands_pipeline(conn, force, sec_type)

        # ---- Pipeline 8: moneyness series + spot daily ------------------
        logger.info("\n" + "=" * 60)
        logger.info("PIPELINE 8: options_moneyness_series + options_spot_daily")
        logger.info("=" * 60)
        n8 = await _run_moneyness_pipeline(conn, force, sec_type)

        total = n_id + n1 + n2 + n3 + n4 + n5 + n6 + n7 + n8
        logger.info(f"\n  TOTAL: {total:,} rows written "
              f"(expiry_identity={n_id:,}, "
              f"skewness={n1:,}, oi={n2:,}, walls={n3:,}, "
              f"iv_skew={n4:,}, greek_skew={n5:,}, vol_index={n6:,}, "
              f"bands={n7:,}, moneyness={n8:,})")


if __name__ == "__main__":
    OptionsAnalysis().execute()
