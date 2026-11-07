/**
 * MA-Spread codes list — listMovAveSpreadCodes, one row per asset code with
 * first/last date, n_dates, and the latest snapshot of all 9 gap_values.
 * Split out of the former monolithic mov-ave-spreads.ts.
 */
import { formatDate, toNum } from "@api/lib/db";
import { querySqlView } from "@api/lib/sql-views";
import type { QueryResultRow } from "pg";
import { stripExchangeSuffix, matchesExchange } from "@api/lib/classify-etf";
import { stripped } from "@api/services/analysis/_shared";
import { matchesClassification } from "@api/services/_shared";
import type {
  MovAveSpreadCodeRow,
  MovAveSpreadCodesResponse,
  MovAveSpreadLatestGap,
} from "@shared/types";
import { normalizeSecType, secTypeParam } from "./sec-sources.js";
import { PAIR_ORDER } from "./pairs-config.js";
import { MA_SPREAD_META_TYPE, type DbMaSpreadMetaRow } from "./meta.js";

const CODES_LIST_VIEW = "data_viz/api/services/analysis/mov-ave-spreads/codes/codes_list";
const META_CLASSIFIED_ROWS_VIEW =
  "data_viz/api/services/analysis/mov-ave-spreads/meta/classified_rows";

// ----------------------------------------------------------------------------
//  listMovAveSpreadCodes — one row per asset code with first/last date,
//  n_dates, and the latest snapshot of all 9 gap_values (for sparkline /
//  sort). Reads the PRECOMPUTED per-code summary table
//  analysis.mov_ave_spreads_codes (maintained by analysis.mov_ave_spread's
//  refresh_codes_summary after every detail write pass). The former live
//  aggregate — GROUP BY code over 18 MIN/MAX columns of the detail table +
//  per-code LATERAL latest-row and name lookups — took 7-14s on the stock
//  universe (~6.8M rows) and gated the page's first open; this is the same
//  result from a small indexed table (~10ms).
// ----------------------------------------------------------------------------
export interface DbCodeRow extends QueryResultRow {
  code: string;
  name: string;
  first_date: Date | string;
  last_date: Date | string;
  n_dates: number;
  // 9 latest gap columns from the wide detail row at MAX(date).
  price_vs_ma5: number | null;
  price_vs_ma20: number | null;
  price_vs_ma60: number | null;
  price_vs_ma120: number | null;
  price_vs_ma255: number | null;
  ma5_vs_ma20: number | null;
  ma5_vs_ma60: number | null;
  ma5_vs_ma120: number | null;
  ma5_vs_ma255: number | null;
  // All-time max gain / max loss across all 9 pairs (fractional).
  max_gain: number | null;
  max_loss: number | null;
  max_spread: number | null;
}

/** The codes list behind listMovAveSpreadCodes — also the industry branch's
 *  entity-universe source in themes.ts (one row per industry code; the
 *  classification trees join it with the grouped sec_classification read).
 *  The SELECT lives in the sql view
 *  data_viz/api/services/analysis/mov-ave-spreads/codes/codes_list. */

export async function listMovAveSpreadCodes(
  rawSecType: string | undefined | null,
  sector?: string | null,
  industry?: string | null,
  strategy?: string | null,
  theme?: string | null,
  rawExchange?: string | null,
): Promise<MovAveSpreadCodesResponse> {
  const secType = normalizeSecType(rawSecType);
  const sectorFilter = (sector ?? "").trim();
  const industryFilter = (industry ?? "").trim();
  const strategyFilter = (strategy ?? "").trim();
  const themeFilter = (theme ?? "").trim();
  const hasClassFilter = !!(sectorFilter || industryFilter || strategyFilter || themeFilter);
  const exFilter = (rawExchange ?? "").trim() || null;
  // Build the meta map when EITHER a classification filter or an exchange
  // filter is active — both need the sec_classification row to decide.
  // Industry codes have NO sec_classification rows (the industry IS the
  // classification top), so both filter families are neutralized for it.
  const needMeta = secType !== "industry"
    && (hasClassFilter || !!exFilter);

  const rows = await querySqlView<DbCodeRow>(CODES_LIST_VIEW,
    { sec_type: secTypeParam(secType) });

  // When a classification filter is active, fetch the meta rows (same query as
  // listMovAveSpreadThemes) and build a code → classification map so
  // matchesClassification() can decide which codes to include. Industry and
  // strategy filters are mutually exclusive (handled by matchesClassification).
  let classMap: Map<string, DbMaSpreadMetaRow> | null = null;
  if (needMeta) {
    const metaType = MA_SPREAD_META_TYPE[secType];
    const metaRows = await querySqlView<DbMaSpreadMetaRow>(META_CLASSIFIED_ROWS_VIEW,
      { sec_type: secTypeParam(secType), m_type: metaType });
    classMap = new Map<string, DbMaSpreadMetaRow>();
    for (const m of metaRows) {
      const code = stripExchangeSuffix(m.code);
      if (!code) continue;
      classMap.set(code, m);
    }
  }

  const codes: MovAveSpreadCodeRow[] = [];
  for (const r of rows) {
    const code = stripped(r.code);
    if (classMap) {
      const meta = classMap.get(code);
      if (hasClassFilter && (!meta || !matchesClassification(meta, sectorFilter, industryFilter, strategyFilter, themeFilter))) {
        continue;
      }
      // Exchange filter: codes without a sec_classification row (meta is null)
      // have no exchange info and are excluded when a filter is active — same
      // behavior as listIndexThemes (COALESCE(exchange, '') fails the match).
      if (exFilter && (!meta || !matchesExchange(meta.exchange, exFilter))) {
        continue;
      }
    }
    // Build the latest_gaps array from the 9 wide gap columns.
    const latestGaps: MovAveSpreadLatestGap[] = PAIR_ORDER.map(
      ([maShort, maLong, gapCol]) => ({
        ma_short: maShort,
        ma_long: maLong,
        gap_value: toNum(r[gapCol as keyof DbCodeRow]),
      }),
    );
    codes.push({
      code,
      name: r.name ?? "",
      first_date: formatDate(r.first_date),
      last_date: formatDate(r.last_date),
      n_dates: Number(r.n_dates) || 0,
      latest_gaps: latestGaps,
      max_gain: toNum(r.max_gain),
      max_loss: toNum(r.max_loss),
      max_spread: toNum(r.max_spread),
    });
  }
  return { codes };
}
