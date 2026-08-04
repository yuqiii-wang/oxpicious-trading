/**
 * Industry Sentiments - member index values, rebased to 100 client-side.
 * Extracted from the former analysis.service.ts.
 */
import { queryRows, formatDate, toNum } from "../../lib/db.js";
import type { QueryResultRow } from "pg";
import type {
  SectorNode,
  IndustryNode,
  IndustrySentimentsIndexRow,
  IndustrySentimentsIndex,
  IndustrySentimentsAggRow,
  IndustrySentimentsChartResponse,
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
interface DbIndustrySentimentsThemeRow extends QueryResultRow {
  industry_id: string;
  sector_id: string;
  sector_label: string;
  industry_label: string;
  industry_slug: string;
  /** Count of member indices (stats.sec_classification type='index') in this
   *  industry. Drives the per-industry chip count on the ThemeSelector. */
  member_count: number;
}

export async function listIndustrySentimentsThemes(): Promise<SectorNode[]> {
  // Build the L1 sector → L2 industry tree directly from sec_classification
  // (type='index'). No analysis-table intermediary — every classified index
  // contributes its (sector_id, industry_id) tag. Per-industry count = number
  // of member indices.
  //
  // COMPOSITION-ONLY FILTER: only indices with at least one sec_composition
  // snapshot (source_type='index') are counted. Industries whose indices ALL
  // lack composition data do not appear in the tree at all.
  const sql = `
    SELECT
      industry_id,
      COALESCE(NULLIF(sector_id, ''),      'OTHER')  AS sector_id,
      COALESCE(NULLIF(sector_label, ''),   '其他')   AS sector_label,
      COALESCE(NULLIF(industry_label, ''), industry_id) AS industry_label,
      COALESCE(NULLIF(industry_slug, ''),  LOWER(industry_id)) AS industry_slug,
      COUNT(*) AS member_count
    FROM stats.sec_classification sc
    WHERE type = 'index'
      AND industry_id IS NOT NULL
      AND industry_id <> ''
      AND COALESCE(sector_id, '') <> 'DEBT'
      AND EXISTS (
          SELECT 1 FROM stats.sec_composition sc2
          WHERE sc2.code = sc.code AND sc2.source_type = 'index'
      )
    GROUP BY industry_id, sector_id, sector_label, industry_label, industry_slug
  `;
  const rows = await queryRows<DbIndustrySentimentsThemeRow>(sql, []);

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode & { member_count: number }>;
  }>();

  for (const r of rows) {
    if (!sectorMap.has(r.sector_id)) {
      sectorMap.set(r.sector_id, { sector_label: r.sector_label, industries: new Map() });
    }
    const sector = sectorMap.get(r.sector_id)!;
    if (!sector.industries.has(r.industry_id)) {
      sector.industries.set(r.industry_id, {
        industry_id: r.industry_id,
        industry_label: r.industry_label,
        industry_slug: r.industry_slug,
        count: Number(r.member_count) || 0,
        items: [],
        member_count: Number(r.member_count) || 0,
      });
    }
  }

  const sectors: SectorNode[] = [];
  for (const [sector_id, sector] of sectorMap) {
    const industries = Array.from(sector.industries.values()).sort((a, b) => {
      if (a.industry_id === "OTHER") return 1;
      if (b.industry_id === "OTHER") return -1;
      return b.member_count - a.member_count;
    });
    sectors.push({
      sector_id,
      sector_label: sector.sector_label,
      count: industries.reduce((sum, i) => sum + i.member_count, 0),
      industries: industries.map(({ ...rest }) => rest),
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
): Promise<IndustrySentimentsChartResponse> {
  const industryId = (rawIndustryId ?? "").trim();
  if (!industryId) throw new Error("Missing 'industry_id' parameter");

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

  return {
    industry_id: industryId,
    industry_label: labelRows[0]?.industry_label ?? "",
    indices: Array.from(byCode.values()),
    aggregation,
    benchmarks,
  };
}
