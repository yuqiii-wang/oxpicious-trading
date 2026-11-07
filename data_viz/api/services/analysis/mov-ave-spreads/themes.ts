/**
 * MA-Spread themes trees — listMovAveSpreadThemes (L1 sector → L2 industry
 * → items) and listMovAveSpreadStrategyThemes (L1 strategy → L2 theme →
 * items) for the MA-Spread page's ThemeSelector.
 * Split out of the former monolithic mov-ave-spreads.ts.
 */
import { querySqlView } from "@api/lib/sql-views";
import { stripExchangeSuffix, matchesExchange } from "@api/lib/classify-etf";
import {
  buildIndustryClassifiedThemes,
  buildIndustryStrategyThemes,
  buildStrategyThemesFromRows,
} from "@api/services/_shared";
import { listIndustryEntityClassRows } from "@api/services/classification-cache.js";
import type {
  SectorNode,
  IndustryNode,
  StrategyNode,
} from "@shared/types";
import { normalizeSecType, secTypeParam } from "./sec-sources.js";
import type { DbCodeRow } from "./codes.js";
import { MA_SPREAD_META_TYPE, type DbMaSpreadMetaRow } from "./meta.js";

const CODES_LIST_VIEW = "data_viz/api/services/analysis/mov-ave-spreads/codes/codes_list";
const META_CLASSIFIED_ROWS_VIEW =
  "data_viz/api/services/analysis/mov-ave-spreads/meta/classified_rows";

// ----------------------------------------------------------------------------
//  listMovAveSpreadThemes — two-level L1 sector → L2 industry → items tree for
//  the MA-Spread page's ThemeSelector. Mirrors listPerfAttrThemes() in
//  perf-attribution but draws codes from analysis.mov_ave_spreads_detail
//  (filtered by sec_type) and supports etf / index / stock / industry —
//  industry renders the industry ENTITIES (code = industry_id) under their
//  stats.sec_classification classification (grouped by industry_id).
//
//  Labels come precomputed from stats.sec_classification (denormalized onto
//  the table by build_classification.py — no catalog JOIN needed). Codes that
//  don't have a sec_classification row are bucketed under sector 'OTHER' /
//  industry '未分类' so the user still sees them in the selector.
// ----------------------------------------------------------------------------
export async function listMovAveSpreadThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<SectorNode[]> {
  const secType = normalizeSecType(rawSecType);
  // Industry: the entities under their EXISTING classification — L1 sector
  // → L2 industry tree derived from stats.sec_classification grouped by
  // industry_id (one entity item per node), restricted to the per-code
  // summary universe. Exchange is not applicable to an industry entity and
  // is ignored (as before).
  if (secType === "industry") {
    const rows = await querySqlView<DbCodeRow>(CODES_LIST_VIEW,
      { sec_type: secTypeParam(secType) });
    const classRows = await listIndustryEntityClassRows(rows.map((r) => r.code));
    return buildIndustryClassifiedThemes(rows, classRows);
  }
  const exFilter = (rawExchange ?? "").trim() || null;
  const metaType = MA_SPREAD_META_TYPE[secType];
  const rows = await querySqlView<DbMaSpreadMetaRow>(META_CLASSIFIED_ROWS_VIEW,
    { sec_type: secTypeParam(secType), m_type: metaType });

  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, IndustryNode>;
  }>();

  for (const r of rows) {
    // LEFT column: only industry-primary securities. Strategy-primary rows
    // (is_industry_not_strategy=FALSE) carry strategy/theme in
    // sector_id/industry_id and belong in the RIGHT column only.
    if (!r.is_industry_not_strategy) continue;
    // Apply exchange filter so the nav tree respects the selected exchange
    // (e.g. HK indices are excluded when "All (primary)" is selected).
    if (exFilter && !matchesExchange(r.exchange, exFilter)) continue;
    // Strip exchange suffix so item codes match the codes returned by
    // listMovAveSpreadCodes (which also strips the suffix).
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
//  listMovAveSpreadStrategyThemes — parallel L1 strategy → L2 theme → items
//  tree built from the same meta view (mov-ave-spreads/meta/classified_rows)
//  but using the strategy-primary
//  rows (is_industry_not_strategy=FALSE). sector_id/industry_id on those rows
//  carry the strategy/theme classification. Tree-building is delegated to the
//  shared buildStrategyThemesFromRows helper to avoid duplicating the
//  grouping/sorting logic.
// ----------------------------------------------------------------------------
export async function listMovAveSpreadStrategyThemes(
  rawSecType: string | undefined | null,
  rawExchange?: string | null,
): Promise<StrategyNode[]> {
  const secType = normalizeSecType(rawSecType);
  // Industry: the strategy-primary entity ids (BROAD_*, DIV_*, …) under
  // their strategy → theme classification — the same grouped-by-industry_id
  // read as the LEFT column, just the other is_industry_not_strategy side.
  if (secType === "industry") {
    const rows = await querySqlView<DbCodeRow>(CODES_LIST_VIEW,
      { sec_type: secTypeParam(secType) });
    const classRows = await listIndustryEntityClassRows(rows.map((r) => r.code));
    return buildIndustryStrategyThemes(rows, classRows);
  }
  const exFilter = (rawExchange ?? "").trim() || null;
  const metaType = MA_SPREAD_META_TYPE[secType];
  const rows = await querySqlView<DbMaSpreadMetaRow>(META_CLASSIFIED_ROWS_VIEW,
    { sec_type: secTypeParam(secType), m_type: metaType });

  // Filter by exchange BEFORE building the strategy tree so cross-border
  // securities are excluded when "All (primary)" is selected (same behavior
  // as listMovAveSpreadThemes and listIndexThemes).
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
