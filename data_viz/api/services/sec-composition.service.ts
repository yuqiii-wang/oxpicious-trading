/**
 * Sec Composition service — queries stats.sec_composition (ETF + index holdings)
 * JOIN sec_classification (type='stock') for L1/L2 classification labels.
 *
 * sec_composition stores:
 *   - Full composition (ALL holdings, rank 1..N) for ETFs (source_type='etf')
 *   - Index composition (ALL constituents) for CSI indices (source_type='index')
 *
 * Lookup order for a requested ETF:
 *   1. ETF holdings (source_type='etf'). If any rows exist → return them.
 *   2. If the ETF has NO holdings, look up its tracking index in
 *      stats.sec_classification (type='etf', parent_index_code populated by
 *      build_classification.py from the CSIndex CSV) and return the index's
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
  LinkedEtfsResponse,
  LinkedEtfRow,
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
           COALESCE(sc.industry_label, '未分类')  AS industry,
           COALESCE(sc.sector_id, 'OTHER')        AS sector_id,
           COALESCE(sc.sector_label, '其他')      AS sector_label
      FROM stats.sec_composition h
      CROSS JOIN (
        SELECT MAX(snapshot_date) AS snap_date
          FROM stats.sec_composition
         WHERE source_type = $2
           AND ${latestCodePredicate}
      ) latest
      LEFT JOIN LATERAL (
        SELECT sc.sector_id, sc.industry_id, sc.sector_label, sc.industry_label
          FROM stats.sec_classification sc
         WHERE sc.code = h.stock_code AND sc.type = 'stock'
         ORDER BY sc.parent_index_weight DESC NULLS LAST, sc.parent_index_code
         LIMIT 1
      ) sc ON true
     WHERE h.source_type = $2
       AND ${codePredicate}
       AND h.snapshot_date = latest.snap_date
     ORDER BY h.weight_pct DESC
  `;
  return queryRows<DbCompositionRow>(sql, [code, sourceType]);
}

/**
 * Look up the primary tracking index for an ETF from stats.sec_classification.
 * Returns null if the ETF has no associated index (parent_index_code is empty).
 *
 * sec_classification.code is stored WITH exchange suffix (e.g. 159530.SZ), so the
 * suffix is stripped on the DB side to match the bare input code.  The index
 * name is resolved via a self-JOIN on the index's own sec_classification row.
 */
async function fetchTrackingIndex(
  strippedEtfCode: string,
): Promise<{ code: string; name: string } | null> {
  const rows = await queryRows<IndexMetaRow>(
    `SELECT sc.parent_index_code AS index_code,
            si.name               AS index_name
       FROM stats.sec_classification sc
       LEFT JOIN stats.sec_classification si
         ON si.code = sc.parent_index_code AND si.type = 'index'
      WHERE sc.type = 'etf'
        AND REGEXP_REPLACE(sc.code, '\\.(SZ|SS|SH)$', '') = $1
        AND sc.parent_index_code <> ''
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
 *      stats.sec_classification and return the index's composition (source_type='index').
 *   3. If neither ETF holdings nor a tracking index is found, try a direct
 *      index lookup (source_type='index') with the bare code. This lets
 *      callers pass a bare index code (e.g. "000300" or "H30007") directly
 *      — used by the Index Baseline page, which has no sec_classification entry.
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
  //    associated sec_classification entry (e.g. the Index Baseline page).
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

// ----------------------------------------------------------------------------
//  Linked ETFs — ETFs tracking a given index (stats.sec_classification
//  type='etf' WHERE parent_index_code = $1), enriched with the latest close
//  price + trading-day count from stats.v_etf_margin.
//
//  Used by the Index Baseline page's "Linked ETFs" expansion shown beside
//  the Composition pie. The input is a bare index code (e.g. "000300") as
//  stored on sec_classification.code for type='index' rows.
// ----------------------------------------------------------------------------
interface DbLinkedEtfRow extends QueryResultRow {
  code: string;
  name: string | null;
  exchange: string | null;
  sector_label: string | null;
  industry_label: string | null;
  latest_date: string | null;
  latest_close: number | null;
  latest_trading_amount: number | null;
  aum_yi: number | null;
  n_days: number | null;
}

export async function getLinkedEtfs(
  indexCodeParam: string,
): Promise<LinkedEtfsResponse> {
  const indexCode = indexCodeParam.replace(SUFFIX_RE, "").trim();
  if (!indexCode) {
    return {
      index_code: indexCodeParam,
      etfs: [],
      total_etf_trading_amount: null,
      total_etf_trading_amount_ma5: null,
      total_etf_trading_amount_date: "",
    };
  }

  // Three CTEs:
  //   linked_etfs — ETF sec_classification rows whose parent_index_code = $1.
  //                 Also pulls aum_yi (net asset value in 亿元) which is
  //                 populated from the etf_index_map_all_*.csv by
  //                 build_classification.py.  Available for ALL ETFs (SSE +
  //                 SZSE), unlike v_etf_margin.trading_amount (SZSE only).
  //   latest      — DISTINCT ON (code) picks the most-recent v_etf_margin row
  //                 per ETF (gives latest_date + latest_close).
  //   counts      — per-ETF trading-day count from v_etf_margin.
  const sql = `
    WITH linked_etfs AS (
      SELECT sc.code, sc.name, sc.exchange, sc.sector_label, sc.industry_label, sc.aum_yi
        FROM stats.sec_classification sc
       WHERE sc.type = 'etf'
         AND sc.parent_index_code = $1
    ),
    latest AS (
      SELECT DISTINCT ON (code) code, date AS latest_date, close AS latest_close, trading_amount AS latest_trading_amount
        FROM stats.v_etf_margin
       WHERE code IN (SELECT code FROM linked_etfs)
       ORDER BY code, date DESC
    ),
    counts AS (
      SELECT code, COUNT(*) AS n_days
        FROM stats.v_etf_margin
       WHERE code IN (SELECT code FROM linked_etfs)
       GROUP BY code
    )
    SELECT le.code,
           le.name,
           le.exchange,
           COALESCE(le.sector_label,    '其他')   AS sector_label,
           COALESCE(le.industry_label, '未分类')  AS industry_label,
           COALESCE(la.latest_date::text, '')    AS latest_date,
           la.latest_close,
           la.latest_trading_amount,
           le.aum_yi,
           COALESCE(co.n_days, 0)                AS n_days
      FROM linked_etfs le
      LEFT JOIN latest  la ON la.code = le.code
      LEFT JOIN counts  co ON co.code = le.code
     ORDER BY le.aum_yi DESC NULLS LAST, n_days DESC, le.code
  `;
  const [rows, extRows] = await Promise.all([
    queryRows<DbLinkedEtfRow>(sql, [indexCode]),
    // Fetch the latest index_exts row for this index — gives the precomputed
    // total_etf_trading_amount (Σ ETF turnover tracking the index, yuan) and its 5-day
    // moving average (total_etf_trading_amount_ma5). Both are NULL when the index has
    // no tracking ETF (no index_exts row).
    queryRows<{
      total_etf_trading_amount: number | null;
      total_etf_trading_amount_ma5: number | null;
      date: string | null;
    }>(
      `SELECT total_etf_trading_amount, total_etf_trading_amount_ma5, date::text AS date
         FROM stats.index_exts
        WHERE code = $1
        ORDER BY date DESC
        LIMIT 1`,
      [indexCode],
    ),
  ]);
  const ext = extRows[0];
  return {
    index_code: indexCode,
    etfs: rows.map<LinkedEtfRow>((r) => ({
      code: r.code,
      name: r.name ?? "",
      exchange: r.exchange ?? "",
      sector_label: r.sector_label ?? "其他",
      industry_label: r.industry_label ?? "未分类",
      latest_date: formatDate(r.latest_date) || "",
      latest_close: toNum(r.latest_close),
      // Trading amount (成交金额) from v_etf_margin.trading_amount (yuan).
      latest_trading_amount: toNum(r.latest_trading_amount),
      aum_yi: toNum(r.aum_yi),
      n_days: parseInt(String(r.n_days ?? 0), 10) || 0,
    })),
    total_etf_trading_amount: toNum(ext?.total_etf_trading_amount),
    total_etf_trading_amount_ma5: toNum(ext?.total_etf_trading_amount_ma5),
    total_etf_trading_amount_date: ext?.date ?? "",
  };
}
