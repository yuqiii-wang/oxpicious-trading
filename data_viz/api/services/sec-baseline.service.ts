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
import { getIndicesCombined } from "./index.service.js";
import { getEtfMarginCombined } from "./etf-margin.service.js";
import { getStocksCombined } from "./stock.service.js";
import type {
  IndexBundle,
  EtfBundle,
  StockBundle,
} from "../../shared/types.js";

export type SecBaselineBundle = IndexBundle | EtfBundle | StockBundle;

export type SecBaselineSecType = "index" | "etf" | "stock";

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
  throw new Error(`Invalid sec_type: ${secType}. Expected 'index', 'etf', or 'stock'.`);
}
