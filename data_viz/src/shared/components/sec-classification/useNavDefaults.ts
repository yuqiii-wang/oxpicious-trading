/**
 * useNavDefaults — the nav's ONCE-per-itemKind default selection: on
 * initial mount, when the classification tree has loaded, auto-select the
 * default (sector/strategy + industry/theme + code) for the current
 * itemKind (see DEFAULTS_BY_KIND); when the canonical code has drifted out
 * of the industry, the industry's FIRST item is picked instead so every
 * mode still seeds a default. Applied once via a ref guard so
 * subsequent tree reloads (e.g. exchange filter changes) do not override
 * the user's selection; the guard resets when itemKind changes so the
 * default re-applies on every sec_type switch. Skipped in multi-select
 * mode. Split out of SecClassificationNav.tsx (2026-10-20, >900-line
 * split).
 */
import { useEffect, useRef } from "react";
import type { SectorNode, StrategyNode } from "@shared/types";
import { DEFAULTS_BY_KIND } from "./constants";

export interface NavDefaultsArgs {
  itemKind?: "Index" | "ETF" | "Stock" | "Industry";
  multiSelect: boolean;
  multiSelectItems: boolean;
  sectors: SectorNode[];
  strategies: StrategyNode[] | undefined;
  sectorId: string | null;
  strategyId: string | null;
  themeSlug: string | null;
  industrySlug: string | null;
  selectedItemCode: string | null;
  onSectorChange: (id: string | null) => void;
  onIndustryChange: (slug: string | null) => void;
  onStrategyChange?: (id: string | null) => void;
  onThemeChange?: (slug: string | null) => void;
  onItemSelected?: (code: string) => void;
}

export function useNavDefaults(args: NavDefaultsArgs): void {
  const {
    itemKind,
    multiSelect,
    multiSelectItems,
    sectors,
    strategies,
    sectorId,
    strategyId,
    themeSlug,
    industrySlug,
    selectedItemCode,
    onSectorChange,
    onIndustryChange,
    onStrategyChange,
    onThemeChange,
    onItemSelected,
  } = args;

  // Handles two scenarios:
  //  1. Clean slate — no sector/strategy selected (page has no default logic):
  //     set the full default (sector/strategy + industry/theme + code).
  //  2. Page already set the sector/strategy but didn't drill down: complete
  //     the selection with the industry/theme + code.
  //
  // Default lives in the strategy (RIGHT) column for Index and ETF
  // (is_industry_not_strategy=FALSE), and in the industry (LEFT) column for
  // Stock (is_industry_not_strategy=TRUE). Falls back to LEFT column when
  // the strategy column is absent on the page.
  //
  // Reset the "default applied" guard whenever itemKind changes, so the
  // default re-applies on every sec_type switch (Index → Stock → ETF). Without
  // this, the guard is set to TRUE on the first tree load and never fires
  // again, so switching sec_type would leave the nav without a default.
  const defaultAppliedRef = useRef(false);
  const lastItemKindRef = useRef<typeof itemKind>(undefined);
  useEffect(() => {
    if (lastItemKindRef.current !== itemKind) {
      lastItemKindRef.current = itemKind;
      defaultAppliedRef.current = false;
    }
  }, [itemKind]);
  useEffect(() => {
    if (defaultAppliedRef.current) return;
    if (!itemKind) return;
    if (multiSelect || multiSelectItems) return;

    const def = DEFAULTS_BY_KIND[itemKind];
    if (!def) return;

    const hasTree = sectors.length > 0 || (!!strategies && strategies.length > 0);
    if (!hasTree) return;

    // The default's column is chosen by `isStrategy`, but it must fall back
    // to the other column when that column is absent on the page. E.g. for
    // Index/ETF the default lives in the strategy column, but pages without a
    // strategy column should still drill down via sector/industry.
    const asStrategy = def.isStrategy
      ? (strategies?.find((s) => s.sector_id === def.sectorId) ?? null)
      : null;
    const asSector = sectors.find((s) => s.sector_id === def.sectorId) ?? null;
    const node = asStrategy ?? asSector;
    if (!node) return;

    const industry = node.industries.find((i) => i.industry_slug === def.industrySlug);
    if (!industry) return;
    // Canonical code first; when classification drift moved it elsewhere
    // (e.g. Stock 000001.SZ → OTHER), fall back to the industry's first
    // item so EVERY mode still seeds a default selection.
    const item = industry.items.find((it) => it.code === def.itemCode) ?? industry.items[0];
    if (!item) return;

    // All validation passed — mark as applied NOW (not earlier) so that a
    // stale tree from a different sec_type (e.g. old Index tree while
    // switching to ETF) doesn't prematurely set the guard and block the
    // default from firing when the correct tree arrives.
    defaultAppliedRef.current = true;

    const isStrategy = !!asStrategy;

    if (isStrategy) {
      // Default lives in the RIGHT column (strategy → theme)
      if (!sectorId && !strategyId) {
        onStrategyChange?.(def.sectorId);
        onThemeChange?.(def.industrySlug);
        onItemSelected?.(item.code);
      } else if (strategyId === def.sectorId && !themeSlug && !selectedItemCode) {
        onThemeChange?.(def.industrySlug);
        onItemSelected?.(item.code);
      }
    } else {
      // Default lives in the LEFT column (sector → industry)
      if (!sectorId && !strategyId) {
        onSectorChange(def.sectorId);
        onIndustryChange(def.industrySlug);
        onItemSelected?.(item.code);
      } else if (sectorId === def.sectorId && !industrySlug && !selectedItemCode) {
        onIndustryChange(def.industrySlug);
        onItemSelected?.(item.code);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemKind, sectors, strategies, sectorId, strategyId, themeSlug, industrySlug, selectedItemCode]);
}
