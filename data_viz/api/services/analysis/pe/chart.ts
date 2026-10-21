/**
 * PE chart — getPeChart, one asset's daily close + pe + dividend_yield
 * series, the close drawn from the sec_type-appropriate stats source.
 * Split out of the former monolithic pe.ts.
 */
import { queryRows, formatDate, toNum } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import { codeVariants } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import type {
  PeSecType,
  PeChartResponse,
  PeChartRow,
} from "@shared/types";
import { normalizeSecType, SEC_SOURCES, buildNameSql } from "./_shared.js";

interface DbChartRow extends QueryResultRow {
  date: Date | string;
  close: number | null;
  pe: number | null;
  dividend_yield: number | null;
}

// ----------------------------------------------------------------------------
//  getPeChart — daily time series for one asset.
//
//  Drives from the asset-appropriate close source (etf_basic_stats +
//  etf_adjustment for ETFs; index_basic_stats for indices;
//  stock_basic_stats for stocks) and LEFT JOINs the two split metric
//  tables — analysis.pe and analysis.dividends — so the response keeps
//  one row per close day with nulls where a metric is undefined.
// ----------------------------------------------------------------------------
function buildChartSql(secType: PeSecType): string {
  const src = SEC_SOURCES[secType];
  // The code predicate is repeated inside BOTH LEFT JOIN ON clauses on
  // purpose: without it the planner merge-joins by sorting the ENTIRE
  // analysis.pe / analysis.dividends tables (the join condition alone
  // defeats the ANY-array pushdown) and degenerates into a near-cartesian
  // product — measured 2.67M rows / 30s for ONE index code (2026-09-29).
  // Semantically identical (b.code ∈ ANY($1) already implies p.code ∈
  // ANY($1)); each side becomes an index probe (~60ms total).
  return `
    SELECT
      b.date,
      ${src.closeExpr} AS close,
      p.pe,
      dy.dividend_yield
    ${src.chartFromClause}
    LEFT JOIN analysis.pe p
      ON p.code = b.code AND p.date = b.date AND p.code = ANY($1::text[])
    LEFT JOIN analysis.dividends dy
      ON dy.code = b.code AND dy.date = b.date AND dy.code = ANY($1::text[])
    WHERE b.close IS NOT NULL
      AND b.code = ANY($1::text[])
    ORDER BY b.date ASC
  `;
}

export async function getPeChart(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<PeChartResponse> {
  const secType = normalizeSecType(rawSecType);
  const target = stripped(rawCode);

  const [chartRows, nameRows] = await Promise.all([
    queryRows<DbChartRow>(buildChartSql(secType), [codeVariants(target)]),
    queryRows<{ name: string | null }>(buildNameSql(secType), [codeVariants(target)]),
  ]);

  const name = nameRows[0]?.name ?? "";

  const rows: PeChartRow[] = chartRows.map((r) => ({
    date: formatDate(r.date),
    close: toNum(r.close),
    pe: toNum(r.pe),
    dividend_yield: toNum(r.dividend_yield),
  }));

  return { code: target, name, rows };
}
