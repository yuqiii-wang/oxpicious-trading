/**
 * Intraday Movements — Prev-Day OHLC service.
 *
 * Returns the RAW daily OHLC of the PREVIOUS trading day (relative to the
 * live date) for:
 *   • the benchmark itself (drives the DEFAULT prev-day bar), and
 *   • EVERY member index of the benchmark's universe (active
 *     stats.sec_classification indices/ETFs with a non-BROAD industry —
 *     the same universe the live tick rows are built for), each carrying
 *     its industry_id — the client aggregates per-industry candles as the
 *     MEAN of member %s (equal-weight, same semantics as the page's
 *     industry_price_pct).
 *
 * The client converts to FRACTIONS vs each entry's own close (close → 0.0)
 * so the single prev-day OHLC bar shares the "% change vs prev close"
 * y-axis with today's intraday curves: yesterday's close sits exactly at
 * 0.0 — the same reference today's ticks are measured against.
 *
 * The SELECTs live in sql views under
 * database/sql_views/data_viz/api/services/analysis/
 * intraday-movements-prev-day-ohlc/.
 *
 * GET /api/live-data/intraday-movements/prev-day-ohlc
 *   ?benchmark_code=000300&date=YYYY-MM-DD
 */
import { formatDate, toNum } from "../../lib/db.js";
import { querySqlView } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import type {
  PrevDayOhlcEntry,
  PrevDayOhlcMember,
  PrevDayOhlcResponse,
} from "../../../shared/types.js";

interface DbLatestDateRow extends QueryResultRow {
  max_date: Date | string | null;
}

interface DbOhlcRow extends QueryResultRow {
  code: string;
  code_name: string | null;
  industry_id: string | null;
  prev_date: Date | string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
}

// ----------------------------------------------------------------------------
//  SQL: prev-day raw OHLC for the benchmark + all member indices. The
//  SELECT lives in the sql view
//  data_viz/api/services/analysis/intraday-movements-prev-day-ohlc/
//  prev_day_ohlc.
//
//  Member universe mirrors the tick rows' member base — active
//  stats.sec_classification indices/ETFs with a non-BROAD industry
//  (benchmark excluded) — so the bar's aggregation base EXACTLY matches
//  the industries/indices the user can click on the middle/bottom plots.
//  (The former live.sec_alloc_live_prev_ref lookup was consolidated away;
//  classification is the surviving source of the member roster + industry
//  tags.)
//
//  The prev day is the STRICT previous weekday of the live date (Mon → Fri,
//  Sun → Fri, otherwise −1 day) — NOT the latest available date. When
//  yesterday's data has not been downloaded/built (or the strict prev
//  weekday was a CN holiday), the benchmark row comes back NULL and the
//  client renders an EMPTY prev-day tick (no bar) instead of silently
//  falling back to an older day, which would mislead the % comparison.
//
//  Per code: stats.index_basic_stats row at EXACTLY the prev weekday with
//  clean (non-NULL, non-NaN) OHLC — stats.index_basic_stats carries literal
//  NaN numerics on some dates, so every field is filtered with
//  ::text <> 'NaN'. close must also be > 0 (it is the % divisor on the
//  client). LEFT JOIN LATERAL keeps codes whose prev-weekday row is
//  missing/dirty out of the result (they are simply skipped client-side).
// ----------------------------------------------------------------------------

export async function getIntradayMovementsPrevDayOhlc(
  rawBenchmarkCode: string,
  rawDate: string | null,
): Promise<PrevDayOhlcResponse> {
  const benchmarkCode = (rawBenchmarkCode ?? "").trim();
  const date = (rawDate ?? "").trim() || null;
  if (!benchmarkCode) {
    throw new Error("Missing 'benchmark_code' parameter");
  }

  // Resolve the live date the same way the main intraday-movements endpoint
  // does (latest raw intraday date for the benchmark) when not provided.
  let targetDate = date;
  if (!targetDate) {
    const dateRows = await querySqlView<DbLatestDateRow>(
      "data_viz/api/services/analysis/intraday-movements-prev-day-ohlc/latest_raw_date",
      { benchmark_code: benchmarkCode },
    );
    targetDate = dateRows[0]?.max_date ? formatDate(dateRows[0].max_date) : null;
  }

  const empty: PrevDayOhlcResponse = {
    benchmark_code: benchmarkCode,
    date: targetDate ?? "",
    benchmark: null,
    members: [],
  };
  if (!targetDate) return empty;

  const rows = await querySqlView<DbOhlcRow>(
    "data_viz/api/services/analysis/intraday-movements-prev-day-ohlc/prev_day_ohlc",
    { benchmark_code: benchmarkCode, date: targetDate },
  );

  let benchmark: PrevDayOhlcEntry | null = null;
  const members: PrevDayOhlcMember[] = [];
  for (const r of rows) {
    const entry: PrevDayOhlcEntry = {
      date: formatDate(r.prev_date),
      open: toNum(r.open),
      high: toNum(r.high),
      low: toNum(r.low),
      close: toNum(r.close),
    };
    if (r.code === benchmarkCode) {
      benchmark = entry;
      continue;
    }
    if (!r.industry_id) continue;
    members.push({
      code: r.code,
      code_name: r.code_name ?? r.code,
      industry_id: r.industry_id,
      ...entry,
    });
  }

  return {
    benchmark_code: benchmarkCode,
    date: targetDate,
    benchmark,
    members,
  };
}
