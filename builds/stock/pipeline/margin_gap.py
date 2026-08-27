"""Margin (融资融券) gap detection: zero / partial / missing-liquidity dates."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from _common.build_commons import ymd_from_filename, ymd_to_date

# Repair-scan gating for the identity-vs-liquidity_margin hole check (#3).
# An identity row without a liquidity_margin row is only refillable when
# the source CSVs actually carry that stock on that day; structurally
# unfillable holes (suspended/delisted stocks) never converge, so without
# a gate they re-flag their whole date on EVERY run and each default run
# degenerates into a full-market OHLCV backfill. Two independent gates:
MARGIN_REPAIR_HOLE_RATIO: float = 0.01   # holes must exceed 1% of the day's identity rows
MARGIN_REPAIR_MAX_AGE_DAYS: int = 30     # and the date must be within 30d of the newest CSV


@dataclass
class MarginGapResult:
    """Dates needing margin/liquidity (re)loads, split by gap kind."""
    target_dates: set[date]
    missing_margin_dates: set[date] = field(default_factory=set)
    partial_margin_dates: set[date] = field(default_factory=set)
    missing_liq_dates: set[date] = field(default_factory=set)
    missing_liquidity_dates: set[date] = field(default_factory=set)


def count_margin_csv_codes(
    szse_files: list[str],
    sse_files: list[str],
    target_dates: set[date],
) -> dict[date, int]:
    """Quickly scan margin CSV files and count unique stock codes per date.

    BYTE-level scan (no DataFrame): reads each target-date file once,
    splits lines, and counts distinct 证券代码 values among sec_type ==
    "stock" rows using column positions taken from the file's own header.
    Placeholder/holiday exports yield zero matches and fall out naturally,
    so no separate emptiness peek is needed. Used for detecting partial-
    margin gaps where CSV has data but DB has zero margin for some stocks.
    """
    result: dict[date, int] = {}
    all_files = szse_files + sse_files

    for fpath in all_files:
        ymd = ymd_from_filename(fpath, "szse_margin_detail_") or \
            ymd_from_filename(fpath, "sse_margin_detail_")
        if not ymd:
            continue
        d = ymd_to_date(ymd)
        if d is None or d not in target_dates or d in result:
            continue
        try:
            with open(fpath, "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        text = raw.replace(b"\r\n", b"\n").decode(
            "utf-8-sig", errors="replace")
        lines = text.split("\n")
        if not lines:
            continue
        header = [c.strip() for c in lines[0].split(",")]
        if "证券代码" not in header:
            continue
        i_code = header.index("证券代码")
        i_sec = header.index("sec_type") if "sec_type" in header else -1
        codes: set[str] = set()
        for ln in lines[1:]:
            cells = ln.split(",")
            if len(cells) <= max(i_code, i_sec):
                continue
            if i_sec >= 0 and cells[i_sec].strip() != "stock":
                continue
            code_val = cells[i_code].strip()
            if code_val:
                codes.add(code_val)
        if codes:
            result[d] = len(codes)

    return result


def newest_margin_csv_date(
    szse_files: list[str],
    sse_files: list[str],
) -> date | None:
    """Newest date among SZSE/SSE margin detail CSV file names (or None)."""
    best: date | None = None
    for fpath in szse_files + sse_files:
        ymd = ymd_from_filename(fpath, "szse_margin_detail_") or \
            ymd_from_filename(fpath, "sse_margin_detail_")
        if not ymd:
            continue
        d = ymd_to_date(ymd)
        if d is not None and (best is None or d > best):
            best = d
    return best


async def detect_margin_gaps(
    conn,
    margin_available_dates: set[date],
    missing_dates: set[date],
    code_filter: str | None,
    force: bool,
    szse_margin_files: list[str],
    sse_margin_files: list[str],
) -> MarginGapResult:
    """Detect dates needing margin (re)loads.

    force mode reloads margin for all missing_dates; otherwise four
    checks run against the DB:
      1) all-zero margin dates (every stock has 0 margin)
      2) partial-margin dates (CSV has more stock codes than the DB has
         codes with non-zero margin)
      3) dates in identity but missing from liquidity_margin — GATED:
         flagged only when holes exceed MARGIN_REPAIR_HOLE_RATIO of the
         day's identity rows AND the date lies within
         MARGIN_REPAIR_MAX_AGE_DAYS of the newest margin detail CSV
         (older/sparse holes are structurally unfillable — suspended or
         delisted stocks — and would otherwise re-fire every run)
      4) dates with zero liquidity but non-zero margin (whole-date OHLCV
         missing; per-stock zero-volume rows from suspensions excluded by
         the HAVING MAX(...) whole-date semantics)
    """
    if force:
        return MarginGapResult(target_dates=set(missing_dates))

    missing_margin_dates: set[date] = set()
    partial_margin_dates: set[date] = set()
    missing_liq_dates: set[date] = set()
    missing_liquidity_dates: set[date] = set()
    n_hole_dates_gated_out: int = 0

    if margin_available_dates:
        # Build WHERE clause snippet for code filtering
        _code_where = f"AND code = '{code_filter}'" if code_filter else ""

        # ---- 1) All-zero margin dates (every stock has 0 margin) ----
        margin_backfill_rows = await conn.fetch(
            f"""
            SELECT slm.date
            FROM stats.stock_liquidity_margin slm
            WHERE slm.date = ANY($1::date[])
              {_code_where}
            GROUP BY slm.date
            HAVING MAX(slm.rz_balance) = 0
               AND MAX(slm.rz_buy) = 0
               AND MAX(slm.rq_sell_qty) = 0
               AND MAX(slm.rq_balance_qty) = 0
               AND MAX(slm.rq_balance_amt) = 0
               AND MAX(slm.total_balance) = 0
            """,
            sorted(margin_available_dates),
        )
        missing_margin_dates = {r["date"] for r in margin_backfill_rows}

        # ---- 2) Partial-margin dates: some stocks have zero margin
        #    while the CSV has data for them. Detected by comparing
        #    CSV stock-code count vs DB non-zero-margin code count
        #    per date. If the CSV has more stock codes than the DB
        #    has codes with non-zero margin, some stocks are missing
        #    margin data (but may have trading data). ----
        if code_filter:
            # Single-code mode: simplify to direct DB check per date
            csv_code_counts: dict[date, int] = {d: 1 for d in margin_available_dates}
        else:
            csv_code_counts = count_margin_csv_codes(
                szse_margin_files, sse_margin_files,
                margin_available_dates,
            )
        if csv_code_counts:
            csv_count_params = [
                {"date": d.isoformat(), "csv_count": c}
                for d, c in csv_code_counts.items()
            ]
            partial_rows = await conn.fetch(
                f"""
                WITH csv_counts(date, csv_count) AS (
                    SELECT (v->>'date')::date AS date,
                           (v->>'csv_count')::int AS csv_count
                    FROM jsonb_array_elements($1::jsonb) AS v
                ),
                db_counts AS (
                    SELECT date, COUNT(*) AS nz_count
                    FROM stats.stock_liquidity_margin
                    WHERE (rz_balance > 0 OR rq_balance_amt > 0)
                      {_code_where}
                    GROUP BY date
                )
                SELECT cc.date
                FROM csv_counts cc
                LEFT JOIN db_counts dc ON cc.date = dc.date
                WHERE cc.csv_count > COALESCE(dc.nz_count, 0)
                """,
                json.dumps(csv_count_params),
            )
            partial_margin_dates = {
                r["date"] for r in partial_rows
            }

        # ---- 3) Dates in identity but missing from liquidity_margin
        #    (GATED repair scan: ratio + recency, see module constants) ----
        _code_where_si = f"AND si.code = '{code_filter}'" if code_filter else ""
        hole_rows = await conn.fetch(
            f"""
            WITH ident AS (
                SELECT si.date, count(*)::int AS n_ident
                FROM stats.stock_identity si
                WHERE si.date = ANY($1::date[])
                  {_code_where_si}
                GROUP BY si.date
            ),
            holes AS (
                SELECT si.date, count(*)::int AS n_holes
                FROM stats.stock_identity si
                LEFT JOIN stats.stock_liquidity_margin slm
                  ON si.date = slm.date AND si.code = slm.code
                WHERE si.date = ANY($1::date[])
                  AND slm.date IS NULL
                  {_code_where_si}
                GROUP BY si.date
            )
            SELECT i.date AS date,
                   i.n_ident AS n_ident,
                   COALESCE(h.n_holes, 0)::int AS n_holes
            FROM ident i
            LEFT JOIN holes h ON h.date = i.date
            """,
            sorted(margin_available_dates),
        )
        newest_csv = newest_margin_csv_date(szse_margin_files, sse_margin_files)
        window_start: date | None = (
            newest_csv - timedelta(days=MARGIN_REPAIR_MAX_AGE_DAYS)
            if newest_csv is not None else None
        )
        for hr in hole_rows:
            hr_date: date = hr["date"]
            n_ident_row: int = int(hr["n_ident"])
            n_holes_row: int = int(hr["n_holes"])
            if n_holes_row == 0:
                continue
            ratio_ok = n_holes_row > MARGIN_REPAIR_HOLE_RATIO * n_ident_row
            recency_ok = window_start is None or hr_date >= window_start
            if ratio_ok and recency_ok:
                missing_liq_dates.add(hr_date)
            else:
                n_hole_dates_gated_out += 1

        # ---- 4) Dates with zero liquidity but non-zero margin ----
        missing_liquidity_rows = await conn.fetch(
            f"""
            SELECT slm.date
            FROM stats.stock_liquidity_margin slm
            WHERE slm.date = ANY($1::date[])
              {_code_where}
            GROUP BY slm.date
            HAVING MAX(slm.trading_shares) = 0
               AND MAX(slm.trading_amount) = 0
               AND MAX(slm.rz_balance) > 0
            """,
            sorted(margin_available_dates),
        )
        missing_liquidity_dates = {
            r["date"] for r in missing_liquidity_rows
        }

    target_dates = (
        set(missing_dates)
        | missing_margin_dates
        | partial_margin_dates
        | missing_liq_dates
        | missing_liquidity_dates
    )
    if missing_margin_dates:
        print(f"    [MARGIN] {len(missing_margin_dates)} dates need "
              f"margin backfill (ALL stocks have zero margin in "
              f"stock_liquidity_margin)", flush=True)
    if partial_margin_dates:
        print(f"    [MARGIN] {len(partial_margin_dates)} dates have "
              f"partial-margin gaps (identity count > liquidity_margin "
              f"count; some stocks missing margin data)", flush=True)
    if missing_liq_dates:
        print(f"    [MARGIN] {len(missing_liq_dates)} dates need "
              f"liquidity + margin (identity-vs-liquidity_margin holes > "
              f"{MARGIN_REPAIR_HOLE_RATIO:.0%} of the day's identity rows "
              f"AND within {MARGIN_REPAIR_MAX_AGE_DAYS}d of newest CSV)",
              flush=True)
    if n_hole_dates_gated_out:
        print(f"    [MARGIN] {n_hole_dates_gated_out} hole-dates suppressed "
              f"by repair gate (sparse/suspended holes — structural, not "
              f"refillable from source CSVs)", flush=True)
    if missing_liquidity_dates:
        print(f"    [MARGIN] {len(missing_liquidity_dates)} dates have "
              f"margin but zero liquidity (no stock traded that day — "
              f"per-stock zero-volume rows from suspensions are excluded); "
              f"the margin pass will fill them", flush=True)

    return MarginGapResult(
        target_dates=target_dates,
        missing_margin_dates=missing_margin_dates,
        partial_margin_dates=partial_margin_dates,
        missing_liq_dates=missing_liq_dates,
        missing_liquidity_dates=missing_liquidity_dates,
    )
