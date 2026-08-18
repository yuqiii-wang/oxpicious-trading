/**
 * Intraday Movements — Prev-Day OHLC service.
 *
 * Returns the RAW daily OHLC of the PREVIOUS trading day (relative to the
 * live date) for:
 *   • the benchmark itself (drives the DEFAULT prev-day bar), and
 *   • EVERY member index of the benchmark's universe (from
 *     analysis.intraday_index_market_movements for the date), each carrying
 *     its industry_id — the client aggregates per-industry candles as the
 *     MEAN of member %s (equal-weight, same semantics as the page's
 *     industry_price_pct).
 *
 * The client converts to FRACTIONS vs each entry's own close (close → 0.0)
 * so the single prev-day OHLC bar shares the "% change vs prev close"
 * y-axis with today's intraday curves: yesterday's close sits exactly at
 * 0.0 — the same reference today's ticks are measured against.
 *
 * $1 = benchmark_code, $2 = live date (YYYY-MM-DD)
 *
 * GET /api/live-data/intraday-movements/prev-day-ohlc
 *   ?benchmark_code=000300&date=YYYY-MM-DD
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
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
//  SQL: prev-day raw OHLC for the benchmark + all member indices.
//
//  Member universe mirrors the tick rows' member base —
//  live.sec_alloc_live_prev_ref for (benchmark, date) restricted to
//  tick-eligible sec_types (index/etf; stocks hold share weights in the ref
//  but carry no tick rows) — so the bar's aggregation base EXACTLY matches
//  the industries/indices the user can click on the middle/bottom plots.
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
const PREV_DAY_OHLC_SQL = `
WITH params AS (
    SELECT $2::date - CASE extract(dow FROM $2::date)::int
                        WHEN 0 THEN 2   -- Sun -> Fri
                        WHEN 1 THEN 3   -- Mon -> Fri
                        ELSE 1
                      END AS prev_date
),
members AS (
    SELECT DISTINCT code, industry_id
    FROM live.sec_alloc_live_prev_ref
    WHERE benchmark_code = $1::text
      AND date = $2::date
      AND sec_type IN ('index', 'etf')
),
codes AS (
    SELECT code, industry_id FROM members
    UNION ALL
    SELECT $1::text, NULL::text
),
prev_ohlc AS (
    SELECT
        c.code,
        c.industry_id,
        p.prev_date,
        b.open,
        b.high,
        b.low,
        b.close
    FROM codes c
    CROSS JOIN params p
    LEFT JOIN LATERAL (
        SELECT open, high, low, close
        FROM stats.index_basic_stats
        WHERE code = c.code
          AND date = p.prev_date
          AND close IS NOT NULL AND close::text <> 'NaN' AND close > 0
          AND open  IS NOT NULL AND open::text  <> 'NaN'
          AND high  IS NOT NULL AND high::text  <> 'NaN'
          AND low   IS NOT NULL AND low::text   <> 'NaN'
        LIMIT 1
    ) b ON true
    ORDER BY c.code
)
SELECT
    p.code,
    (SELECT name FROM stats.index_identity ii
      WHERE ii.code = p.code ORDER BY ii.date DESC LIMIT 1) AS code_name,
    p.industry_id,
    p.prev_date,
    p.open,
    p.high,
    p.low,
    p.close
FROM prev_ohlc p
ORDER BY p.code
`;

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
    const dateRows = await queryRows<DbLatestDateRow>(
      `SELECT MAX(date) AS max_date FROM stats.index_intraday_5min
       WHERE code = $1::text AND close IS NOT NULL`,
      [benchmarkCode],
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

  const rows = await queryRows<DbOhlcRow>(PREV_DAY_OHLC_SQL, [
    benchmarkCode,
    targetDate,
  ]);

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
