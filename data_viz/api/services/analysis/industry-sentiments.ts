/**
 * Industry Sentiments - member index values, rebased to 100 client-side.
 * Extracted from the former analysis.service.ts.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import { buildStrategyThemesFromRows, matchesClassification } from "../_shared.js";
import { stripExchangeSuffix, matchesExchange } from "../../lib/classify-etf.js";
import type {
  SectorNode,
  IndustryNode,
  IndustrySentimentsIndexRow,
  IndustrySentimentsIndex,
  IndustrySentimentsAggRow,
  IndustrySentimentsChartResponse,
  StrategyNode,
} from "../../../shared/types.js";

// ============================================================================
//  Industry Sentiments — member index values, rebased to 100 client-side
//
//  NO analysis.industry_sentiments table — the former cross-sectional
//  aggregation (max / min / mean / median / var of subject_return /
//  benchmark_return / active_return percentages) has been DROPPED. Mixing
//  scales across indices with different price levels was misleading, and
//  the broad-market-removal step was opaque.
//
//  NEW APPROACH: each industry's plot shows its member INDEX VALUES directly,
//  rebased to 100 at the start of the displayed (zoom) window. Rebased-to-100
//  makes member indices comparable regardless of absolute price level (e.g.
//  CSI 500 ~5500pts and SSE 50 ~2600pts plot on a common scale, so a +10%
//  move on either looks equally large). The rebasing is computed in the
//  BROWSER (IndustrySentimentsPage.tsx) from raw daily closes returned here.
//
//  Data source (queried directly — no aggregation table intermediary):
//    stats.index_basic_stats.close   (raw daily index closes)
//    JOIN stats.sec_classification    (type='index') for industry membership
//    stats.sec_composition            (stock_num → pool_size classification)
//    analysis.industry_sentiments     (precomputed mean/var per pool_size slice)
//
//  COMPOSITION-ONLY FILTER
//    Both endpoints restrict to indices that have at least one
//    stats.sec_composition snapshot (source_type='index'). Indices WITHOUT
//    any composition data are dropped entirely — they are never returned to
//    the frontend, and industries whose indices all lack composition do not
//    appear in the themes tree at all.
//
//  Two endpoints:
//    listIndustrySentimentsThemes()  — L1 sector → L2 industry tree from
//       stats.sec_classification (type='index') directly. industries.count =
//       number of member indices in the industry (informative "how many
//       indices contribute" chip). items[] left empty (the page plots the
//       industry as a whole, not per-code children).
//    getIndustrySentimentsChart(id)  — per-index close time series for ONE
//       industry + precomputed mean/var aggregation rows per pool_size slice.
//       Returns one entry per member index in the industry, each with its raw
//       daily close series AND stock_num (for pool_size classification +
//       tooltip display). Indices with no index_basic_stats rows are omitted.
// ============================================================================
export async function listIndustrySentimentsThemes(
  exchange?: string | null,
): Promise<SectorNode[]> {
  // Build the L1 sector → L2 industry → items tree from per-code meta rows.
  // Uses INDUSTRY_SENTIMENTS_META_SQL (which already applies the composition-
  // only filter) and groups by sector/industry, pushing each index code into
  // its industry's items[] array. This populates the L3 security-level chips
  // in the SecClassificationNav so the user can pick an individual index.
  // The exchange filter is applied in TS (via matchesExchange) so the nav
  // tree respects the selected exchange — e.g. HK indices are excluded when
  // "All (primary)" is selected, mirroring the index-baseline themes endpoint.
  const exFilter = (exchange ?? "").trim() || null;
  const rows = await queryRows<DbIndustrySentimentsMetaRow>(
    INDUSTRY_SENTIMENTS_META_SQL,
    [],
  );

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    // LEFT column: only industry-primary securities.
    if (!r.is_industry_not_strategy) continue;
    if (exFilter && !matchesExchange(r.exchange, exFilter)) continue;
    if (!sectorMap.has(r.sector_id)) {
      sectorMap.set(r.sector_id, { sector_label: r.sector_label, industries: new Map() });
    }
    const sector = sectorMap.get(r.sector_id)!;
    if (!sector.industries.has(r.industry_id)) {
      sector.industries.set(r.industry_id, {
        industry_id: r.industry_id,
        industry_label: r.industry_label,
        industry_slug: r.industry_slug,
        count: 0,
        items: [],
      });
    }
    const ind = sector.industries.get(r.industry_id)!;
    ind.items.push({ code: r.code, name: r.name ?? "" });
    ind.count++;
  }

  const sectors: SectorNode[] = [];
  for (const [sector_id, sector] of sectorMap) {
    const industries = Array.from(sector.industries.values()).sort((a, b) => {
      if (a.industry_id === "OTHER") return 1;
      if (b.industry_id === "OTHER") return -1;
      return b.count - a.count;
    });
    sectors.push({
      sector_id,
      sector_label: sector.sector_label,
      count: industries.reduce((sum, i) => sum + i.count, 0),
      industries,
    });
  }
  sectors.sort((a, b) => {
    if (a.sector_id === "OTHER") return 1;
    if (b.sector_id === "OTHER") return -1;
    return b.count - a.count;
  });
  return sectors;
}

// ----------------------------------------------------------------------------
//  Per-code meta query — one row per classified index (type='index') that has
//  at least one stats.sec_composition snapshot. Used by
//  listIndustrySentimentsStrategyThemes() (RIGHT column strategy/theme tree)
//  and by getIndustrySentimentsChart() for classification filtering.
//  is_industry_not_strategy distinguishes industry-primary (TRUE) from
//  strategy-primary (FALSE) rows; for strategy-primary rows sector_id/
//  industry_id carry the STRATEGY/theme classification.
// ----------------------------------------------------------------------------
interface DbIndustrySentimentsMetaRow extends QueryResultRow {
  code: string;
  name: string;
  exchange: string | null;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  is_industry_not_strategy: boolean;
}

const INDUSTRY_SENTIMENTS_META_SQL = `
  SELECT sc.code,
         COALESCE(sc.name, '')             AS name,
         sc.exchange                       AS exchange,
         COALESCE(sc.sector_id,       'OTHER')  AS sector_id,
         COALESCE(sc.sector_label,    '其他')   AS sector_label,
         COALESCE(sc.industry_id,     'OTHER')  AS industry_id,
         COALESCE(sc.industry_label,  '未分类')  AS industry_label,
         COALESCE(sc.industry_slug,   'other')  AS industry_slug,
         COALESCE(sc.is_industry_not_strategy, TRUE) AS is_industry_not_strategy
    FROM stats.sec_classification sc
   WHERE sc.type = 'index'
     AND sc.is_active = TRUE
     AND sc.industry_id IS NOT NULL
     AND sc.industry_id <> ''
     AND COALESCE(sc.sector_id, '') <> 'DEBT'
     AND EXISTS (
         SELECT 1 FROM stats.sec_composition sc2
         WHERE sc2.code = sc.code AND sc2.source_type = 'index'
     )
`;

// ----------------------------------------------------------------------------
//  listIndustrySentimentsStrategyThemes — parallel L1 strategy → L2 theme →
//  items tree built from INDUSTRY_SENTIMENTS_META_SQL but using the
//  strategy-primary rows (is_industry_not_strategy=FALSE). sector_id/
//  industry_id on those rows carry the strategy/theme classification.
//  Tree-building is delegated to the shared buildStrategyThemesFromRows helper
//  to avoid duplicating the grouping/sorting logic. Index codes are already
//  bare (e.g. "000300"), so no exchange-suffix stripping is needed.
// ----------------------------------------------------------------------------
export async function listIndustrySentimentsStrategyThemes(
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const exFilter = (exchange ?? "").trim() || null;
  const rows = await queryRows<DbIndustrySentimentsMetaRow>(INDUSTRY_SENTIMENTS_META_SQL, []);

  const mappedRows = rows
    .filter((r) => {
      // RIGHT column: only strategy-primary securities, then apply exchange.
      if (r.is_industry_not_strategy) return false;
      if (exFilter && !matchesExchange(r.exchange, exFilter)) return false;
      return true;
    })
    .map((r) => ({
      code: r.code,
      name: r.name,
      sector_id: r.sector_id,
      sector_label: r.sector_label,
      industry_id: r.industry_id,
      industry_label: r.industry_label,
      industry_slug: r.industry_slug,
      is_industry_not_strategy: r.is_industry_not_strategy,
    }));

  return buildStrategyThemesFromRows(mappedRows);
}

// ----------------------------------------------------------------------------

interface DbIndustrySentimentsChartRow extends QueryResultRow {
  code: string;
  name: string;
  exchange: string | null;
  date: Date | string;
  close: number | null;
  stock_num: number | null;
}

interface DbIndustrySentimentsAggRow extends QueryResultRow {
  date: Date | string;
  pool_size: "small" | "mid" | "large" | "all";
  index_count: number | null;
  mean_price: number | null;
  var_price: number | null;
  mean_pe: number | null;
  total_trading_amount: number | null;
}

/** Broad-market benchmark indices offered in the UI benchmark dropdown.
 *  Each is fetched as a close series and rebased to 100 client-side (same as
 *  member indices). The frontend renders a multi-select dropdown so the user
 *  can tick any subset to overlay on the chart. */
const INDUSTRY_SENTIMENTS_BENCHMARKS = [
  { code: "000300", name: "沪深300", stockNum: 300 },
  { code: "000016", name: "上证50", stockNum: 50 },
  { code: "000852", name: "中证1000", stockNum: 1000 },
  { code: "932000", name: "中证2000", stockNum: 2000 },
  { code: "000688", name: "科创50", stockNum: 50 },
];

/**
 * Fetch per-index close time series for ONE industry + precomputed mean/var
 * aggregation rows per pool_size slice. Returns one entry per member index
 * in the industry (stats.sec_classification type='index' AND industry_id =
 * $1 AND index has composition data in stats.sec_composition), each with
 * its raw daily close series AND stock_num (looked up from the latest
 * sec_composition snapshot). The frontend rebases each index to 100 at the
 * start of the visible (zoom) window and overlays the precomputed mean/var
 * for the user-selected pool_size slice.
 *
 * COMPOSITION-ONLY FILTER: indices WITHOUT any sec_composition snapshot are
 * excluded entirely — they are never returned to the frontend.
 *
 * ALSO returns the close series for each broad-market benchmark
 * (INDUSTRY_SENTIMENTS_BENCHMARKS) so the frontend can overlay any subset
 * via the benchmark dropdown.
 *
 * Indices classified into the industry but lacking any index_basic_stats
 * rows are omitted (nothing to plot). The shared date axis is the union of
 * all member indices' trading days — handled client-side by the chart.
 */
export async function getIndustrySentimentsChart(
  rawIndustryId: string,
  sector?: string | null,
  industry?: string | null,
  strategy?: string | null,
  theme?: string | null,
): Promise<IndustrySentimentsChartResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) throw new Error("Missing 'industry_id' parameter");
  const sectorFilter = (sector ?? "").trim();
  const industryFilter = (industry ?? "").trim();
  const strategyFilter = (strategy ?? "").trim();
  const themeFilter = (theme ?? "").trim();
  const hasClassFilter = !!(sectorFilter || industryFilter || strategyFilter || themeFilter);

  // Single query: member indices' classification JOINed with their daily
  // closes AND a LATERAL lookup for stock_num from the latest sec_composition
  // snapshot (covers ALL compositioned indices, not just ETF-tracked ones —
  // index_exts only covers 147 of 222 compositioned indices).
  // sec_classification.name carries the index display name, so no separate
  // identity JOIN is needed.
  //
  // COMPOSITION-ONLY FILTER: the EXISTS subquery restricts to indices that
  // have at least one sec_composition snapshot. Indices without ANY
  // composition data are dropped entirely.
  const chartSql = `
    WITH stock_counts AS (
        SELECT code, snapshot_date,
               COUNT(DISTINCT stock_code) AS stock_num
        FROM stats.sec_composition
        WHERE source_type = 'index'
        GROUP BY code, snapshot_date
    )
    SELECT
      sc.code,
      COALESCE(sc.name, '') AS name,
      sc.exchange,
      ib.date,
      ib.close,
      latest.stock_num
    FROM stats.sec_classification sc
    JOIN stats.index_basic_stats ib
      ON ib.code = sc.code
    LEFT JOIN LATERAL (
        SELECT stock_num
        FROM stock_counts sc2
        WHERE sc2.code = ib.code
        ORDER BY snapshot_date DESC
        LIMIT 1
    ) latest ON true
    WHERE sc.type = 'index'
      AND sc.is_active = TRUE
      AND sc.industry_id = $1::text
      AND EXISTS (
          SELECT 1 FROM stats.sec_composition sc3
          WHERE sc3.code = sc.code AND sc3.source_type = 'index'
      )
    ORDER BY sc.code, ib.date ASC
  `;
  // Precomputed mean/var aggregation rows from analysis.industry_sentiments.
  // Returns rows for ALL 4 pool_size slices — the frontend filters to the
  // user-selected slice for the overlay.
  const aggSql = `
    SELECT date, pool_size, index_count, mean_price, var_price, mean_pe, total_trading_amount
    FROM analysis.industry_sentiments
    WHERE industry_id = $1::text
    ORDER BY pool_size, date ASC
  `;
  const benchmarkCodes = INDUSTRY_SENTIMENTS_BENCHMARKS.map((b) => b.code);
  const [chartRows, aggRows, labelRows, benchmarkRows] = await Promise.all([
    queryRows<DbIndustrySentimentsChartRow>(chartSql, [industryId]),
    queryRows<DbIndustrySentimentsAggRow>(aggSql, [industryId]),
    queryRows<{ industry_label: string | null }>(
      `SELECT industry_label FROM stats.sec_classification
       WHERE industry_id = $1::text AND type = 'index' LIMIT 1`,
      [industryId],
    ),
    // Benchmarks: close series for each broad-market benchmark in
    // INDUSTRY_SENTIMENTS_BENCHMARKS. Rebased to 100 client-side same as
    // member indices. The frontend renders a multi-select dropdown so the
    // user can tick any subset to overlay.
    queryRows<{ code: string; date: Date | string; close: number | null }>(
      `SELECT code, date, close FROM stats.index_basic_stats
       WHERE code = ANY($1::text[]) ORDER BY code, date ASC`,
      [benchmarkCodes],
    ),
  ]);

  // Group flat chart rows by index code, preserving first-seen order.
  const byCode = new Map<string, IndustrySentimentsIndex>();
  for (const r of chartRows) {
    let idx = byCode.get(r.code);
    if (!idx) {
      idx = { code: r.code, name: r.name ?? "", exchange: r.exchange ?? null, rows: [] };
      byCode.set(r.code, idx);
    }
    const row: IndustrySentimentsIndexRow = {
      date: formatDate(r.date),
      close: toNum(r.close),
      stock_num: r.stock_num == null ? null : Number(r.stock_num),
    };
    idx.rows.push(row);
  }

  const aggregation: IndustrySentimentsAggRow[] = aggRows.map((r) => ({
    date: formatDate(r.date),
    pool_size: r.pool_size,
    index_count: r.index_count == null ? null : Number(r.index_count),
    mean_price: toNum(r.mean_price),
    var_price: toNum(r.var_price),
    mean_pe: toNum(r.mean_pe),
    total_trading_amount: toNum(r.total_trading_amount),
  }));

  // Build benchmarks array — one entry per benchmark with close rows, in the
  // canonical INDUSTRY_SENTIMENTS_BENCHMARKS order. Benchmarks with no close
  // data are omitted.
  const benchRowsByCode = new Map<string, { date: Date | string; close: number | null }[]>();
  for (const r of benchmarkRows) {
    let arr = benchRowsByCode.get(r.code);
    if (!arr) {
      arr = [];
      benchRowsByCode.set(r.code, arr);
    }
    arr.push({ date: r.date, close: r.close });
  }
  const benchmarks: IndustrySentimentsIndex[] = [];
  for (const b of INDUSTRY_SENTIMENTS_BENCHMARKS) {
    const rows = benchRowsByCode.get(b.code);
    if (!rows || rows.length === 0) continue;
    benchmarks.push({
      code: b.code,
      name: `${b.name} (benchmark)`,
      exchange: null,
      rows: rows.map((r) => ({
        date: formatDate(r.date),
        close: toNum(r.close),
        stock_num: b.stockNum,
      })),
    });
  }

  // When a classification filter is active, fetch the per-code meta rows and
  // filter member indices via matchesClassification(). Industry and strategy
  // filters are mutually exclusive (handled by matchesClassification). Index
  // codes are already bare, so they map directly to the meta rows.
  let indices = Array.from(byCode.values());
  if (hasClassFilter) {
    const metaRows = await queryRows<DbIndustrySentimentsMetaRow>(INDUSTRY_SENTIMENTS_META_SQL, []);
    const classMap = new Map<string, DbIndustrySentimentsMetaRow>();
    for (const m of metaRows) {
      classMap.set(m.code, m);
    }
    indices = indices.filter((idx) => {
      const meta = classMap.get(idx.code);
      if (!meta) return false;
      return matchesClassification(meta, sectorFilter, industryFilter, strategyFilter, themeFilter);
    });
  }

  return {
    industry_id: industryId,
    industry_label: labelRows[0]?.industry_label ?? "",
    indices,
    aggregation,
    benchmarks,
  };
}

/**
 * Fetch chart data (close series + stock_num) for a SINGLE index code.
 * Used when the user clicks an L3 index chip under a strategy/theme —
 * strategy-primary indices may not have an industry_id, so the standard
 * industry-based chart endpoint can't find them.
 *
 * Returns an IndustrySentimentsChartResponse with at most one index in
 * `indices` (empty if the code has no index_basic_stats rows). Aggregation
 * is always empty (no precomputed mean/var for a single index). Benchmarks
 * are included so the dropdown still works.
 */
export async function getIndustrySentimentsChartByCode(
  rawCode: string,
): Promise<IndustrySentimentsChartResponse> {
  const code = stripExchangeSuffix((rawCode ?? "").trim());
  if (!code) throw new Error("Missing 'code' parameter");

  const chartSql = `
    WITH stock_counts AS (
        SELECT code, snapshot_date,
               COUNT(DISTINCT stock_code) AS stock_num
        FROM stats.sec_composition
        WHERE source_type = 'index'
        GROUP BY code, snapshot_date
    )
    SELECT
      sc.code,
      COALESCE(sc.name, '') AS name,
      sc.exchange,
      ib.date,
      ib.close,
      latest.stock_num
    FROM stats.sec_classification sc
    JOIN stats.index_basic_stats ib
      ON ib.code = sc.code
    LEFT JOIN LATERAL (
        SELECT stock_num
        FROM stock_counts sc2
        WHERE sc2.code = ib.code
        ORDER BY snapshot_date DESC
        LIMIT 1
    ) latest ON true
    WHERE sc.type = 'index'
      AND sc.code = $1::text
    ORDER BY ib.date ASC
  `;
  const benchmarkCodes = INDUSTRY_SENTIMENTS_BENCHMARKS.map((b) => b.code);
  const [chartRows, benchmarkRows] = await Promise.all([
    queryRows<DbIndustrySentimentsChartRow>(chartSql, [code]),
    queryRows<{ code: string; date: Date | string; close: number | null }>(
      `SELECT code, date, close FROM stats.index_basic_stats
       WHERE code = ANY($1::text[]) ORDER BY code, date ASC`,
      [benchmarkCodes],
    ),
  ]);

  // Build the single index entry (if any rows found).
  const indices: IndustrySentimentsIndex[] = [];
  if (chartRows.length > 0) {
    const r0 = chartRows[0];
    const idx: IndustrySentimentsIndex = {
      code: r0.code,
      name: r0.name ?? "",
      exchange: r0.exchange ?? null,
      rows: chartRows.map((r) => ({
        date: formatDate(r.date),
        close: toNum(r.close),
        stock_num: r.stock_num == null ? null : Number(r.stock_num),
      })),
    };
    indices.push(idx);
  }

  // Build benchmarks (same logic as getIndustrySentimentsChart).
  const benchmarks: IndustrySentimentsIndex[] = [];
  const benchRowsByCode = new Map<string, { date: Date | string; close: number | null }[]>();
  for (const r of benchmarkRows) {
    let arr = benchRowsByCode.get(r.code);
    if (!arr) {
      arr = [];
      benchRowsByCode.set(r.code, arr);
    }
    arr.push({ date: r.date, close: r.close });
  }
  for (const b of INDUSTRY_SENTIMENTS_BENCHMARKS) {
    const rows = benchRowsByCode.get(b.code);
    if (!rows || rows.length === 0) continue;
    benchmarks.push({
      code: b.code,
      name: `${b.name} (benchmark)`,
      exchange: null,
      rows: rows.map((r) => ({
        date: formatDate(r.date),
        close: toNum(r.close),
        stock_num: b.stockNum,
      })),
    });
  }

  return {
    industry_id: code,
    industry_label: indices[0]?.name ?? code,
    indices,
    aggregation: [],
    benchmarks,
  };
}
