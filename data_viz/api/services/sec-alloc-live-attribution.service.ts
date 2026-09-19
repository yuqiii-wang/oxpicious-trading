/**
 * Sec-Alloc Live Attribution service — per-industry aggregates at ONE 5-min
 * tick from the live schema tables (populated by
 * `python -m live.sec_alloc_live_attribution`, triggered every 5 min by the
 * Market Movements UI):
 *
 *   • WEIGHTED ("By Trading Amt"): SUM(weight × shared_weight × tick.pct) /
 *     SUM(weight × shared_weight) over members with non-NULL pct —
 *     renormalized because some members may have NULL pct/weights at a tick.
 *     The former live.sec_alloc_live_prev_ref weight materialization was
 *     consolidated away; both weights are reproduced AT QUERY TIME from the
 *     base tables (same semantics as the pipeline's ref build):
 *       - code_trading_amount_weight = member prev-day trading amount
 *         (stats.index_basic_stats on the latest close-bearing date < the
 *         tick date) / Σ amounts over the benchmark's eligible member
 *         universe (Σ = 1, renormalized over non-NULL amounts).
 *       - code_sec_shared_weight = composition overlap vs benchmark from
 *         stats.cross_stats (pair grain sec_type='index', latest row per
 *         member); NULL/NaN → 0 (zero-overlap members contribute nothing
 *         to the weighted aggregate).
 *   • EQUAL ("without trading amt"): plain AVG(pct) — ref-free by design.
 *
 * Industry identity = stats.sec_classification (latest ACTIVE row per code).
 *
 * weighted_available = at least one eligible member carries a REAL prev-day
 * trading amount for the (benchmark, date) — i.e. the weighted weights are
 * computable. While FALSE (prev-day basic_stats lagging), the UI disables
 * the "By Trading Amt" toggle and renders the equal-weighted aggregates.
 *
 * ON-DEMAND BACKFILL: the tick table is a rolling cache (newest 20 trading
 * dates). When the requested (benchmark, date) has NO tick rows — a date
 * selected on the UI that fell out of the retention window, or one the
 * 5-min keeper never reached — this service invokes the pipeline's
 * `--mode compute --date D --benchmark B` via the py-runner and then serves
 * the normal queries. The spawn is deduped by process-id-tag; when a run
 * is already in flight the request proceeds with whatever rows exist (the
 * UI's 5-min poll picks the data up on a later tick). Lookback is bounded
 * by the raw intraday table's retention, not by the cache window.
 *
 * GET /api/live-data/sec-alloc-live/attribution
 *   ?benchmark_code=000300&date=YYYY-MM-DD&time=HH:MM:SS
 */
import { queryRows, formatDate, toNum } from "../lib/db.js";
import { runPythonModule } from "./py-runner.service.js";
import type { QueryResultRow } from "pg";
import type {
  SecAllocLiveAttributionIndustry,
  SecAllocLiveAttributionResponse,
} from "../../shared/types.js";

interface DbDateRow extends QueryResultRow {
  max_date: Date | string | null;
}

interface DbAvailabilityRow extends QueryResultRow {
  weighted_available: boolean;
}

interface DbHasRowsRow extends QueryResultRow {
  has_tick_rows: boolean;
}

interface DbAggregateRow extends QueryResultRow {
  industry_id: string;
  is_industry_not_strategy: boolean | null;
  weighted_pct: number | null;
  equal_pct: number | null;
  member_count: number | string;
}

// ----------------------------------------------------------------------------
//  SQL: aggregates per industry at one tick.
//
//  Weight semantics (computed ONCE per request in the CTEs — the same
//  once-per-(benchmark, date) normalization the pipeline's former ref build
//  performed, now at read time):
//    • prev_d          — ONE resolved prev date: latest close-bearing
//                        stats.index_basic_stats date strictly before the
//                        tick date; every prev-value join is an exact-date
//                        equality on it.
//    • member_prev_amt — member's REAL prev-day trading_amount (literal NaN
//                        numerics excluded via ::text <> 'NaN'). Members
//                        without one keep weight NULL → excluded from the
//                        weighted aggregate (renormalized denominator),
//                        still counted by AVG (equal).
//    • weights         — amount / Σ amounts (Σ over members with a real
//                        amount; 0..1, Σ = 1). Only tick-eligible members
//                        (index/etf) need weights: stocks never carry tick
//                        rows, so the per-tick renormalized aggregate is
//                        identical to normalizing over the full universe.
//    • cs_weight       — composition overlap from stats.cross_stats (latest
//                        row per member; snapshot-constant per pair).
//                        NaN → 0, NULL → 0 (zero-overlap).
//
//  $1 = benchmark_code, $2 = date, $3 = time
// ----------------------------------------------------------------------------
const AGGREGATES_SQL = `
WITH cls AS (
    SELECT DISTINCT ON (code)
        code,
        industry_id,
        is_industry_not_strategy
    FROM stats.sec_classification
    WHERE is_active = TRUE
      AND industry_id IS NOT NULL AND industry_id <> ''
    ORDER BY code
),
universe AS (
    SELECT code FROM stats.sec_classification
    WHERE is_active = TRUE
      AND type IN ('index', 'etf')
      AND industry_id IS NOT NULL AND industry_id <> ''
      AND industry_id NOT LIKE 'BROAD_%'
      AND code <> $1::text
),
prev_d AS (
    SELECT MAX(date) AS d
    FROM stats.index_basic_stats
    WHERE date < $2::date
      AND close IS NOT NULL
),
member_prev_amt AS (
    SELECT b.code, b.trading_amount AS amt
    FROM stats.index_basic_stats b
    WHERE b.date = (SELECT d FROM prev_d)
      AND b.trading_amount IS NOT NULL AND b.trading_amount::text <> 'NaN'
      AND b.code IN (SELECT code FROM universe)
),
weights AS (
    SELECT m.code, m.amt / t.total AS w
    FROM member_prev_amt m
    CROSS JOIN (SELECT SUM(amt) AS total FROM member_prev_amt) t
    WHERE t.total > 0
),
cs_weight AS (
    SELECT DISTINCT ON (cs.code)
        cs.code, cs.code_sec_shared_weight
    FROM stats.cross_stats cs
    WHERE cs.sec_type = 'index'
      AND cs.benchmark_code = $1::text
    ORDER BY cs.code, cs.date DESC
),
base AS (
    SELECT
        cls.industry_id,
        COALESCE(cls.is_industry_not_strategy, TRUE)    AS is_industry_not_strategy,
        w.w,
        CASE WHEN csw.code_sec_shared_weight::text = 'NaN' THEN 0
             ELSE COALESCE(csw.code_sec_shared_weight, 0) END AS sw,
        a.code_price_pct_relative_prev_date_close       AS pct
    FROM live.sec_alloc_live_attribution a
    JOIN cls ON cls.code = a.code
    LEFT JOIN weights w ON w.code = a.code
    LEFT JOIN cs_weight csw ON csw.code = a.code
    WHERE a.benchmark_code = $1::text
      AND a.date = $2::date
      AND a.time = $3::time
)
SELECT
    industry_id,
    BOOL_OR(is_industry_not_strategy)                     AS is_industry_not_strategy,
    COALESCE(
        (SUM(w * sw * pct)
            / NULLIF(SUM(w * sw) FILTER (WHERE pct IS NOT NULL AND w IS NOT NULL AND sw IS NOT NULL), 0)
        )::float8,
        0
    )                                                      AS weighted_pct,
    AVG(pct)::float8                                      AS equal_pct,
    COUNT(*)                                              AS member_count
FROM base
WHERE industry_id IS NOT NULL
GROUP BY industry_id
ORDER BY weighted_pct DESC
`;

/** Latest tick date for one benchmark.
 *  $1 = benchmark_code */
const LATEST_DATE_SQL = `
SELECT MAX(date) AS max_date
FROM live.sec_alloc_live_attribution
WHERE benchmark_code = $1::text
`;

/** Latest RAW intraday date for one benchmark — the date-resolution fallback
 *  when the tick cache has no rows at all for the benchmark (the on-demand
 *  compute below then backfills it).
 *  $1 = benchmark_code */
const RAW_LATEST_DATE_SQL = `
SELECT MAX(date) AS max_date
FROM stats.index_intraday_5min
WHERE code = $1::text AND close IS NOT NULL
`;

/** Existence probe: does the (benchmark, date) have ANY tick rows? A plain
 *  indexed lookup (idx_sec_alloc_live_attr_bench_dt) — runs on every request,
 *  so it must stay O(1)-ish. $1 = benchmark_code, $2 = date */
const HAS_TICK_ROWS_SQL = `
SELECT EXISTS (
    SELECT 1
    FROM live.sec_alloc_live_attribution
    WHERE benchmark_code = $1::text
      AND date = $2::date
) AS has_tick_rows
`;

// ----------------------------------------------------------------------------
//  On-demand backfill — invoked when the requested (benchmark, date) has no
//  tick rows (outside the rolling retention window / never reached by the
//  keeper). Runs `python -m live.sec_alloc_live_attribution --mode compute
//  --date D --benchmark B` and waits for it (a few seconds: subprocess start
//  + ~1s of per-pair SQL). Deduped by process-id-tag so concurrent requests
//  for the same date resolve immediately; failures are logged and swallowed
//  (the response then carries whatever the table holds — same as before this
//  path existed).
// ----------------------------------------------------------------------------
async function backfillDateOnDemand(
  benchmarkCode: string,
  targetDate: string,
): Promise<void> {
  const tag = `sec-alloc-live:compute:${benchmarkCode}:${targetDate}`;
  const result = await runPythonModule(
    "live.sec_alloc_live_attribution",
    ["--mode", "compute", "--date", targetDate, "--benchmark", benchmarkCode],
    { processIdTag: tag },
  );
  if (!result.success && !result.already_running) {
    console.error(
      `[sec-alloc-live/attribution] on-demand compute failed for ` +
      `${benchmarkCode} @ ${targetDate} (exit ${result.exitCode}):`,
      result.stderr.slice(-500),
    );
  }
}

// ----------------------------------------------------------------------------
//  weighted_available — the trading-amount weights are computable for the
//  (benchmark, date): a resolved prev date exists AND at least one eligible
//  member carries a REAL prev-day amount.
//  $1 = benchmark_code, $2 = date
// ----------------------------------------------------------------------------
const WEIGHTED_AVAILABLE_SQL = `
WITH universe AS (
    SELECT code FROM stats.sec_classification
    WHERE is_active = TRUE
      AND type IN ('index', 'etf')
      AND industry_id IS NOT NULL AND industry_id <> ''
      AND industry_id NOT LIKE 'BROAD_%'
      AND code <> $1::text
),
prev_d AS (
    SELECT MAX(date) AS d
    FROM stats.index_basic_stats
    WHERE date < $2::date
      AND close IS NOT NULL
)
SELECT EXISTS (
    SELECT 1
    FROM stats.index_basic_stats b
    WHERE b.date = (SELECT d FROM prev_d)
      AND b.trading_amount IS NOT NULL AND b.trading_amount::text <> 'NaN'
      AND b.code IN (SELECT code FROM universe)
) AS weighted_available
`;

export async function getSecAllocLiveAttribution(
  benchmarkCode: string,
  date: string | null,
  time: string,
): Promise<SecAllocLiveAttributionResponse> {
  let targetDate = date;
  if (!targetDate) {
    let dateRows = await queryRows<DbDateRow>(LATEST_DATE_SQL, [benchmarkCode]);
    targetDate = formatDate(dateRows[0]?.max_date ?? null);
    if (!targetDate) {
      // No cached ticks for the benchmark at all — fall back to the raw
      // intraday latest and let the on-demand compute below backfill it.
      dateRows = await queryRows<DbDateRow>(RAW_LATEST_DATE_SQL, [benchmarkCode]);
      targetDate = formatDate(dateRows[0]?.max_date ?? null);
    }
    if (!targetDate) {
      return {
        benchmark_code: benchmarkCode,
        date: "",
        time,
        weighted_available: false,
        industries: [],
      };
    }
  }

  // On-demand backfill: a selected date outside the rolling retention window
  // (or one the 5-min keeper never reached) has no tick rows — recompute it
  // from the base tables before serving. The probe is an indexed point
  // lookup, so the normal cached path pays ~nothing for this check.
  const hasRows = await queryRows<DbHasRowsRow>(HAS_TICK_ROWS_SQL, [
    benchmarkCode,
    targetDate,
  ]);
  if (hasRows[0]?.has_tick_rows !== true) {
    await backfillDateOnDemand(benchmarkCode, targetDate);
  }

  const [availRows, aggRows] = await Promise.all([
    queryRows<DbAvailabilityRow>(WEIGHTED_AVAILABLE_SQL, [benchmarkCode, targetDate]),
    queryRows<DbAggregateRow>(AGGREGATES_SQL, [benchmarkCode, targetDate, time]),
  ]);

  const industries: SecAllocLiveAttributionIndustry[] = aggRows.map((r) => ({
    industry_id: r.industry_id,
    is_strategy: !(r.is_industry_not_strategy ?? true),
    weighted_pct: toNum(r.weighted_pct),
    equal_pct: toNum(r.equal_pct),
    member_count: Number(r.member_count ?? 0),
  }));

  return {
    benchmark_code: benchmarkCode,
    date: targetDate,
    time,
    weighted_available: availRows[0]?.weighted_available === true,
    industries,
  };
}
