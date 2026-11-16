/**
 * Market Crowdedness hot years — listMarketCrowdednessHotYears: the
 * year-pinned hot-set variants available for one security (DISTINCT
 * hot_year from analysis.market_crowdedness_hot_sets, the frozen
 * annual rosters behind analysis.market_crowdedness_year). The page
 * dropdown lists them newest-first; picking one re-fetches the chart
 * with hot_year set.
 */
import { querySqlView } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import { codeVariants } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import { LABEL_CODE } from "@api/services/infra/label-codes";
import type { MarketCrowdednessHotYearsResponse } from "@shared/types";
import { normalizeMcSecType } from "./_shared.js";

interface DbYearRow extends QueryResultRow {
  hot_year: number;
}

export async function listMarketCrowdednessHotYears(
  rawCode: string,
  rawSecType: string | undefined | null,
): Promise<MarketCrowdednessHotYearsResponse> {
  const secType = normalizeMcSecType(rawSecType);
  const target = stripped(rawCode);
  const codes = codeVariants(target);

  // The SELECT lives in the sql view
  // data_viz/api/services/analysis/market-crowdedness/hot-years/hot_years.
  const rows = await querySqlView<DbYearRow>(
    "data_viz/api/services/analysis/market-crowdedness/hot-years/hot_years",
    { codes, sec_type: LABEL_CODE.sec_type[secType] },
  );

  return { code: target, years: rows.map((r) => Number(r.hot_year)) };
}
