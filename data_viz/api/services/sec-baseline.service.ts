/**
 * Security baseline bundle service — ONE shared endpoint for the shared
 * baseline-plot component.
 *
 * The baseline trend panel (IndexPanel / EtfMarginPanel / StockPanel via
 * useSecBaselineBundle) is shared across pages (PE, Dividends, …), so its
 * data path is shared too (2026-09-29): instead of every consumer
 * branching over the three PAGE-scoped combined browse endpoints
 * (/api/index/combined, /api/etf-margin/combined, /api/stock/combined —
 * each returning pagination metadata the panel never reads), this service
 * resolves (sec_type, code) → exactly one bundle through the same
 * per-sec_type services, behind one URL: GET /api/sec-baseline.
 *
 * Feeder funds (ETF联接基金, bare OTC codes like 012761) need no separate
 * sec_type: builds.etf.feeder_nav loads their NAV series into
 * stats.etf_identity, so they flow through the plain "etf" path below.
 */
import type { QueryResultRow } from "pg";
import { querySqlView } from "../lib/sql-views.js";
import { getIndicesCombined } from "./index.service.js";
import { getEtfMarginCombined } from "./etf-margin.service.js";
import { getStocksCombined } from "./stock.service.js";
import type {
  IndexBundle,
  EtfBundle,
  StockBundle,
} from "../../shared/types.js";

export type SecBaselineBundle = IndexBundle | EtfBundle | StockBundle;

export type SecBaselineSecType = "index" | "etf" | "stock" | "industry";

/**
 * Fetch ONE security's baseline bundle (rows + classification meta).
 * Returns null when the code has no rows in its sec_type's universe.
 *
 * Delegates to the per-sec_type combined services with the exact-code path
 * (code filter + page_size 1) — that path already carries the per-table
 * code-predicate optimizations and per-code meta lookups; nothing is
 * reimplemented here.
 */
export async function getSecBaselineBundle(
  secType: SecBaselineSecType,
  code: string,
): Promise<SecBaselineBundle | null> {
  const target = code.trim();
  if (!target) return null;
  if (secType === "index") {
    const r = await getIndicesCombined({ code: target, page: 1, page_size: 1 });
    return r.indices[0] ?? null;
  }
  if (secType === "etf") {
    const r = await getEtfMarginCombined({ code: target, page: 1, page_size: 1 });
    return r.etfs[0] ?? null;
  }
  if (secType === "stock") {
    const r = await getStocksCombined({ code: target, page: 1, page_size: 1 });
    return r.stocks[0] ?? null;
  }
  // Industry (code = industry_id): assembled DIRECTLY as an IndexBundle —
  // the composite pool-'all' slice IS an index-shaped entity (rebased-to-100
  // composite OHLC), so the shared IndexPanel renders it unchanged. MAs are
  // window aggregates over the composite close (partial windows — the
  // builds' min_periods=1 convention); pe = stats.industry_basic_stats.mean_pe
  // (the build's cross-sectional member mean — the same value analysis.pe
  // carries for sec_type=3).
  if (secType === "industry") {
    // The SELECT lives in the sql view
    // data_viz/api/services/sec-baseline/industry_bundle.
    const rows = await querySqlView<DbIndustryBaselineRow>(
      "data_viz/api/services/sec-baseline/industry_bundle",
      { industry_code: target },
    );
    if (rows.length === 0) return null;
    return {
      code: target,
      name: rows[0].name ?? "",
      sector_id: "INDUSTRY",
      sector_label: "行业",
      industry_id: target,
      industry_label: rows[0].name ?? "",
      is_industry_not_strategy: true,
      rows: rows.map((r) => ({
        date: r.date,
        open: r.open,
        high: r.high,
        low: r.low,
        close: r.close,
        trading_shares: null,
        trading_amount: r.trading_amount,
        change_pct: null,
        pe: r.pe,
        cons_number: null,
        ma5: r.ma5,
        ma20: r.ma20,
        ma60: r.ma60,
        ma120: r.ma120,
        ma255: r.ma255,
        has_intraday_5mins: false,
      })),
    };
  }
  throw new Error(`Invalid sec_type: ${secType}. Expected 'index', 'etf', 'stock', or 'industry'.`);
}

interface DbIndustryBaselineRow extends QueryResultRow {
  name: string | null;
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  trading_amount: number | null;
  pe: number | null;
  ma5: number | null;
  ma20: number | null;
  ma60: number | null;
  ma120: number | null;
  ma255: number | null;
}

// Composite-industry baseline rows + label (pool-'all' slice; window MAs) —
// the SELECT lives in the sql view
// data_viz/api/services/sec-baseline/industry_bundle.
