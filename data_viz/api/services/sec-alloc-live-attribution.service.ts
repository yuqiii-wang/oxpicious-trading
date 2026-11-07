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
import { formatDate, toNum } from "../lib/db.js";
import { querySqlView } from "../lib/sql-views.js";
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
//  SQL: aggregates per industry at one tick — the SELECT lives in the sql
//  view data_viz/api/services/sec-alloc-live-attribution/aggregates (the
//  weight semantics — prev_d / member_prev_amt / weights / cs_weight CTEs —
//  are documented in its header).
// ----------------------------------------------------------------------------

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
//  member carries a REAL prev-day amount. The SELECT lives in the sql view
//  data_viz/api/services/sec-alloc-live-attribution/weighted_available.
// ----------------------------------------------------------------------------

export async function getSecAllocLiveAttribution(
  benchmarkCode: string,
  date: string | null,
  time: string,
): Promise<SecAllocLiveAttributionResponse> {
  let targetDate = date;
  if (!targetDate) {
    let dateRows = await querySqlView<DbDateRow>(
      "data_viz/api/services/sec-alloc-live-attribution/latest_tick_date",
      { benchmark_code: benchmarkCode },
    );
    targetDate = formatDate(dateRows[0]?.max_date ?? null);
    if (!targetDate) {
      // No cached ticks for the benchmark at all — fall back to the raw
      // intraday latest and let the on-demand compute below backfill it.
      dateRows = await querySqlView<DbDateRow>(
        "data_viz/api/services/sec-alloc-live-attribution/raw_latest_date",
        { code: benchmarkCode },
      );
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
  const hasRows = await querySqlView<DbHasRowsRow>(
    "data_viz/api/services/sec-alloc-live-attribution/has_tick_rows",
    { benchmark_code: benchmarkCode, date: targetDate },
  );
  if (hasRows[0]?.has_tick_rows !== true) {
    await backfillDateOnDemand(benchmarkCode, targetDate);
  }

  const [availRows, aggRows] = await Promise.all([
    querySqlView<DbAvailabilityRow>(
      "data_viz/api/services/sec-alloc-live-attribution/weighted_available",
      { benchmark_code: benchmarkCode, date: targetDate },
    ),
    querySqlView<DbAggregateRow>(
      "data_viz/api/services/sec-alloc-live-attribution/aggregates",
      { benchmark_code: benchmarkCode, date: targetDate, time },
    ),
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
