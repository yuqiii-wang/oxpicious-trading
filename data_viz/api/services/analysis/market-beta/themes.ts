/**
 * Market Beta themes trees — listMarketBetaThemes (L1
 * sector → L2 industry → items) and listMarketBetaStrategyThemes
 * (L1 strategy → L2 theme → items) over the compositioned index universe.
 * Split out of the former monolithic industry-sentiments.ts.
 */
import { matchesExchange } from "@api/lib/classify-etf";
import { buildStrategyThemesFromRows } from "@api/services/_shared";
import type {
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "@shared/types";
import { listMarketBetaMetaRows } from "./meta.js";

export async function listMarketBetaThemes(
  exchange?: string | null,
): Promise<SectorNode[]> {
  // Build the L1 sector → L2 industry → items tree from per-code meta rows.
  // Uses MARKET_BETA_META_SQL (which already applies the composition-
  // only filter) and groups by sector/industry, pushing each index code into
  // its industry's items[] array. This populates the L3 security-level chips
  // in the SecClassificationNav so the user can pick an individual index.
  // The exchange filter is applied in TS (via matchesExchange) so the nav
  // tree respects the selected exchange — e.g. HK indices are excluded when
  // "All (primary)" is selected, mirroring the index-baseline themes endpoint.
  const exFilter = (exchange ?? "").trim() || null;
  const rows = await listMarketBetaMetaRows();

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
//  listMarketBetaStrategyThemes — parallel L1 strategy → L2 theme →
//  items tree built from MARKET_BETA_META_SQL but using the
//  strategy-primary rows (is_industry_not_strategy=FALSE). sector_id/
//  industry_id on those rows carry the strategy/theme classification.
//  Tree-building is delegated to the shared buildStrategyThemesFromRows helper
//  to avoid duplicating the grouping/sorting logic. Index codes are already
//  bare (e.g. "000300"), so no exchange-suffix stripping is needed.
// ----------------------------------------------------------------------------
export async function listMarketBetaStrategyThemes(
  exchange?: string | null,
): Promise<StrategyNode[]> {
  const exFilter = (exchange ?? "").trim() || null;
  const rows = await listMarketBetaMetaRows();

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
