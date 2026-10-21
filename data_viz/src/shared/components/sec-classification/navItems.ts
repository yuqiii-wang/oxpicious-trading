/**
 * collectActiveItems — the nav's L3 item collection: which security items
 * the active L1/L2 selection shows. Pure function split out of
 * SecClassificationNav.tsx (2026-10-20, >900-line split); the nav memoizes
 * over its inputs.
 */
import type { SectorNode, StrategyNode } from "@shared/types";

export interface ActiveItemsArgs {
  activeColumn: "industry" | "strategy";
  activeSector: SectorNode | null;
  activeStrategy: StrategyNode | null;
  industrySlug: string | null;
  themeSlug: string | null;
  multiSelect: boolean;
  selectedIndustrySlugs: string[];
  mutuallyExclusive: boolean;
}

type NavItem = { code: string; name: string };

export function collectActiveItems(args: ActiveItemsArgs): NavItem[] {
  const {
    activeColumn,
    activeSector,
    activeStrategy,
    industrySlug,
    themeSlug,
    multiSelect,
    selectedIndustrySlugs,
    mutuallyExclusive,
  } = args;

  // Helper: collect strategy-theme items (de-duplicated by code).
  const collectStrategyItems = (): NavItem[] => {
    if (!activeStrategy) return [];
    if (themeSlug) {
      const theme = activeStrategy.industries.find((t) => t.industry_slug === themeSlug);
      return theme ? theme.items : [];
    }
    const seen = new Set<string>();
    const all: NavItem[] = [];
    for (const th of activeStrategy.industries) {
      for (const it of th.items) {
        if (!seen.has(it.code)) { seen.add(it.code); all.push(it); }
      }
    }
    return all;
  };

  // Helper: collect industry items from the active sector (de-duplicated).
  const collectIndustryItems = (): NavItem[] => {
    if (!activeSector) return [];
    if (multiSelect && selectedIndustrySlugs.length > 0) {
      const selectedSlugs = new Set(selectedIndustrySlugs);
      const seen = new Set<string>();
      const all: NavItem[] = [];
      for (const ind of activeSector.industries) {
        if (!selectedSlugs.has(ind.industry_slug)) continue;
        for (const it of ind.items) {
          if (!seen.has(it.code)) { seen.add(it.code); all.push(it); }
        }
      }
      return all;
    }
    if (industrySlug) {
      const ind = activeSector.industries.find((i) => i.industry_slug === industrySlug);
      return ind ? ind.items : [];
    }
    const seen = new Set<string>();
    const all: NavItem[] = [];
    for (const ind of activeSector.industries) {
      for (const it of ind.items) {
        if (!seen.has(it.code)) { seen.add(it.code); all.push(it); }
      }
    }
    return all;
  };

  // Mutually-exclusive mode (default): only the active column's items.
  if (mutuallyExclusive) {
    if (activeColumn === "strategy") return collectStrategyItems();
    return collectIndustryItems();
  }

  // Non-exclusive mode: union of BOTH columns' items (de-duplicated).
  const seen = new Set<string>();
  const all: NavItem[] = [];
  for (const it of collectIndustryItems()) {
    if (!seen.has(it.code)) { seen.add(it.code); all.push(it); }
  }
  for (const it of collectStrategyItems()) {
    if (!seen.has(it.code)) { seen.add(it.code); all.push(it); }
  }
  return all;
}
