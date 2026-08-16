/**
 * Analysis Futures service — queries analysis.futures_ext for basis and
 * correlation metrics per (date, code) for a given product.
 *
 * Endpoints:
 *   getFuturesExt(product) -> all rows for a product with gap + corr data
 *
 * Used by the Analysis > Derivatives > Futures page:
 *   - Provides gap_price_vs_underlying for the 1st plot tooltip
 *   - Provides corr_price_vs_underlying for the 2nd plot (correlation chart)
 */
import { queryRows, toNum } from "../lib/db.js";

export interface FuturesExtRow {
  date: string;
  code: string;
  gap_price_vs_underlying: number | null;
  corr_price_vs_underlying: number | null;
}

export interface FuturesExtResponse {
  product: string;
  /** date -> code -> gap_price_vs_underlying */
  gapByCodeDate: Map<string, Map<string, number | null>>;
  /** date -> code -> corr_price_vs_underlying */
  corrByCodeDate: Map<string, Map<string, number | null>>;
  /** All (date, code) pairs for the product */
  rows: FuturesExtRow[];
}

export async function getFuturesExt(
  product: string,
): Promise<FuturesExtResponse> {
  const rows = await queryRows(`
    SELECT
      e.date,
      e.code,
      e.gap_price_vs_underlying,
      e.corr_price_vs_underlying
    FROM analysis.futures_ext e
    JOIN stats.futures_identity i
      ON e.date = i.date AND e.code = i.code
    WHERE i.product_code = $1
    ORDER BY e.date, e.code
  `, [product]);

  const gapByCodeDate = new Map<string, Map<string, number | null>>();
  const corrByCodeDate = new Map<string, Map<string, number | null>>();
  const outRows: FuturesExtRow[] = [];

  for (const r of rows) {
    const date = String(r.date);
    const code = String(r.code);
    const gap = toNum(r.gap_price_vs_underlying);
    const corr = toNum(r.corr_price_vs_underlying);

    if (!gapByCodeDate.has(code)) gapByCodeDate.set(code, new Map());
    gapByCodeDate.get(code)!.set(date, gap);

    if (!corrByCodeDate.has(code)) corrByCodeDate.set(code, new Map());
    corrByCodeDate.get(code)!.set(date, corr);

    outRows.push({ date, code, gap_price_vs_underlying: gap, corr_price_vs_underlying: corr });
  }

  return {
    product,
    gapByCodeDate,
    corrByCodeDate,
    rows: outRows,
  };
}
