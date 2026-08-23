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
 *
 * Seasonal (quarterly) support — snapshot cadence in the DB is irregular
 * (SZSE ETF full-composition CSVs land roughly monthly; CSI index
 * closeweight files land per review). Every "by season" query therefore maps
 * a date to its CALENDAR QUARTER and picks the LATEST snapshot within that
 * quarter (no carry-forward: a quarter without a snapshot yields no data —
 * see getQuarterlyComposition for the per-quarter bar-chart feed).
 */
import { queryRows, formatDate, toNum } from "../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  SecCompositionResponse,
  SecCompositionHolding,
  LinkedEtfsResponse,
  LinkedEtfRow,
  SimilarIndicesResponse,
  SimilarIndexRow,
  QuarterlyCompositionResponse,
  QuarterlyCompositionQuarter,
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

/** Map a "YYYY-MM-DD" date to its quarter label ("2026Q2"). "" on bad input. */
function quarterLabel(dateStr: string): string {
  const m = /^(\d{4})-(\d{2})/.exec(dateStr);
  if (!m) return "";
  return `${m[1]}Q${Math.floor((parseInt(m[2], 10) - 1) / 3) + 1}`;
}

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
 * @param date       optional "YYYY-MM-DD". When provided, the snapshot is
 *                   constrained to the CALENDAR QUARTER containing the date
 *                   (latest snapshot within that quarter). When the quarter
 *                   has no snapshot, [] is returned.
 */
async function fetchHoldings(
  code: string,
  sourceType: "etf" | "index",
  date?: string,
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

  // Seasonal constraint: latest snapshot WITHIN the quarter containing `date`.
  // Both the `latest` subquery and the outer WHERE must carry the filter so
  // the chosen snapshot and the returned rows agree.
  const hasDate = typeof date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(date);
  const dateFilter = hasDate
    ? "AND snapshot_date >= date_trunc('quarter', $3::date) AND snapshot_date < date_trunc('quarter', $3::date) + interval '3 months'"
    : "";
  const outerDateFilter = hasDate
    ? "AND h.snapshot_date >= date_trunc('quarter', $3::date) AND h.snapshot_date < date_trunc('quarter', $3::date) + interval '3 months'"
    : "";
  const params: unknown[] = hasDate ? [code, sourceType, date] : [code, sourceType];

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
           ${dateFilter}
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
       ${outerDateFilter}
     ORDER BY h.weight_pct DESC
  `;
  return queryRows<DbCompositionRow>(sql, params);
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
 * When `date` is provided, every step is constrained to the calendar quarter
 * containing that date (latest snapshot within the quarter) — the "by season"
 * lookup used by the ETF Holdings page's quarterly bar → pie drill-down.
 *
 * Returns holdings (all) and the source type
 * ("full" = ETF holdings, "index" = tracking/raw index composition).
 */
export async function getSecComposition(
  codeParam: string,
  dateParam?: string,
): Promise<SecCompositionResponse> {
  const stripped = codeParam.replace(SUFFIX_RE, "").trim();
  const date = typeof dateParam === "string" ? dateParam.trim() : undefined;
  if (!stripped) {
    return {
      code: codeParam,
      snapshot_date: "",
      holdings: [],
      source: "full",
    };
  }

  // 1. Try ETF holdings first.
  const etfRows = await fetchHoldings(stripped, "etf", date);
  if (etfRows.length > 0) {
    const holdings = etfRows.map(toHolding);
    return {
      code: etfRows[0].code,
      snapshot_date: formatDate(etfRows[0].snapshot_date),
      quarter: quarterLabel(formatDate(etfRows[0].snapshot_date)),
      holdings,
      source: "full",
    };
  }

  // 2. Fallback: ETF has no holdings → use its tracking index's composition.
  const idx = await fetchTrackingIndex(stripped);
  if (idx) {
    const idxRows = await fetchHoldings(idx.code, "index", date);
    if (idxRows.length > 0) {
      const holdings = idxRows.map(toHolding);
      return {
        code: codeParam,
        snapshot_date: formatDate(idxRows[0].snapshot_date),
        quarter: quarterLabel(formatDate(idxRows[0].snapshot_date)),
        holdings,
        source: "index",
        index_source: { code: idx.code, name: idx.name },
      };
    }
  }

  // 3. Direct index lookup — caller passed a bare index code with no
  //    associated sec_classification entry (e.g. the Index Baseline page).
  const directIdxRows = await fetchHoldings(stripped, "index", date);
  if (directIdxRows.length > 0) {
    const holdings = directIdxRows.map(toHolding);
    return {
      code: codeParam,
      snapshot_date: formatDate(directIdxRows[0].snapshot_date),
      quarter: quarterLabel(formatDate(directIdxRows[0].snapshot_date)),
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
//  Quarterly composition — per-season industry-aggregated weights for the
//  ETF Holdings page's stacked bar chart.
//
//  For each calendar quarter the LATEST snapshot within that quarter is
//  chosen (DISTINCT ON date_trunc('quarter', snapshot_date)); quarters
//  without a snapshot are simply absent (no carry-forward — bars appear only
//  where real data exists). Weights are aggregated per industry via the same
//  LATERAL sec_classification join as fetchHoldings.
//
//  Fallback chain mirrors getSecComposition: ETF snapshots → tracking index
//  snapshots → direct index snapshots.
// ----------------------------------------------------------------------------
interface DbQuarterlyRow extends QueryResultRow {
  quarter_start: string;
  snapshot_date: string;
  sector_id: string;
  sector_label: string;
  industry: string;
  weight_pct: string | number | null;
  n_holdings: string | number;
}

async function fetchQuarterlyRows(
  code: string,
  sourceType: "etf" | "index",
): Promise<DbQuarterlyRow[]> {
  // The code predicate differs by source_type — static strings, no injection
  // risk (sourceType is a hardcoded literal, not user input).
  const codePredicate =
    sourceType === "etf"
      ? "REGEXP_REPLACE(h.code, '\\.(SZ|SS|SH)$', '') = $1"
      : "h.code = $1";

  const sql = `
    WITH snaps AS (
      SELECT DISTINCT ON (date_trunc('quarter', snapshot_date))
             date_trunc('quarter', snapshot_date) AS quarter_start,
             snapshot_date
        FROM stats.sec_composition
       WHERE source_type = $2
         AND REGEXP_REPLACE(code, '\\.(SZ|SS|SH)$', '') = $1
       ORDER BY date_trunc('quarter', snapshot_date), snapshot_date DESC
    )
    SELECT s.quarter_start::text AS quarter_start,
           s.snapshot_date::text AS snapshot_date,
           COALESCE(sc.sector_id, 'OTHER')   AS sector_id,
           COALESCE(sc.sector_label, '其他') AS sector_label,
           COALESCE(sc.industry_label, '未分类') AS industry,
           SUM(h.weight_pct)                AS weight_pct,
           COUNT(*)                         AS n_holdings
      FROM snaps s
      JOIN stats.sec_composition h
        ON h.source_type = $2
       AND h.snapshot_date = s.snapshot_date
       AND ${codePredicate}
      LEFT JOIN LATERAL (
        SELECT sc.sector_id, sc.industry_id, sc.sector_label, sc.industry_label
          FROM stats.sec_classification sc
         WHERE sc.code = h.stock_code AND sc.type = 'stock'
         ORDER BY sc.parent_index_weight DESC NULLS LAST, sc.parent_index_code
         LIMIT 1
      ) sc ON true
     GROUP BY s.quarter_start, s.snapshot_date,
              COALESCE(sc.sector_id, 'OTHER'),
              COALESCE(sc.sector_label, '其他'),
              COALESCE(sc.industry_label, '未分类')
     ORDER BY s.quarter_start, SUM(h.weight_pct) DESC
  `;
  return queryRows<DbQuarterlyRow>(sql, [code, sourceType]);
}

/** Fold raw grouped rows into the per-quarter response shape. */
function foldQuarterlyRows(rows: DbQuarterlyRow[]): QuarterlyCompositionQuarter[] {
  const byQuarter = new Map<string, QuarterlyCompositionQuarter>();
  for (const r of rows) {
    const label = quarterLabel(r.quarter_start);
    let q = byQuarter.get(label);
    if (!q) {
      q = {
        quarter: label,
        snapshot_date: formatDate(r.snapshot_date),
        n_holdings: 0,
        total_weight_pct: 0,
        industries: [],
      };
      byQuarter.set(label, q);
    }
    q.n_holdings += parseInt(String(r.n_holdings), 10) || 0;
    const w = toNum(r.weight_pct) ?? 0;
    q.total_weight_pct += w;
    q.industries.push({
      industry: r.industry,
      sector_id: r.sector_id,
      sector_label: r.sector_label,
      weight_pct: w,
      n_holdings: parseInt(String(r.n_holdings), 10) || 0,
    });
  }
  const quarters = Array.from(byQuarter.values());
  for (const q of quarters) {
    // Round totals + per-industry weights (SQL SUM of NUMERIC arrives as string).
    q.total_weight_pct = Number(q.total_weight_pct.toFixed(6));
    for (const ind of q.industries) {
      ind.weight_pct = Number(ind.weight_pct.toFixed(6));
    }
  }
  return quarters;
}

/**
 * Per-quarter industry-aggregated composition for the given code.
 *
 * Lookup order (same as getSecComposition):
 *   1. ETF snapshots (source_type='etf') — source "full".
 *   2. Tracking index snapshots — source "index" (+ index_source).
 *   3. Direct index snapshots — source "index".
 *
 * Quarters without any snapshot are absent from `quarters`.
 */
export async function getQuarterlyComposition(
  codeParam: string,
): Promise<QuarterlyCompositionResponse> {
  const stripped = codeParam.replace(SUFFIX_RE, "").trim();
  const empty: QuarterlyCompositionResponse = {
    code: codeParam,
    source: "full",
    quarters: [],
  };
  if (!stripped) return empty;

  // 1. ETF snapshots.
  const etfRows = await fetchQuarterlyRows(stripped, "etf");
  if (etfRows.length > 0) {
    return {
      code: codeParam,
      source: "full",
      quarters: foldQuarterlyRows(etfRows),
    };
  }

  // 2. Tracking index fallback.
  const idx = await fetchTrackingIndex(stripped);
  if (idx) {
    const idxRows = await fetchQuarterlyRows(idx.code, "index");
    if (idxRows.length > 0) {
      return {
        code: codeParam,
        source: "index",
        index_source: { code: idx.code, name: idx.name },
        quarters: foldQuarterlyRows(idxRows),
      };
    }
  }

  // 3. Direct index lookup.
  const directRows = await fetchQuarterlyRows(stripped, "index");
  if (directRows.length > 0) {
    return {
      code: codeParam,
      source: "index",
      quarters: foldQuarterlyRows(directRows),
    };
  }

  return empty;
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

// ----------------------------------------------------------------------------
//  Similar Indices — top-5 similar codes + top-5 similar/dissimilar
//  industry-classified peer codes by MUTUAL shared composition weight for a
//  given subject index. Reads stats.sec_similars (sec_type='index', built by
//  builds.index.exts._sec_similars from stats.sec_composition +
//  stats.sec_classification).
//
//  `date` in sec_similars is the COMPOSITION snapshot_date (quarterly,
//  NOT a trading day). To get the currently-effective similars, we pick the
//  latest row with date <= CURRENT_DATE for the requested code (same
//  carry-forward pattern as index_exts.stock_num).
//
//  All three categories store SEC CODES (index codes), not industry_ids.
//  "industry" means the peer pool is filtered to is_industry_not_strategy=true.
//  Each category is stored as 5 pairs of columns; we fetch the latest row,
//  then run three small unpivot queries (reusing the same SQL template with
//  column-name substitution) to expand each into up to 5 ranked rows. Each
//  row JOINs sec_classification (type='index') to resolve the display name.
// ----------------------------------------------------------------------------
interface DbSimilarCodeRow extends QueryResultRow {
  rank: number;
  code: string;
  name: string | null;
  sharing_weight_pct: number | null;
}
interface DbLatestRow extends QueryResultRow {
  date: string;
}

export async function getSimilarIndices(
  indexCodeParam: string,
): Promise<SimilarIndicesResponse> {
  const indexCode = indexCodeParam.replace(SUFFIX_RE, "").trim();
  const empty: SimilarIndicesResponse = {
    index_code: indexCodeParam,
    snapshot_date: "",
    similars: [],
    similar_industries: [],
    dissimilar_industries: [],
  };
  if (!indexCode) return empty;

  // --- Fetch the latest sec_similars row (date <= today) -------------
  const latestSql = `
    SELECT date::text
      FROM stats.sec_similars
     WHERE code = $1
       AND sec_type = 'index'
       AND date <= CURRENT_DATE
     ORDER BY date DESC
     LIMIT 1
  `;
  const latest = await queryRows<DbLatestRow>(latestSql, [indexCode]);
  if (!latest.length) return empty;
  const snapshotDate = formatDate(latest[0].date);

  // --- Similar codes (unpivot 5 column-pairs into rows) -------------
  const codesSql = `
    SELECT u.rank,
           u.code,
           sc.name,
           u.sharing_weight_pct
      FROM (
        SELECT 1 AS rank,
               similar_1st_code_by_sharing_weights AS code,
               similar_1st_code_sharing_weight_pct AS sharing_weight_pct
          FROM stats.sec_similars
         WHERE code = $1 AND sec_type = 'index' AND date = $2
        UNION ALL
        SELECT 2, similar_2nd_code_by_sharing_weights,
               similar_2nd_code_sharing_weight_pct
          FROM stats.sec_similars
         WHERE code = $1 AND sec_type = 'index' AND date = $2
        UNION ALL
        SELECT 3, similar_3rd_code_by_sharing_weights,
               similar_3rd_code_sharing_weight_pct
          FROM stats.sec_similars
         WHERE code = $1 AND sec_type = 'index' AND date = $2
        UNION ALL
        SELECT 4, similar_4th_code_by_sharing_weights,
               similar_4th_code_sharing_weight_pct
          FROM stats.sec_similars
         WHERE code = $1 AND sec_type = 'index' AND date = $2
        UNION ALL
        SELECT 5, similar_5th_code_by_sharing_weights,
               similar_5th_code_sharing_weight_pct
          FROM stats.sec_similars
         WHERE code = $1 AND sec_type = 'index' AND date = $2
      ) u
      LEFT JOIN LATERAL (
        SELECT name
          FROM stats.sec_classification sc
         WHERE sc.code = u.code AND sc.type = 'index'
         LIMIT 1
      ) sc ON true
     WHERE u.code IS NOT NULL AND u.code <> ''
     ORDER BY u.rank
  `;
  const codeRows = await queryRows<DbSimilarCodeRow>(codesSql, [indexCode, latest[0].date]);
  const similars: SimilarIndexRow[] = codeRows.map((r) => ({
    rank: r.rank as 1 | 2 | 3 | 4 | 5,
    code: r.code,
    name: r.name ?? "",
    sharing_weight_pct: toNum(r.sharing_weight_pct),
  }));

  // --- Similar industry-classified peers (same structure, industry_* cols) ---
  // These columns store SEC CODES (index codes), not industry_ids. The only
  // difference from the codes query is the column name prefix. We reuse the
  // same LATERAL JOIN to resolve the index display name.
  const indSql = codesSql
    .replace(/similar_(\d)(st|nd|rd|th)_code_by_sharing_weights/g,
      "similar_$1$2_industry_code_by_sharing_weights")
    .replace(/similar_(\d)(st|nd|rd|th)_code_sharing_weight_pct/g,
      "similar_$1$2_industry_code_sharing_weight_pct");
  const indRows = await queryRows<DbSimilarCodeRow>(indSql, [indexCode, latest[0].date]);
  const similar_industries: SimilarIndexRow[] = indRows.map((r) => ({
    rank: r.rank as 1 | 2 | 3 | 4 | 5,
    code: r.code,
    name: r.name ?? "",
    sharing_weight_pct: toNum(r.sharing_weight_pct),
  }));

  // --- Dissimilar industry-classified peers (dissimilar_* cols) --------
  const dissSql = codesSql
    .replace(/similar_(\d)(st|nd|rd|th)_code_by_sharing_weights/g,
      "dissimilar_$1$2_industry_code_by_sharing_weights")
    .replace(/similar_(\d)(st|nd|rd|th)_code_sharing_weight_pct/g,
      "dissimilar_$1$2_industry_code_sharing_weight_pct");
  const dissRows = await queryRows<DbSimilarCodeRow>(dissSql, [indexCode, latest[0].date]);
  const dissimilar_industries: SimilarIndexRow[] = dissRows.map((r) => ({
    rank: r.rank as 1 | 2 | 3 | 4 | 5,
    code: r.code,
    name: r.name ?? "",
    sharing_weight_pct: toNum(r.sharing_weight_pct),
  }));

  return {
    index_code: indexCode,
    snapshot_date: snapshotDate,
    similars,
    similar_industries,
    dissimilar_industries,
  };
}
