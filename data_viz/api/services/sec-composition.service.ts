/**
 * Sec Composition service — queries stats.sec_composition (ALL ETF holdings)
 * JOIN stock_industry_map.
 *
 * sec_composition stores:
 *   - Full composition (ALL holdings, rank 1..N) for ~65 ETFs (source_type='etf')
 *   - Top-5 (rank 1-5) for ~505 ETFs without full composition data (source_type='etf')
 *   - Index composition (ALL constituents) for CSI indices (source_type='index')
 *
 * This service queries ETF holdings only (source_type='etf').
 *
 * Returns:
 *   - holdings: ALL holdings for the ETF with industry + L1 sector classification.
 *   - top5:     Top 5 by weight (for the text list display).
 *   - source:   "full" if the ETF has more than 5 holdings (complete pie chart),
 *               "top5" if only top 5 are available.
 */
import { queryRows, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  SecCompositionResponse,
  SecCompositionHolding,
} from "../../shared/types.js";

interface DbCompositionRow extends QueryResultRow {
  snapshot_date: string;
  code: string;
  stock_code: string;
  stock_name: string | null;
  weight_pct: number | null;
  industry: string | null;
  sector_id: string | null;
  sector_label: string | null;
}

/** Regex to strip the exchange suffix from a code (159001.SZ → 159001). */
const SUFFIX_RE = /\.(SZ|SS|SH)$/;

/**
 * Fetch the latest holdings snapshot for the given security from stats.sec_composition.
 *
 * Returns holdings (all), top5 (text list), and the source type
 * ("full" = all holdings available, "top5" = only top 5).
 */
export async function getSecComposition(
  codeParam: string,
): Promise<SecCompositionResponse> {
  const stripped = codeParam.replace(SUFFIX_RE, "").trim();
  if (!stripped) {
    return {
      code: codeParam,
      snapshot_date: "",
      holdings: [],
      top5: [],
      source: "top5",
    };
  }

  const sql = `
    SELECT h.snapshot_date,
           h.code,
           h.stock_code,
           h.stock_name,
           h.weight_pct,
           COALESCE(sim.industry, '未分类')      AS industry,
           COALESCE(sim.sector_id, 'OTHER')     AS sector_id,
           COALESCE(sim.sector_label, '其他')    AS sector_label
      FROM stats.sec_composition h
      CROSS JOIN (
        SELECT MAX(snapshot_date) AS snap_date
          FROM stats.sec_composition
         WHERE source_type = 'etf'
           AND REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $1
      ) latest
      LEFT JOIN stats.stock_industry_map sim
        ON sim.stock_code = h.stock_code
     WHERE h.source_type = 'etf'
       AND REGEXP_REPLACE(h.code, '\\.(SZ|SS|SH)$', '') = $1
       AND h.snapshot_date = latest.snap_date
     ORDER BY h.weight_pct DESC
  `;
  const rows = await queryRows<DbCompositionRow>(sql, [stripped]);

  if (rows.length === 0) {
    return {
      code: codeParam,
      snapshot_date: "",
      holdings: [],
      top5: [],
      source: "top5",
    };
  }

  const holdings: SecCompositionHolding[] = rows.map((r) => ({
    stock_code: r.stock_code,
    stock_name: r.stock_name ?? "",
    weight_pct: toNum(r.weight_pct) ?? 0,
    industry: r.industry ?? "未分类",
    sector_id: r.sector_id ?? "OTHER",
    sector_label: r.sector_label ?? "其他",
  }));

  // top5 = first 5 by weight (holdings is already sorted by weight DESC)
  const top5 = holdings.slice(0, 5);

  // source = "full" when more than 5 holdings exist (complete composition),
  // "top5" when only top 5 are available.
  const source: "full" | "top5" = holdings.length > 5 ? "full" : "top5";

  return {
    code: rows[0].code,
    snapshot_date: formatDate(rows[0].snapshot_date),
    holdings,
    top5,
    source,
  };
}
