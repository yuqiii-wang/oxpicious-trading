/**
 * Valuation codes list — listValuationCodes, one row per code with
 * first/last date, n_dates and the latest pe + dividend_yield snapshot.
 * Serves the SHARED /api/analysis/valuation/codes route used by BOTH the
 * PE and Dividends pages.
 * Split out of the former monolithic pe.ts.
 */
import { formatDate, toNum } from "@api/lib/db";
import { querySqlView } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix, matchesExchange } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import { matchesClassification } from "@api/services/_shared";
import { LABEL_CODE } from "@api/services/label-codes";
import type {
  ValuationCodeRow,
  ValuationCodesResponse,
} from "@shared/types";
import { normalizeSecType, listPeMetaRows, type DbMetaRow } from "./_shared.js";

export interface DbCodeRow extends QueryResultRow {
  code: string;
  name: string;
  first_date: Date | string;
  last_date: Date | string;
  n_dates: number;
  latest_pe: number | null;
  latest_dividend_yield: number | null;
}

// ----------------------------------------------------------------------------
//  listValuationCodes — one row per code with first/last date, n_dates,
//  and the latest snapshot's pe + dividend_yield (for sparkline / sort).
//  Reads the PRECOMPUTED per-code summary analysis.pe_codes (maintained by
//  analysis.pe_and_dividends' refresh_pe_codes_summary after every detail
//  write pass). The former live aggregate — UNION + sort + GROUP BY over
//  analysis.pe ∪ analysis.dividends (1.3M rows index / 7.1M stock) plus
//  DISTINCT-ON latest-row and identity-name lookups — took 4.7s index /
//  35s stock per request and gated the PE / Dividends pages' first open;
//  this is the same result from a small indexed table (~10ms), the
//  mov_ave_spreads_codes pattern. The SELECT lives in the sql view
//  data_viz/api/services/analysis/pe/codes/codes_list.
// ----------------------------------------------------------------------------
export async function listValuationCodes(
  rawSecType: string | undefined | null,
  sector?: string | null,
  industry?: string | null,
  strategy?: string | null,
  theme?: string | null,
  rawExchange?: string | null,
): Promise<ValuationCodesResponse> {
  const secType = normalizeSecType(rawSecType);
  const sectorFilter = (sector ?? "").trim();
  const industryFilter = (industry ?? "").trim();
  const strategyFilter = (strategy ?? "").trim();
  const themeFilter = (theme ?? "").trim();
  const hasClassFilter = !!(sectorFilter || industryFilter || strategyFilter || themeFilter);
  const exFilter = (rawExchange ?? "").trim() || null;
  // Industry codes have NO sec_classification rows (the industry IS the
  // classification top) — both filter families are neutralized for it.
  const needMeta = secType !== "industry"
    && (hasClassFilter || !!exFilter);

  const rows = await querySqlView<DbCodeRow>(
    "data_viz/api/services/analysis/pe/codes/codes_list",
    { sec_type: LABEL_CODE.sec_type[secType] },
  );

  let classMap: Map<string, DbMetaRow> | null = null;
  if (needMeta) {
    const metaRows = await listPeMetaRows(secType);
    classMap = new Map<string, DbMetaRow>();
    for (const m of metaRows) {
      const code = stripExchangeSuffix(m.code);
      if (!code) continue;
      classMap.set(code, m);
    }
  }

  const codes: ValuationCodeRow[] = [];
  for (const r of rows) {
    const code = stripped(r.code);
    if (classMap) {
      const meta = classMap.get(code);
      if (hasClassFilter && (!meta || !matchesClassification(meta, sectorFilter, industryFilter, strategyFilter, themeFilter))) {
        continue;
      }
      if (exFilter && (!meta || !matchesExchange(meta.exchange, exFilter))) {
        continue;
      }
    }
    codes.push({
      code,
      name: r.name ?? "",
      first_date: formatDate(r.first_date),
      last_date: formatDate(r.last_date),
      n_dates: Number(r.n_dates) || 0,
      latest_pe: toNum(r.latest_pe),
      latest_dividend_yield: toNum(r.latest_dividend_yield),
    });
  }
  return { codes };
}
