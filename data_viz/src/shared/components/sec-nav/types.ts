/**
 * Shared types for the analysis classification-nav kit (SecNavShell +
 * useSecNav + SecTypeToggle).
 */
import type { SectorNode, StrategyNode } from "@shared/types";

/** Security type across all analysis pages — the sec_type toggle value. */
export type SecNavSecType = "etf" | "index" | "stock";

/** Themes endpoints for ONE sec_type — a page plugs in its own backend routes. */
export interface SecNavThemesSource {
  /** LEFT column — L1 sector → L2 industry tree. */
  themes: (exchange: string | null) => Promise<SectorNode[]>;
  /** RIGHT column — parallel L1 strategy → L2 theme tree. */
  strategyThemes: (exchange: string | null) => Promise<StrategyNode[]>;
}

export interface UseSecNavOptions {
  /** Themes endpoints per sec_type; keys without an entry can't be loaded. */
  themesSources: Partial<Record<SecNavSecType, SecNavThemesSource>>;
  /** Initial sec_type (and the only one for single-sec-type pages). Default "index". */
  defaultSecType?: SecNavSecType;
  /** Label in the not-found message: `Code not found in ${SEC} ${dataLabel} data`. Default "classification". */
  dataLabel?: string;
  /** When FALSE (default TRUE), the LEFT (sector/industry) and RIGHT
   *  (strategy/theme) columns can be selected simultaneously — the handlers
   *  skip the cross-column clears. Used by multi-select merge pages. */
  mutuallyExclusive?: boolean;
  /** Called from refresh() before bumping refreshKey — pages invalidate their API cache here. */
  onInvalidateCache?: () => void;
}

/** Everything SecNavShell needs to render the nav, plus what pages filter on. */
export interface SecNavState {
  // Classification data
  sectors: SectorNode[];
  strategies: StrategyNode[];
  loading: boolean;
  error: string | null;

  // Selection state
  secType: SecNavSecType;
  sectorId: string | null;
  industrySlug: string | null;
  exchange: string | null;
  strategyId: string | null;
  themeSlug: string | null;
  searchCode: string | null;

  setSecType: (t: SecNavSecType) => void;

  // Code search (resolves against BOTH trees)
  handleSearch: (code: string) => void;
  handleClearSearch: () => void;

  // Nav handlers (clear searchCode on change; LEFT/RIGHT columns are
  // mutually exclusive)
  handleSectorChange: (id: string | null) => void;
  handleIndustryChange: (slug: string | null) => void;
  handleStrategyChange: (id: string | null) => void;
  handleThemeChange: (slug: string | null) => void;
  handleExchangeChange: (ex: string | null) => void;

  // Item selection from L3 chips
  onItemSelected: (code: string) => void;
  onClearItemSelection: () => void;

  // Manual refresh: onInvalidateCache() + bump refreshKey (reloads the trees).
  // refreshKey is also exposed so pages can re-fetch their own per-selection
  // data (codes lists etc.) on the same cycle.
  refresh: () => void;
  refreshKey: number;

  /** "Sector / Industry" label of the active selection (or strategy/theme). */
  headerLabel: string;

  /** Display name for a code, looked up across both trees (suffix-normalized). */
  findItemName: (code: string) => string | undefined;
}
