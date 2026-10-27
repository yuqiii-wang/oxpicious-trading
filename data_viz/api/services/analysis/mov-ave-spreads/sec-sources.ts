/**
 * MA-Spread per-sec_type source-table config — the sec_type parameter
 * plumbing plus the per-sec_type chart JOIN tables and the prefetched-CTE
 * machinery shared by the chart blocks and the extras groups.
 * Split out of the former monolithic mov-ave-spreads.ts.
 */
import { LABEL_CODE } from "@api/services/label-codes";
import type { MaSpreadSecType } from "@shared/types";

const VALID_SEC_TYPES: ReadonlySet<MaSpreadSecType> = new Set(["etf", "index", "stock", "industry"]);

/** The stored sec_type value of the analysis.mov_ave* tables AND the
 *  stats.market_regime* tables: their static.label_dim SMALLINT code
 *  (the label-code contract). The per-sec_type stats SOURCE tables
 *  ({etf,index,stock,industry}_*) need no binding — each is already
 *  single-sec_type. */
export function secTypeParam(st: MaSpreadSecType): number {
  return LABEL_CODE.sec_type[st];
}

export function normalizeSecType(raw: string | undefined | null): MaSpreadSecType {
  const v = (raw ?? "").trim().toLowerCase();
  if (!VALID_SEC_TYPES.has(v as MaSpreadSecType)) {
    throw new Error(`Invalid sec_type: ${raw!}. Expected 'etf', 'index', 'stock', or 'industry'.`);
  }
  return v as MaSpreadSecType;
}

// ----------------------------------------------------------------------------
//  Per-sec_type source-table config — used to branch the chart JOINs and
//  the name lookup. ETFs use etf_basic_stats + etf_adjustment + etf_tech_stats
//  (price = COALESCE(adj_close, close)); indices use index_basic_stats +
//  index_tech_stats (price = close, no adjustment table).
// ----------------------------------------------------------------------------
export interface SecSource {
  /** Schema-qualified identity table for the asset name lookup. */
  identityTable: string;
  /** Per-code prefetched source tables for the chart query (basic_stats
   *  INNER + adjustment/liquidity_margin/tech_stats LEFT). Each becomes a
   *  MATERIALIZED CTE restricted to the requested codes — the MATERIALIZED
   *  keyword is the optimization fence (the OFFSET 0 anti-flatten marker's
   *  replacement) — then joined on the exact PK columns. Replaces the old
   *  per-row LATERAL probes: those issued 12–14 index descents per history
   *  row (~1600 rows per stock code); the CTE version does ONE index range
   *  scan per table per request and hash-joins the small pre-filtered
   *  sets (55ms → 31ms on stock, and the win grows with history length). */
  chartTables: ChartSourceTable[];
  /** SQL expression for the per-row price column. */
  priceExpr: string;
  /** SQL expression for the open column. */
  openExpr: string;
  /** SQL expression for the high column. */
  highExpr: string;
  /** SQL expression for the low column. */
  lowExpr: string;
  /** SQL expression for the trading_amount column. */
  tradingAmtExpr: string;
}

/** One per-code prefetched chart source table. `withSecType` marks the
 *  analysis.* tables that carry a sec_type column (the stats.* tables are
 *  already per-sec_type). `inner` drops driving rows with no match — used
 *  for the required basic_stats. */
export interface ChartSourceTable {
  alias: string;
  table: string;
  withSecType: boolean;
  inner: boolean;
}

/** analysis.* companions fetched by the DEFAULT chart request (EMA detail
 *  for the EMA pairs' gaps/derivatives/σ + RSI for the shared tooltip
 *  metrics). All carry sec_type. The trading-amount table is extras-only
 *  (TRADING_AMT_TABLE) — the default pair views don't need it. */
export const CHART_ANALYSIS_TABLES: ChartSourceTable[] = [
  { alias: "ema", table: "analysis.mov_ave_spreads_detail_ema", withSecType: true, inner: false },
  { alias: "rsi", table: "analysis.mov_ave_rsi", withSecType: true, inner: false },
];

/** The trading-amount table (amt MA Bollinger σ) — fetched only by the
 *  "amt" extras group. */
export const TRADING_AMT_TABLE: ChartSourceTable = {
  alias: "ta",
  table: "analysis.mov_ave_trading_amt",
  withSecType: true,
  inner: false,
};

export const SEC_SOURCES: Record<MaSpreadSecType, SecSource> = {
  etf: {
    identityTable: "stats.etf_identity",
    chartTables: [
      { alias: "b", table: "stats.etf_basic_stats", withSecType: false, inner: true },
      { alias: "a", table: "stats.etf_adjustment", withSecType: false, inner: false },
      { alias: "lm", table: "stats.etf_liquidity_margin", withSecType: false, inner: false },
      { alias: "t", table: "stats.etf_tech_stats", withSecType: false, inner: false },
    ],
    priceExpr: "COALESCE(a.adj_close, b.close)",
    openExpr: "COALESCE(a.adj_open, b.open)",
    highExpr: "COALESCE(a.adj_high, b.high)",
    lowExpr: "COALESCE(a.adj_low, b.low)",
    tradingAmtExpr: "lm.trading_amount",
  },
  index: {
    identityTable: "stats.index_identity",
    chartTables: [
      { alias: "b", table: "stats.index_basic_stats", withSecType: false, inner: true },
      { alias: "t", table: "stats.index_tech_stats", withSecType: false, inner: false },
    ],
    priceExpr: "b.close",
    openExpr: "b.open",
    highExpr: "b.high",
    lowExpr: "b.low",
    tradingAmtExpr: "b.trading_amount",
  },
  stock: {
    identityTable: "stats.stock_identity",
    chartTables: [
      { alias: "b", table: "stats.stock_basic_stats", withSecType: false, inner: true },
      { alias: "lm", table: "stats.stock_liquidity_margin", withSecType: false, inner: false },
      { alias: "t", table: "stats.stock_tech_stats", withSecType: false, inner: false },
    ],
    priceExpr: "b.close",
    openExpr: "b.open",
    highExpr: "b.high",
    lowExpr: "b.low",
    tradingAmtExpr: "lm.trading_amount",
  },
  // Industry (code = industry_id): the composite pool-'all' slice of
  // stats.industry_basic_stats. NO *_tech_stats table exists, so the
  // MA columns are window aggregates over the composite close (partial
  // windows — the builds' min_periods=1 convention) inside a rename
  // subquery (industry_id -> code, mean_* -> close/open/high/low). The
  // EMA columns are NULL here — getMovAveSpreadChartEma recomputes them
  // API-side from the returned price series (SQL has no EMA primitive).
  // The window-function subquery is a partition barrier, so the per-code
  // WHERE outside it cannot push down — the ~173K-row aggregate runs
  // once per request (~50ms).
  industry: {
    identityTable: "stats.industry_identity",
    chartTables: [
      {
        alias: "t",
        table: `(
          SELECT
            industry_id AS code, date,
            mean_close AS close, mean_open AS open,
            mean_high AS high, mean_low AS low,
            total_trading_amount AS trading_amount,
            AVG(mean_close) OVER (PARTITION BY industry_id ORDER BY date
              ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS ma5,
            AVG(mean_close) OVER (PARTITION BY industry_id ORDER BY date
              ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
            AVG(mean_close) OVER (PARTITION BY industry_id ORDER BY date
              ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS ma60,
            AVG(mean_close) OVER (PARTITION BY industry_id ORDER BY date
              ROWS BETWEEN 119 PRECEDING AND CURRENT ROW) AS ma120,
            AVG(mean_close) OVER (PARTITION BY industry_id ORDER BY date
              ROWS BETWEEN 254 PRECEDING AND CURRENT ROW) AS ma255,
            NULL::double precision AS ema6,
            NULL::double precision AS ema12,
            NULL::double precision AS ema26
          FROM stats.industry_basic_stats
          WHERE pool_size = 3 AND mean_close IS NOT NULL
        )`,
        withSecType: false,
        inner: true,
      },
    ],
    priceExpr: "t.close",
    openExpr: "t.open",
    highExpr: "t.high",
    lowExpr: "t.low",
    tradingAmtExpr: "t.trading_amount",
  },
};

export function buildNameSql(secType: MaSpreadSecType): string {
  const src = SEC_SOURCES[secType];
  // DISTINCT ON (code) + ORDER BY code, date DESC walks the (code, date) PK
  // backwards PER CODE — one index probe per variant. The naive
  // `ORDER BY date DESC LIMIT 1` form drove a date-index backward scan that
  // filtered EVERY row of every touched partition before finding a match
  // (~470k rows/partition, 4.4s measured 2026-09-29); the subselect wrapper
  // then picks the overall-latest variant's row.
  return `
    SELECT name
    FROM (
      SELECT DISTINCT ON (code) code, name, date
      FROM ${src.identityTable}
      WHERE code = ANY($1::text[])
      ORDER BY code, date DESC
    ) latest
    ORDER BY date DESC
    LIMIT 1
  `;
}

/** WITH-CTE list prefetching every source table for the requested codes —
 *  the MATERIALIZED keyword fences the planner (the OFFSET 0 anti-flatten
 *  marker's replacement) so each table is read as ONE index range scan and
 *  hash-joined, instead of per-row index probes. */
export function chartCtesSql(tables: ChartSourceTable[]): string {
  return tables
    .map((t) => {
      const where = t.withSecType
        ? "sec_type = $1 AND code = ANY($2::text[])"
        : "code = ANY($2::text[])";
      return (
        `${t.alias} AS MATERIALIZED (\n` +
        `      SELECT * FROM ${t.table}\n` +
        `      WHERE ${where}\n` +
        `    )`
      );
    })
    .join(",\n    ");
}

/** FROM-clause join list for the prefetched CTEs, on the exact PK equality
 *  (basic_stats INNER — drops driving rows with no price row). */
export function chartJoinSql(tables: ChartSourceTable[]): string {
  return tables
    .map((t) => {
      const kw = t.inner ? "JOIN" : "LEFT JOIN";
      const secCond = t.withSecType ? `${t.alias}.sec_type = d.sec_type AND ` : "";
      return `${kw} ${t.alias} ON ${secCond}${t.alias}.code = d.code AND ${t.alias}.date = d.date`;
    })
    .join("\n    ");
}
