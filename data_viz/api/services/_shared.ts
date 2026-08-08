import type { StrategyNode, ThemeNode } from "../../shared/types";

/** Row shape required by buildStrategyThemesFromRows.
 *  Each service maps its own Db*MetaRow to this shape before calling the
 *  helper (stripping exchange suffixes, etc.). */
export interface StrategyThemeInputRow {
  code: string;
  name: string;
  sector_id: string;
  sector_label: string;
  industry_id: string;
  industry_label: string;
  industry_slug: string;
  is_industry_not_strategy: boolean;
}

/** Build the L1 sector → L2 industry → items tree from a flat list of meta
 *  rows, restricted to strategy-PRIMARY rows (is_industry_not_strategy=FALSE).
 *  For these rows sector_id/industry_id carry the STRATEGY classification
 *  (BROAD/BROAD_CSI, DIV/DIV_SOE, …) — there is no separate strategy_id/
 *  theme_id column, so the tree uses the SAME field names as the industry
 *  tree (sector_id/industry_id).
 *
 *  Used by every service's listStrategyThemes() to avoid duplicating the
 *  tree-building logic. */
export function buildStrategyThemesFromRows(
  rows: StrategyThemeInputRow[],
): StrategyNode[] {
  const sectorMap = new Map<string, {
    sector_label: string;
    industries: Map<string, ThemeNode>;
  }>();

  for (const r of rows) {
    if (r.is_industry_not_strategy) continue;
    const item = { code: r.code, name: r.name ?? "" };
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

  const strategies: StrategyNode[] = [];
  for (const [sector_id, sector] of sectorMap) {
    const industries = Array.from(sector.industries.values()).sort((a, b) => {
      if (a.industry_id === "OTHER") return 1;
      if (b.industry_id === "OTHER") return -1;
      return b.count - a.count;
    });
    strategies.push({
      sector_id,
      sector_label: sector.sector_label,
      count: industries.reduce((sum, t) => sum + t.count, 0),
      industries,
    });
  }
  strategies.sort((a, b) => {
    if (a.sector_id === "OTHER") return 1;
    if (b.sector_id === "OTHER") return -1;
    return b.count - a.count;
  });
  return strategies;
}

/** Shared filter logic for data endpoints that support the two-column
 *  classification (industry LEFT, strategy RIGHT).
 *
 *  Returns TRUE if the row should be INCLUDED in the result set.
 *
 *  Mutual exclusivity: when strategyFilter is set, only strategy-primary
 *  rows (is_ind=FALSE) are considered; when sectorFilter is set, only
 *  industry-primary rows (is_ind=TRUE) are considered.  This prevents
 *  strategy-primary indices from leaking into the LEFT column filter and
 *  vice versa.
 *
 *  When neither sectorFilter nor strategyFilter is set, all rows pass
 *  (no classification filter applied). */
export function matchesClassification(
  row: {
    sector_id: string;
    industry_id: string;
    industry_slug: string;
    is_industry_not_strategy: boolean;
  },
  sectorFilter: string,
  industryFilter: string,
  strategyFilter: string,
  themeFilter: string,
): boolean {
  const useStrategyFilter = !sectorFilter && !!strategyFilter;
  if (useStrategyFilter) {
    if (row.is_industry_not_strategy) return false;
    const stratOk = row.sector_id === strategyFilter;
    const themeOk = !themeFilter || row.industry_slug === themeFilter || row.industry_id === themeFilter;
    return stratOk && themeOk;
  }
  if (sectorFilter) {
    if (!row.is_industry_not_strategy) return false;
    const sectorOk = row.sector_id === sectorFilter;
    const industryOk = !industryFilter || row.industry_slug === industryFilter || row.industry_id === industryFilter;
    return sectorOk && industryOk;
  }
  return true;
}
