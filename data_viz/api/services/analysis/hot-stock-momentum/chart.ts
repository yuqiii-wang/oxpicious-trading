/**
 * Hot Stock Momentum chart — getHotStockMomentumChart, one security's
 * daily stats close + trading amount with the hot-stock momentum metric
 * LEFT JOINed from analysis.hot_stock_momentum (NULL on every row until
 * the placeholder pipeline lands), with the name looked up from the
 * sec_type's identity source.
 */
import { queryRows, formatDate, toNum } from "@api/lib/db";
import type { QueryResultRow } from "pg";
import { codeVariants } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import { LABEL_CODE } from "@api/services/label-codes";
import type {
  HotStockMomentumChartResponse,
  HotStockMomentumChartRow,
} from "@shared/types";
import { normalizeIndexIndustrySecType } from "@api/services/analysis/classification-universe";

interface DbChartRow extends QueryResultRow {
  date: Date | string;
  close: number | string | null;
  trading_amount: number | string | null;
  momentum: number | string | null;
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

export async function getHotStockMomentumChart(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<HotStockMomentumChartResponse> {
  const secType = normalizeIndexIndustrySecType(rawSecType);
  const target = stripped(rawCode);

  // The code predicate is repeated inside the LEFT JOIN ON clause on
  // purpose (the analysis.pe chart pattern): without it the planner
  // merge-joins by sorting the ENTIRE metric table and degenerates.
  const chartSql = `
    SELECT
      b.date,
      b.close,
      b.trading_amount,
      m.momentum
    ${CHART_FROM_CLAUSE[secType]}
    LEFT JOIN analysis.hot_stock_momentum m
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

  const rows: HotStockMomentumChartRow[] = chartRows.map((r) => ({
    date: formatDate(r.date),
    close: toNum(r.close),
    trading_amount: toNum(r.trading_amount),
    momentum: toNum(r.momentum),
  }));

  return { code: target, name, rows };
}
