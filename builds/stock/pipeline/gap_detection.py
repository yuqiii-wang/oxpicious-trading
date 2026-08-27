"""Missing-date detection against the database (identity + basic_stats).

Two detection logics:
- LATEST-MISSING-DATES: cheap tail check — compare the newest source CSV
  date vs the newest database date only (two MAX aggregates, no per-date
  scans). Used for incremental (non-force) runs.
- ALL-MISSING-DATES: treat every loadable date as missing (--force /
  rebuild). Whole-market files are read but rows are filtered to the
  requested --code at read time when code_filter is set.
"""
from __future__ import annotations

from datetime import date

from _common.build_commons import (
    get_max_table_date_async,
)
from builds.stock._helpers import _file_has_data
from builds.stock.pipeline.discovery import file_date_from_path


async def find_latest_missing_dates(
    conn,
    source_dates: set[date],
    code_filter: str | None = None,
) -> set[date]:
    """LATEST-MISSING-DATES logic.

    Compares ONLY the latest source CSV date vs the latest database date:
      - source max >  db max → all source dates after db max are missing
      - source max <= db max → nothing missing (no interior-gap scan)
    The DB reference point is the OLDER of identity-max and
    basic_stats-max (both MAX aggregates; a run that died between the two
    inserts would otherwise hide its unfinished tail forever).
    """
    src_max = max(source_dates)
    if code_filter:
        max_identity = await get_max_table_date_async(
            conn, "stats.stock_identity",
            where_clause=f"code = '{code_filter}'",
        )
    else:
        max_identity = await get_max_table_date_async(conn, "stats.stock_identity")

    # basic_stats max via identity join (no exchange/code column there).
    if code_filter:
        basic_row = await conn.fetchrow(
            "SELECT MAX(sbs.date) AS max_date "
            "FROM stats.stock_basic_stats sbs "
            "JOIN stats.stock_identity si "
            "  ON si.code = sbs.code AND si.date = sbs.date "
            "WHERE si.code = $1",
            code_filter,
        )
    else:
        basic_row = await conn.fetchrow(
            "SELECT MAX(date) AS max_date FROM stats.stock_basic_stats"
        )
    max_basic = basic_row["max_date"] if basic_row else None

    db_candidates: list[date] = [
        d for d in (max_identity, max_basic) if d is not None
    ]
    db_max: date | None = min(db_candidates) if db_candidates else None

    # Crash-consistency guard: MAX(date) cannot see a PARTIALLY-written
    # date (a killed run leaves 1 identity key + 1 basic_stats row at
    # src_max forever defeating the min() above). Verify the tail date
    # actually holds (nearly) as many basic_stats rows as identity keys;
    # an incomplete tail date is re-ingested.
    if db_max is not None and not code_filter:
        comp_row = await conn.fetchrow(
            """
            SELECT (SELECT count(*) FROM stats.stock_identity
                     WHERE date = $1)::float AS n_ident,
                   (SELECT count(*) FROM stats.stock_basic_stats
                     WHERE date = $1)::float AS n_basic
            """,
            db_max,
        )
        n_ident_c: float = float(comp_row["n_ident"])
        n_basic_c: float = float(comp_row["n_basic"])
        if n_ident_c > 0 and n_basic_c < 0.5 * n_ident_c:
            print(f"    [DB] tail date {db_max} INCOMPLETE "
                  f"(basic_stats {int(n_basic_c):,} / identity "
                  f"{int(n_ident_c):,}) → re-ingesting", flush=True)
            missing = {d for d in source_dates if d >= db_max}
            return missing

    if db_max is None or db_max < src_max:
        missing = {d for d in source_dates if db_max is None or d > db_max}
        print(f"    [DB] latest-missing-dates: csv latest {src_max} vs db latest "
              f"{db_max} → {len(missing)} tail dates missing", flush=True)
        return missing

    print(f"    [DB] latest-missing-dates: csv latest {src_max} <= db latest "
          f"{db_max} → nothing missing", flush=True)
    return set()


async def detect_missing_dates(
    conn,
    loadable_dates: set[date],
    code_filter: str | None,
    force: bool = False,
) -> set[date]:
    """Dispatch to one of the two missing-date logics.

    - ALL-MISSING-DATES (force): every loadable date is a target. All
      files get read, but with code_filter set each CSV keeps only that
      code's rows at read time ("WHERE code" pushed into the loaders).
    - LATEST-MISSING-DATES (default): see find_latest_missing_dates().
      An empty/unseen scope still falls back to all loadable dates —
      with file-level code filtering capping memory for --code runs.
    """
    if force:
        mode = "ALL-MISSING-DATES"
        label = f"all {len(loadable_dates)} loadable dates are targets" + \
            (f" (rows filtered to code {code_filter} at read time)"
             if code_filter else "")
        print(f"    [DB] {mode}: {label}", flush=True)
        return set(loadable_dates)

    if not loadable_dates:
        return set()
    # Empty DB / unseen code → full-history fallback (still code-filtered
    # at file-read time below, so memory stays bounded in --code runs).
    where_probe = f"code = '{code_filter}'" if code_filter else None
    probe = await get_max_table_date_async(
        conn, "stats.stock_identity", where_clause=where_probe,
    ) if code_filter else await get_max_table_date_async(
        conn, "stats.stock_identity",
    )
    if probe is None:
        print(f"    [DB] stats.stock_identity empty"
              + (f" for code {code_filter}" if code_filter else "") +
              f" — falling back to all {len(loadable_dates)} loadable dates",
              flush=True)
        return set(loadable_dates)

    return await find_latest_missing_dates(conn, loadable_dates, code_filter)


async def collect_missing_file_pairs(
    conn,
    all_files: list[tuple[str, str, str]],
    force: bool = False,
    code_filter: str | None = None,
) -> list[tuple[str, str]]:
    """Per-suffix missing-date detection → (path, market) pairs to read.

    ALL-MISSING-DATES (force): every file is read — no SQL at all.
    When code_filter is set the callers filter rows to that code at read
    time (WHERE-code pushed into _read_one / margin loaders), so a
    single-code --force never materializes whole-market frames.

    LATEST-MISSING-DATES (default): tail-only comparison per exchange
    suffix — read only files newer than the suffix's DB max date.
    """
    if force:
        mode = "ALL-MISSING-DATES"
        label = f"reading all {len(all_files)} source files" + \
            (f" (rows filtered to code {code_filter} at read time)"
             if code_filter else "")
        print(f"    {mode}: {label}", flush=True)
        return [(path, market) for path, market, _suffix in all_files]

    # ---- latest: two MAX aggregates per suffix, no DISTINCT scans ----
    files_by_suffix: dict[str, list[tuple[str, str]]] = {".SZ": [], ".SS": [], ".BJ": []}
    max_file_date_by_suffix: dict[str, date | None] = {}
    for path, market, suffix in all_files:
        files_by_suffix.setdefault(suffix, []).append((path, market))
        d = file_date_from_path(path)
        if d:
            prev = max_file_date_by_suffix.get(suffix)
            if prev is None or d > prev:
                max_file_date_by_suffix[suffix] = d

    missing_file_pairs: list[tuple[str, str]] = []
    for suffix, suffix_files in files_by_suffix.items():
        if not suffix_files:
            continue
        ex = suffix.lstrip(".")

        max_suffix_identity_date = await get_max_table_date_async(
            conn, "stats.stock_identity",
            where_clause=f"exchange = '{ex}'"
        )
        basic_row = await conn.fetchrow(
            "SELECT MAX(sbs.date) AS max_date "
            "FROM stats.stock_basic_stats sbs "
            "JOIN stats.stock_identity si "
            "  ON si.code = sbs.code AND si.date = sbs.date "
            "WHERE si.exchange = $1",
            ex,
        )
        db_candidates: list[date] = [
            d for d in (
                max_suffix_identity_date,
                basic_row["max_date"] if basic_row else None,
            ) if d is not None
        ]
        db_max: date | None = min(db_candidates) if db_candidates else None

        src_max = max_file_date_by_suffix.get(suffix)
        if db_max is None or (src_max is not None and src_max > db_max):
            target_dates = {
                d for d in (
                    file_date_from_path(path) for path, _mkt in suffix_files
                ) if d is not None and (db_max is None or d > db_max)
            }
            before_count = len(missing_file_pairs)
            for path, market in suffix_files:
                d = file_date_from_path(path)
                if d is not None and d in target_dates:
                    if _file_has_data(path):
                        missing_file_pairs.append((path, market))
            print(f"    [{suffix}] latest-missing-dates: csv latest {src_max} "
                  f"vs db latest {db_max} → {len(target_dates)} tail dates, "
                  f"{len(missing_file_pairs) - before_count} files to read",
                  flush=True)
        else:
            print(f"    [{suffix}] latest-missing-dates: csv latest {src_max} "
                  f"<= db latest {db_max} → no files to read", flush=True)

    return missing_file_pairs
