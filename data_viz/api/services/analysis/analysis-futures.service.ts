/**
 * analysis-futures.service.ts — backend service for futures_ext analysis table.
 *
 * Reads metrics pre-computed by analyze/futures/ and stored in analysis.futures_ext.
 *
 * Computed columns (analyze/futures/compute.py):
 *   gap_price_vs_underlying       : (futures_close - underlying_price) / underlying_price
 *   corr_price_vs_underlying       : 20d rolling correlation of futures & underlying close
 */

import type { QueryResultRow } from "pg";
import { queryRows } from "../../lib/db.js";

export interface FuturesExtRow {
  date: string;
  code: string;
  gap_price_vs_underlying: number | null;
  corr_price_vs_underlying: number | null;
}

export interface FuturesExtResponse {
  product: string;
  gapByCodeDate: Map<string, Map<string, number | null>>;
  corrByCodeDate: Map<string, Map<string, number | null>>;
  rows: FuturesExtRow[];
}

/**
 * Fetch all futures_ext rows for a given product (index or bond).
 *
 * Only rows whose contract_code starts with the product prefix (e.g. "IF2409")
 * are returned, by joining against stats.futures_identity.
 */
export async function getFuturesExt(product: string): Promise<FuturesExtResponse> {
  const q = `
    SELECT
        fe.date,
        fe.code,
        fe.gap_price_vs_underlying,
        fe.corr_price_vs_underlying
    FROM analysis.futures_ext fe
    JOIN stats.futures_identity fi
      ON fi.date = fe.date AND fi.code = fe.code
    WHERE fi.product_code = $1
    ORDER BY fe.code, fe.date
  `;
  const rows = await queryRows(q, [product]);
  const mapped: FuturesExtRow[] = rows.map((r: QueryResultRow) => ({
    date: String(r.date),
    code: String(r.code),
    gap_price_vs_underlying:
      r.gap_price_vs_underlying != null
        ? Number(r.gap_price_vs_underlying)
        : null,
    corr_price_vs_underlying:
      r.corr_price_vs_underlying != null
        ? Number(r.corr_price_vs_underlying)
        : null,
  }));

  // Build nested Maps: code -> date -> value
  const gapMap = new Map<string, Map<string, number | null>>();
  const corrMap = new Map<string, Map<string, number | null>>();

  for (const r of mapped) {
    let dm = gapMap.get(r.code);
    if (!dm) { dm = new Map(); gapMap.set(r.code, dm); }
    dm.set(r.date, r.gap_price_vs_underlying);

    let cm = corrMap.get(r.code);
    if (!cm) { cm = new Map(); corrMap.set(r.code, cm); }
    cm.set(r.date, r.corr_price_vs_underlying);
  }

  return {
    product,
    gapByCodeDate: gapMap,
    corrByCodeDate: corrMap,
    rows: mapped,
  };
}
