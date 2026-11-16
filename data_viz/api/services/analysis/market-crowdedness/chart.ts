/**
 * Market Crowdedness chart — getMarketCrowdednessChart, one security's
 * daily stats close + trading amount with the crowdedness metric LEFT
 * JOINed from analysis.market_crowdedness (the percentile-normalized
 * multi-pillar crowdedness_score with its four RAW pillars —
 * concentration / flow_change / comovement / valuation — and their
 * *_pct history percentiles plus the hot-set names as tooltip
 * evidence), with the name looked up from the sec_type's identity
 * source.
 *
 * With hotYear (a calendar year, e.g. 2025) the metric LEFT JOINs the
 * YEAR-PINNED variant from analysis.market_crowdedness_year instead —
 * the same pillar contract with the hot basket frozen to that year's
 * roster (analysis.market_crowdedness_hot_sets) — and the response is
 * SLICED to that one calendar year (2025-01-01..12-31; the stored
 * variant covers the pool's whole span for the percentile windows, but
 * the chart serves the year itself, counted forward from the data
 * start, never a trailing window ending at the latest date).
 */
import { formatDate, toNum } from "@api/lib/db";
import { querySqlView, sqlFrag } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import { codeVariants } from "@api/lib/classify-etf";
import { HttpError } from "@api/lib/http";
import { stripped } from "@api/services/analysis/_shared";
import { LABEL_CODE } from "@api/services/infra/label-codes";
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
  crowdedness_score_ma5: number | string | null;
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
//  the generic b.code / b.close / b.trading_amount bindings). Shared
//  with the hot-tech service (its template joins the same b bindings).
// ----------------------------------------------------------------------------
export const CHART_FROM_CLAUSE = {
  index: "FROM stats.index_basic_stats b",
  industry:
    "FROM (SELECT industry_id AS code, date, mean_close AS close,\n" +
    "              total_trading_amount AS trading_amount\n" +
    "        FROM stats.industry_basic_stats\n" +
    "        WHERE pool_size = 3 AND mean_close IS NOT NULL) b",
} as const;

/** Per-sec_type identity source for the display-name lookup. */
export const IDENTITY_TABLE = {
  index: "stats.index_identity",
  industry: "stats.industry_identity",
} as const;

/** Parse a calendar year ("YYYY", a plausible 1990..next year) —
 *  anything else is a 400. Shared by the chart and hot-years services;
 *  the value binds against hot_year INT columns. */
export function parseHotYear(raw: string): number {
  const m = /^\d{4}$/.exec(raw.trim());
  if (!m) {
    throw HttpError.invalidParam(
      "hot_year", `expected a YYYY year, got '${raw}'`);
  }
  const year = Number(raw.trim());
  const now = new Date().getFullYear();
  if (year < 1990 || year > now + 1) {
    throw HttpError.invalidParam(
      "hot_year", `'${raw}' is outside the plausible 1990..${now + 1} range`);
  }
  return year;
}

export async function getMarketCrowdednessChart(
  rawCode: string,
  rawSecType: string | undefined | null,
  rawHotYear?: string | null,
): Promise<MarketCrowdednessChartResponse> {
  const secType = normalizeMcSecType(rawSecType);
  const target = stripped(rawCode);
  const hotYear = rawHotYear ? parseHotYear(rawHotYear) : null;

  // The code predicate is repeated inside the LEFT JOIN ON clause on
  // purpose (the analysis.pe chart pattern): without it the planner
  // merge-joins by sorting the ENTIRE metric table and degenerates.
  // The SELECTs live in the sql views
  // …/market-crowdedness/chart/chart_series(_year).
  const codes = codeVariants(target);
  const [chartRows, nameRows] = await Promise.all([
    querySqlView<DbChartRow>(
      hotYear === null
        ? "data_viz/api/services/analysis/market-crowdedness/chart/chart_series"
        : "data_viz/api/services/analysis/market-crowdedness/chart/chart_series_year",
      {
        codes,
        sec_type: LABEL_CODE.sec_type[secType],
        ...(hotYear === null ? {} : { hot_year: hotYear }),
        from_clause: sqlFrag(CHART_FROM_CLAUSE[secType]),
      },
    ),
    querySqlView<{ code: string; name: string | null }>(
      "data_viz/api/services/analysis/market-crowdedness/chart/security_name",
      { codes, identity_table: sqlFrag(IDENTITY_TABLE[secType]) },
    ),
  ]);

  const name = nameRows[0]?.name ?? "";

  const rows: MarketCrowdednessChartRow[] = chartRows.map((r) => ({
    date: formatDate(r.date),
    close: toNum(r.close),
    trading_amount: toNum(r.trading_amount),
    crowdedness_score: toNum(r.crowdedness_score),
    crowdedness_score_ma5: toNum(r.crowdedness_score_ma5),
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
