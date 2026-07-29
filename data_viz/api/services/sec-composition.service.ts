/**
 * Sec Composition service — queries stats.sec_composition (ETF + index holdings)
 * JOIN stock_industry_map.
 *
 * sec_composition stores:
 *   - Full composition (ALL holdings, rank 1..N) for ETFs (source_type='etf')
 *   - Index composition (ALL constituents) for CSI indices (source_type='index')
 *
 * Lookup order for a requested ETF:
 *   1. ETF holdings (source_type='etf'). If any rows exist → return them.
 *   2. If the ETF has NO holdings, look up its tracking index in
 *      stats.etf_meta (index_code, populated by build_etf_classification.py
 *      from _classification.INDUSTRY_INDEX_MAP) and return the index's
 *      composition (source_type='index') as a fallback.
 *
 * Returns:
 *   - holdings: ALL holdings for the ETF/index with industry + L1 sector classification.
 *   - source:   "full"  = all ETF holdings available,
 *               "index" = ETF had no holdings; tracking index composition used.
 *   - index_source: populated only when source === "index" (which index was used).
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

interface IndexMetaRow extends QueryResultRow {
  index_code: string;
  index_name: string;
}

/** Regex to strip the exchange suffix from a code (159001.SZ → 159001). */
const SUFFIX_RE = /\.(SZ|SS|SH)$/;

/** Map a DB row to a SecCompositionHolding. */
function toHolding(r: DbCompositionRow): SecCompositionHolding {
  return {
    stock_code: r.stock_code,
    stock_name: r.stock_name ?? "",
    weight_pct: toNum(r.weight_pct) ?? 0,
    industry: r.industry ?? "未分类",
    sector_id: r.sector_id ?? "OTHER",
    sector_label: r.sector_label ?? "其他",
  };
}

/**
 * Fetch the latest holdings snapshot for a given code + source_type.
 *
 * For ETFs (source_type='etf') the DB stores codes WITH exchange suffix
 * (159001.SZ), so the suffix is stripped on the DB side to match the bare
 * input code.  For indices (source_type='index') the DB stores bare 6-digit
 * codes — matched directly.
 *
 * @param code       bare code (suffix-stripped for ETFs, or bare index code)
 * @param sourceType 'etf' or 'index'
 */
async function fetchHoldings(
  code: string,
  sourceType: "etf" | "index",
): Promise<DbCompositionRow[]> {
  // The code predicate differs by source_type — static strings, no injection
  // risk (sourceType is a hardcoded literal, not user input).
  const codePredicate =
    sourceType === "etf"
      ? "REGEXP_REPLACE(h.code, '\\.(SZ|SS|SH)$', '') = $1"
      : "h.code = $1";
  const latestCodePredicate =
    sourceType === "etf"
      ? "REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $1"
      : "code = $1";

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
         WHERE source_type = $2
           AND ${latestCodePredicate}
      ) latest
      LEFT JOIN stats.stock_industry_map sim
        ON sim.stock_code = h.stock_code
     WHERE h.source_type = $2
       AND ${codePredicate}
       AND h.snapshot_date = latest.snap_date
     ORDER BY h.weight_pct DESC
  `;
  return queryRows<DbCompositionRow>(sql, [code, sourceType]);
}

/**
 * Look up the primary tracking index for an ETF from stats.etf_meta.
 * Returns null if the ETF has no associated index (index_code is empty).
 *
 * etf_meta.code is stored WITH exchange suffix (e.g. 159530.SZ), so the
 * suffix is stripped on the DB side to match the bare input code.
 */
async function fetchTrackingIndex(
  strippedEtfCode: string,
): Promise<{ code: string; name: string } | null> {
  const rows = await queryRows<IndexMetaRow>(
    `SELECT index_code, index_name
       FROM stats.etf_meta
      WHERE REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $1
        AND index_code <> ''
      LIMIT 1`,
    [strippedEtfCode],
  );
  if (rows.length === 0) return null;
  return { code: rows[0].index_code, name: rows[0].index_name ?? "" };
}

/**
 * Fetch the latest holdings snapshot for the given security from stats.sec_composition.
 *
 * Lookup order:
 *   1. ETF holdings (source_type='etf'). If any rows exist → return them.
 *   2. If the ETF has NO holdings, look up its tracking index in
 *      stats.etf_meta and return the index's composition (source_type='index').
 *   3. If neither ETF holdings nor a tracking index is found, try a direct
 *      index lookup (source_type='index') with the bare code. This lets
 *      callers pass a bare index code (e.g. "000300" or "H30007") directly
 *      — used by the Index Baseline page, which has no etf_meta entry.
 *
 * Returns holdings (all) and the source type
 * ("full" = ETF holdings, "index" = tracking/raw index composition).
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
      source: "full",
    };
  }

  // 1. Try ETF holdings first.
  const etfRows = await fetchHoldings(stripped, "etf");
  if (etfRows.length > 0) {
    const holdings = etfRows.map(toHolding);
    return {
      code: etfRows[0].code,
      snapshot_date: formatDate(etfRows[0].snapshot_date),
      holdings,
      source: "full",
    };
  }

  // 2. Fallback: ETF has no holdings → use its tracking index's composition.
  const idx = await fetchTrackingIndex(stripped);
  if (idx) {
    const idxRows = await fetchHoldings(idx.code, "index");
    if (idxRows.length > 0) {
      const holdings = idxRows.map(toHolding);
      return {
        code: codeParam,
        snapshot_date: formatDate(idxRows[0].snapshot_date),
        holdings,
        source: "index",
        index_source: { code: idx.code, name: idx.name },
      };
    }
  }

  // 3. Direct index lookup — caller passed a bare index code with no
  //    associated etf_meta entry (e.g. the Index Baseline page).
  const directIdxRows = await fetchHoldings(stripped, "index");
  if (directIdxRows.length > 0) {
    const holdings = directIdxRows.map(toHolding);
    return {
      code: codeParam,
      snapshot_date: formatDate(directIdxRows[0].snapshot_date),
      holdings,
      source: "index",
    };
  }

  // No ETF holdings and no usable index composition.
  return {
    code: codeParam,
    snapshot_date: "",
    holdings: [],
    source: "full",
  };
}
