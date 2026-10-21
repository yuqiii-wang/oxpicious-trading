/**
 * Valuation themes trees — listValuationThemes (L1 sector → L2 industry →
 * items) and listValuationStrategyThemes (L1 strategy → L2 theme → items)
 * over the analysis.pe ∪ analysis.dividends universe. Serves the SHARED
 * /api/analysis/valuation/themes routes used by BOTH the PE and Dividends
 * pages.
 * Split out of the former monolithic pe.ts.
 */
import { queryRows } from "@api/lib/db";
import { stripExchangeSuffix, matchesExchange } from "@api/lib/classify-etf";
import { buildIndustryFlatThemes, buildStrategyThemesFromRows } from "@api/services/_shared";
import { LABEL_CODE } from "@api/services/label-codes";
import type {
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "@shared/types";
import { normalizeSecType, listPeMetaRows } from "./_shared.js";
import { buildCodesSql, type DbCodeRow } from "./codes.js";

// ----------------------------------------------------------------------------
//  listValuationThemes — L1 sector → L2 industry → items tree, restricted
//  to codes that have rows in analysis.pe / analysis.dividends for the requested
//  sec_type. Mirrors listMovAveSpreadThemes().
// ----------------------------------------------------------------------------
export async function listValuationThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<SectorNode[]> {
  const secType = normalizeSecType(rawSecType);
  // Industry: one flat 'INDUSTRY' -> '全部行业' group from the per-code
  // summary (no sec_classification above the industry entity).
  if (secType === "industry") {
    const rows = await queryRows<DbCodeRow>(buildCodesSql(),
      [LABEL_CODE.sec_type[secType]]);
    return buildIndustryFlatThemes(rows);
  }
  const exFilter = (rawExchange ?? "").trim() || null;
  const rows = await listPeMetaRows(secType);

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    if (!r.is_industry_not_strategy) continue;
    if (exFilter && !matchesExchange(r.exchange, exFilter)) continue;
    const code = stripExchangeSuffix(r.code);
    if (!code) continue;
    const item = { code, name: r.name ?? "" };

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
    ind.items.push(item);
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
//  listValuationStrategyThemes — parallel L1 strategy → L2 theme → items
//  tree from the strategy-primary rows (is_industry_not_strategy=FALSE).
// ----------------------------------------------------------------------------
export async function listValuationStrategyThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<StrategyNode[]> {
  const secType = normalizeSecType(rawSecType);
  // Industry has no strategy/theme classification — an empty RIGHT column.
  if (secType === "industry") return [];
  const exFilter = (rawExchange ?? "").trim() || null;
  const rows = await listPeMetaRows(secType);

  const filteredRows = exFilter
    ? rows.filter((r) => matchesExchange(r.exchange, exFilter))
    : rows;

  const mappedRows = filteredRows.map((r) => ({
    code: stripExchangeSuffix(r.code),
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
