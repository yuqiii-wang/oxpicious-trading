/**
 * Market Crowdedness chart — getMarketCrowdednessChart, one security's
 * daily stats close + trading amount with the crowdedness metric LEFT
 * JOINed from analysis.market_crowdedness (the percentile-normalized
 * multi-pillar crowdedness_score with its four RAW pillars —
 * concentration / flow_change / comovement / valuation — and their
 * *_pct history percentiles plus the hot-set names as tooltip
 * evidence), with the name looked up from the sec_type's identity
 * source.
 */
import { queryRows, formatDate, toNum } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import { codeVariants } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import { LABEL_CODE } from "@api/services/label-codes";
import type {
  MarketCrowdednessChartResponse,
  MarketCrowdednessChartRow,
} from "@shared/types";
import { normalizeMcSecType } from "./_shared.js";

interface DbChartRow extends QueryResultRow {
  date: Date | string;
  close: number | string | null;
  trading_amount: number | string | null;
  crowdedness_score: number | string | null;
  concentration: number | string | null;
  flow_change: number | string | null;
  comovement: number | string | null;
  valuation: number | string | null;
  top1_share: number | string | null;
  top5_share: number | string | null;
  top10_share: number | string | null;
  effective_n: number | string | null;
  concentration_pct: number | string | null;
  flow_pct: number | string | null;
  comovement_pct: number | string | null;
  valuation_pct: number | string | null;
  top_stocks: string | null;
}

// ----------------------------------------------------------------------------
//  Per-sec_type close/amount source — the same sources analysis.pe reads
//  (industry: the pool-'all' slice of industry_basic_stats, renamed to
//  the generic b.code / b.close / b.trading_amount bindings).
// ----------------------------------------------------------------------------
const CHART_FROM_CLAUSE = {
  index: "FROM stats.index_basic_stats b",
  industry:
    "FROM (SELECT industry_id AS code, date, mean_close AS close,\n" +
    "              total_trading_amount AS trading_amount\n" +
    "        FROM stats.industry_basic_stats\n" +
    "        WHERE pool_size = 3 AND mean_close IS NOT NULL) b",
} as const;

/** Per-sec_type identity source for the display-name lookup. */
const IDENTITY_TABLE = {
  index: "stats.index_identity",
  industry: "stats.industry_identity",
} as const;

export async function getMarketCrowdednessChart(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MarketCrowdednessChartResponse> {
  const secType = normalizeMcSecType(rawSecType);
  const target = stripped(rawCode);

  // The code predicate is repeated inside the LEFT JOIN ON clause on
  // purpose (the analysis.pe chart pattern): without it the planner
  // merge-joins by sorting the ENTIRE metric table and degenerates.
  const chartSql = `
    SELECT
      b.date,
      b.close,
      b.trading_amount,
      m.crowdedness_score,
      m.concentration,
      m.flow_change,
      m.comovement,
      m.valuation,
      m.top1_share,
      m.top5_share,
      m.top10_share,
      m.effective_n,
      m.concentration_pct,
      m.flow_pct,
      m.comovement_pct,
      m.valuation_pct,
      m.top_stocks
    ${CHART_FROM_CLAUSE[secType]}
    LEFT JOIN analysis.market_crowdedness m
      ON m.code = b.code AND m.date = b.date AND m.sec_type = $2
      AND m.code = ANY($1::text[])
    WHERE b.close IS NOT NULL
      AND b.code = ANY($1::text[])
    ORDER BY b.date ASC
  `;
  const nameSql = `
    SELECT DISTINCT ON (code) code, name
    FROM ${IDENTITY_TABLE[secType]}
    WHERE code = ANY($1::text[])
    ORDER BY code, date DESC
  `;

  const [chartRows, nameRows] = await Promise.all([
    queryRows<DbChartRow>(chartSql, [codeVariants(target), LABEL_CODE.sec_type[secType]]),
    queryRows<{ code: string; name: string | null }>(nameSql, [codeVariants(target)]),
  ]);

  const name = nameRows[0]?.name ?? "";

  const rows: MarketCrowdednessChartRow[] = chartRows.map((r) => ({
    date: formatDate(r.date),
    close: toNum(r.close),
    trading_amount: toNum(r.trading_amount),
    crowdedness_score: toNum(r.crowdedness_score),
    concentration: toNum(r.concentration),
    flow_change: toNum(r.flow_change),
    comovement: toNum(r.comovement),
    valuation: toNum(r.valuation),
    top1_share: toNum(r.top1_share),
    top5_share: toNum(r.top5_share),
    top10_share: toNum(r.top10_share),
    effective_n: toNum(r.effective_n),
    concentration_pct: toNum(r.concentration_pct),
    flow_pct: toNum(r.flow_pct),
    comovement_pct: toNum(r.comovement_pct),
    valuation_pct: toNum(r.valuation_pct),
    top_stocks: r.top_stocks ?? null,
  }));

  return { code: target, name, rows };
}
