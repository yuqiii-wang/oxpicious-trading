/**
 * Performance Attribution themes trees — listPerfAttrThemes (L1 sector → L2
 * industry → items) and listPerfAttrStrategyThemes (L1 strategy → L2 theme
 * → items) for the Perf Attribution page's ThemeSelector.
 * Split out of the former monolithic perf-attribution.ts.
 */
import { stripExchangeSuffix } from "@api/lib/classify-etf";
import { buildStrategyThemesFromRows } from "@api/services/_shared";
import type {
  PerfAttrSecType,
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "@shared/types";
import {
  PERF_ATTR_META_TABLE,
  PERF_ATTR_META_TYPE,
  resolvePerfAttrCodeSource,
  fetchPerfAttrMetaRows,
} from "./_shared.js";

// ----------------------------------------------------------------------------
//  listPerfAttrThemes — two-level L1 sector → L2 industry → items tree for the
//  Perf Attribution page's ThemeSelector. Mirrors listThemes() in
//  etf-margin.service.ts and listIndexThemes() in index.service.ts,
//  but only includes codes that have rows in stats.cross_stats
//  (sec_type='index') for the requested sec_type.
// ----------------------------------------------------------------------------
export async function listPerfAttrThemes(
  secType: PerfAttrSecType,
): Promise<SectorNode[]> {
  const metaTable = PERF_ATTR_META_TABLE[secType] ?? PERF_ATTR_META_TABLE.etf;
  const metaType = PERF_ATTR_META_TYPE[secType] ?? PERF_ATTR_META_TYPE.etf;
  const codeSource = await resolvePerfAttrCodeSource(secType);
  if (codeSource === "empty") return [];
  const rows = await fetchPerfAttrMetaRows(metaTable, codeSource, secType, metaType);

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    // LEFT column: only industry-primary securities. Strategy-primary rows
    // (is_industry_not_strategy=FALSE) carry strategy/theme in
    // sector_id/industry_id and belong in the RIGHT column only.
    if (!r.is_industry_not_strategy) continue;
    // Strip exchange suffix so the items[] codes match the codes returned by
    // listPerfAttrCodes (which also strips the suffix).
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
//  listPerfAttrStrategyThemes — parallel L1 strategy → L2 theme → items tree
//  built from the same meta SQL but using the strategy-primary rows
//  (is_industry_not_strategy=FALSE). sector_id/industry_id on those rows
//  carry the strategy/theme classification. Tree-building is delegated to the
//  shared buildStrategyThemesFromRows helper to avoid duplicating the
//  grouping/sorting logic.
// ----------------------------------------------------------------------------
export async function listPerfAttrStrategyThemes(
  secType: PerfAttrSecType,
): Promise<StrategyNode[]> {
  const metaTable = PERF_ATTR_META_TABLE[secType] ?? PERF_ATTR_META_TABLE.etf;
  const metaType = PERF_ATTR_META_TYPE[secType] ?? PERF_ATTR_META_TYPE.etf;
  const codeSource = await resolvePerfAttrCodeSource(secType);
  if (codeSource === "empty") return [];
  const rows = await fetchPerfAttrMetaRows(metaTable, codeSource, secType, metaType);

  const mappedRows = rows.map((r) => ({
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
