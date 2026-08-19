/**
 * Sec-Alloc Live Attribution service — per-industry aggregates at ONE 5-min
 * tick from the live schema tables (populated by
 * `python -m live.sec_alloc_live_attribution`, triggered every 5 min by the
 * Market Movements UI):
 *
 *   • WEIGHTED ("By Trading Amt"): SUM(ref.code_trading_amount_weight ×
 *     tick.pct) / SUM(weight) over members with non-NULL pct — renormalized
 *     because stocks hold weights in the ref but never carry tick rows, and
 *     some members may have NULL pct at a tick.
 *   • EQUAL ("without trading amt"): plain AVG(pct) — works with and
 *     without the ref (fallback rows included).
 *
 * Industry identity prefers the ref's denormalized industry_id and falls
 * back to stats.sec_classification (fallback tick rows have no ref parent).
 *
 * weighted_available = a ref row with a non-NULL weight exists for the
 * (benchmark, date). While FALSE (heavy ref not ready — e.g. prev-day
 * basic_stats lagging, or the ref pass still running under the advisory
 * lock), the UI disables the "By Trading Amt" toggle and renders the
 * equal-weighted aggregates.
 *
 * GET /api/live-data/sec-alloc-live/attribution
 *   ?benchmark_code=000300&date=YYYY-MM-DD&time=HH:MM:SS
 */
import { queryRows, formatDate, toNum } from "../lib/db.js";
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
//  LEFT JOINs on purpose:
//    • ref (weights + denormalized industry): missing for FALLBACK rows
//      (is_without_trading_amt = TRUE, written while the ref was not ready)
//      → weight NULL → excluded from weighted numerator/denominator, still
//      counted by AVG (equal) — exactly the intended semantics.
//    • cls (classification fallback for industry identity): DISTINCT ON the
//      latest ACTIVE row per code.
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
base AS (
    SELECT
        COALESCE(r.industry_id, cls.industry_id)          AS industry_id,
        COALESCE(r.is_industry_not_strategy, cls.is_industry_not_strategy, TRUE)
                                                         AS is_industry_not_strategy,
        -- NULL-safe weight: NaN → NULL → excluded from weighted aggregate
        CASE WHEN r.code_trading_amount_weight::text = 'NaN' THEN NULL
             ELSE r.code_trading_amount_weight END         AS w,
        -- NULL-safe shared weight: NaN → 0 (treat as zero-overlap)
        CASE WHEN r.code_sec_shared_weight::text = 'NaN' THEN 0
             ELSE COALESCE(r.code_sec_shared_weight, 0) END AS sw,
        a.code_price_pct_relative_prev_date_close         AS pct
    FROM live.sec_alloc_live_attribution a
    LEFT JOIN live.sec_alloc_live_prev_ref r
        ON r.benchmark_code = a.benchmark_code
       AND r.date = a.date
       AND r.code = a.code
       AND r.sec_type = a.sec_type
    LEFT JOIN cls ON cls.code = a.code
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

/** Availability + date resolution in one round-trip each.
 *  $1 = benchmark_code, $2 = date (optional) */
const LATEST_DATE_SQL = `
SELECT COALESCE(
    MAX(date) FILTER (WHERE src = 'tick'),
    (SELECT MAX(date) FROM live.sec_alloc_live_prev_ref WHERE benchmark_code = $1::text)
)::date AS max_date
FROM (
    SELECT MAX(date) AS date, 'tick' AS src
    FROM live.sec_alloc_live_attribution
    WHERE benchmark_code = $1::text
) s
`;

const WEIGHTED_AVAILABLE_SQL = `
SELECT EXISTS (
    SELECT 1
    FROM live.sec_alloc_live_prev_ref
    WHERE benchmark_code = $1::text
      AND date = $2::date
      AND code_trading_amount_weight IS NOT NULL
) AS weighted_available
`;

export async function getSecAllocLiveAttribution(
  benchmarkCode: string,
  date: string | null,
  time: string,
): Promise<SecAllocLiveAttributionResponse> {
  let targetDate = date;
  if (!targetDate) {
    const dateRows = await queryRows<DbDateRow>(LATEST_DATE_SQL, [benchmarkCode]);
    targetDate = formatDate(dateRows[0]?.max_date ?? null);
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
