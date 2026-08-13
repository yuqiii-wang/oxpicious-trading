/**
 * SecClassificationNav — shared two-column security classification navigator.
 *
 * LEFT column:  L1 sector  → L2 industry  (is_industry_not_strategy=TRUE)
 * RIGHT column: L1 strategy → L2 theme    (is_industry_not_strategy=FALSE)
 *
 * The two columns are PARALLEL and MUTUALLY EXCLUSIVE: selecting a chip in
 * one column clears the selection in the other.
 *
 * When `strategies` is omitted/empty, the RIGHT column is hidden and the
 * component renders as a single-column (sector/industry only) navigator.
 *
 * Style customization:
 *   - `sx`        — MUI sx prop for the outer Stack container
 *   - `chipSize`  — "small" | "medium" (default "small")
 *   - `density`   — "compact" | "comfortable" (controls row spacing)
 *   - `showExchange` — show/hide the exchange filter row (default true)
 *
 * Optional L3 security-level row: when `itemKind` is set, renders one chip
 * per individual Index/ETF/Stock under the active industry/theme, paginated.
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Box, Chip, CircularProgress, Pagination, Stack, Typography, type SxProps } from "@mui/material";
import type { SectorNode, StrategyNode } from "../../../../shared/types";
import { PRIMARY_EXCHANGE_OPTIONS, SECONDARY_EXCHANGE_OPTIONS } from "../../utils/classify";
import SimilarIndicesList from "./SimilarIndicesList";

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------
interface Props {
  sectors: SectorNode[];
  sectorId: string | null;
  industrySlug: string | null;
  onSectorChange: (id: string | null) => void;
  onIndustryChange: (slug: string | null) => void;
  onExchangeChange: (ex: string | null) => void;

  /** Parallel strategy tree (RIGHT column). When omitted/empty, only the
   *  LEFT column (sector/industry) is rendered. */
  strategies?: StrategyNode[];
  strategyId?: string | null;
  themeSlug?: string | null;
  onStrategyChange?: (id: string | null) => void;
  onThemeChange?: (slug: string | null) => void;

  exchange: string | null;

  /** Multi-select mode (optional). See ThemeSelector docs for details. */
  multiSelect?: boolean;
  selectedIndustrySlugs?: string[];
  onMultiIndustryChange?: (slugs: string[]) => void;

  /** Optional L3 security-level row. */
  itemKind?: "Index" | "ETF" | "Stock";
  selectedItemCode?: string | null;
  onItemSelected?: (code: string) => void;
  onClearItemSelection?: () => void;

  /** Optional L3 multi-select mode (parallel to L2 multiSelect). When true,
   *  L3 chips toggle membership in `selectedItemCodes` instead of replacing
   *  a single `selectedItemCode`. "All" clears the selection (no filter =
   *  show all items). */
  multiSelectItems?: boolean;
  selectedItemCodes?: string[];
  onMultiItemSelected?: (codes: string[]) => void;

  /** When TRUE (requires multiSelect + multiSelectItems), renders an
   *  "All <industry>" chip per selected industry (in the active sector) at
   *  the start of the L3 row. Each chip mirrors the L2 industry selection —
   *  it is always filled (selected) because the industry is active. Clicking
   *  it REMOVES the industry from the L2 multi-select (unselects the
   *  industry). Used by Industry Sentiments so users can drop a whole
   *  industry directly from the L3 row without scrolling back to L2. */
  showAllIndustryChips?: boolean;

  // ---- Style customization ----
  /** MUI sx prop applied to the outer Stack container. */
  sx?: SxProps;
  /** Chip size for all classification chips. */
  chipSize?: "small" | "medium";
  /** Row spacing density. "compact" = 0.75, "comfortable" = 1.5. */
  density?: "compact" | "comfortable";
  /** Show/hide the exchange filter row (default true). */
  showExchange?: boolean;

  /** When TRUE, shows a small inline spinner at the right end of the Exchange
   *  row to indicate the classification tree (sectors/strategies) is being
   *  (re)fetched — e.g. on initial mount or when the exchange filter changes.
   *  Consumers should drive this from their themes-fetch loading state. */
  loading?: boolean;

  /** When TRUE (default), the LEFT (industry) and RIGHT (strategy) columns
   *  are mutually exclusive — selecting in one clears the other. When FALSE,
   *  both columns can be selected simultaneously; L3 items show the union of
   *  both columns' selections. Used by pages that merge industry + strategy
   *  selections into one view (e.g. Industry Sentiments). */
  mutuallyExclusive?: boolean;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------
const ITEMS_PAGE_SIZE = 16;

/** Default selection for Index sec_type: 宽基(BROAD) → 上证(broad_sse) → 上证指数(000001).
 *  Applied ONCE on initial mount when the classification tree has loaded and
 *  no prior user selection exists. Centralised here so every page using the
 *  nav gets the same default without per-page boilerplate. */
const DEFAULT_INDEX_SECTOR_ID = "BROAD";
const DEFAULT_INDEX_INDUSTRY_SLUG = "broad_sse";
const DEFAULT_INDEX_ITEM_CODE = "000001";

// ---------------------------------------------------------------------------
// Sub-component: a labeled row of chips
// ---------------------------------------------------------------------------
interface ChipRowProps {
  label: string;
  chipSize: "small" | "medium";
  children: ReactNode;
  /** Optional element rendered immediately after the label (e.g. expand/collapse triangle). */
  labelAdornment?: ReactNode;
}

function ChipRow({ label, chipSize, children, labelAdornment }: ChipRowProps) {
  return (
    <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", alignItems: "center" }}>
      <Typography
        variant="subtitle2"
        sx={{ fontWeight: 600, minWidth: 56, fontSize: chipSize === "small" ? "0.75rem" : "0.85rem" }}
      >
        {label}
      </Typography>
      {labelAdornment}
      {children}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------
export default function SecClassificationNav({
  sectors,
  sectorId,
  industrySlug,
  onSectorChange,
  onIndustryChange,
  onExchangeChange,
  strategies,
  strategyId = null,
  themeSlug = null,
  onStrategyChange,
  onThemeChange,
  exchange,
  multiSelect = false,
  selectedIndustrySlugs = [],
  onMultiIndustryChange,
  itemKind,
  selectedItemCode = null,
  onItemSelected,
  onClearItemSelection,
  multiSelectItems = false,
  selectedItemCodes = [],
  onMultiItemSelected,
  showAllIndustryChips = false,
  sx,
  chipSize = "small",
  density = "compact",
  showExchange = true,
  mutuallyExclusive = true,
  loading = false,
}: Props) {
  const hasStrategyColumn = !!strategies && strategies.length > 0;
  const activeSector = sectors.find((s) => s.sector_id === sectorId) ?? null;
  const activeStrategy = hasStrategyColumn
    ? (strategies!.find((s) => s.sector_id === strategyId) ?? null)
    : null;

  const activeColumn = sectorId ? "industry" : (strategyId ? "strategy" : "industry");
  const rowSpacing = density === "compact" ? 0.75 : 1.5;
  const chipFontSize = chipSize === "small" ? "0.7rem" : "0.8rem";

  // L3 items pagination
  const [itemPage, setItemPage] = useState(1);
  useEffect(() => {
    setItemPage(1);
  }, [sectorId, industrySlug, strategyId, themeSlug]);

  // --- Default selection for Index sec_type ---
  // On initial mount, when the classification tree has loaded, auto-select
  // 宽基(BROAD) → 上证(broad_sse) → 上证指数(000001). Applied ONCE via a ref
  // guard so subsequent tree reloads (e.g. exchange filter changes) do not
  // override the user's selection. Skipped in multi-select mode.
  //
  // Handles two scenarios:
  //  1. Clean slate — no sector/strategy selected (page has no default logic):
  //     set the full default (sector/strategy + industry/theme + code).
  //  2. Page already set BROAD as the sector/strategy but didn't drill down:
  //     complete the selection with the industry/theme + code.
  const defaultAppliedRef = useRef(false);
  useEffect(() => {
    if (defaultAppliedRef.current) return;
    if (itemKind !== "Index") return;
    if (multiSelect || multiSelectItems) return;

    const hasTree = sectors.length > 0 || (!!strategies && strategies.length > 0);
    if (!hasTree) return;

    // Mark as applied on the first tree load — the default only runs once.
    defaultAppliedRef.current = true;

    // BROAD is a strategy (is_industry_not_strategy=FALSE) for index 000001,
    // so it normally lives in the RIGHT column. Fall back to LEFT column.
    const broadStrategy = strategies?.find((s) => s.sector_id === DEFAULT_INDEX_SECTOR_ID);
    const broadSector = sectors.find((s) => s.sector_id === DEFAULT_INDEX_SECTOR_ID);
    const broadNode = broadStrategy ?? broadSector;
    if (!broadNode) return;

    const sseIndustry = broadNode.industries.find(
      (i) => i.industry_slug === DEFAULT_INDEX_INDUSTRY_SLUG,
    );
    if (!sseIndustry) return;
    if (!sseIndustry.items.some((it) => it.code === DEFAULT_INDEX_ITEM_CODE)) return;

    const isStrategy = !!broadStrategy;

    if (isStrategy) {
      // BROAD is in the RIGHT column (strategy → theme)
      if (!sectorId && !strategyId) {
        onStrategyChange?.(DEFAULT_INDEX_SECTOR_ID);
        onThemeChange?.(DEFAULT_INDEX_INDUSTRY_SLUG);
        onItemSelected?.(DEFAULT_INDEX_ITEM_CODE);
      } else if (strategyId === DEFAULT_INDEX_SECTOR_ID && !themeSlug && !selectedItemCode) {
        onThemeChange?.(DEFAULT_INDEX_INDUSTRY_SLUG);
        onItemSelected?.(DEFAULT_INDEX_ITEM_CODE);
      }
    } else {
      // BROAD is in the LEFT column (sector → industry)
      if (!sectorId && !strategyId) {
        onSectorChange(DEFAULT_INDEX_SECTOR_ID);
        onIndustryChange(DEFAULT_INDEX_INDUSTRY_SLUG);
        onItemSelected?.(DEFAULT_INDEX_ITEM_CODE);
      } else if (sectorId === DEFAULT_INDEX_SECTOR_ID && !industrySlug && !selectedItemCode) {
        onIndustryChange(DEFAULT_INDEX_INDUSTRY_SLUG);
        onItemSelected?.(DEFAULT_INDEX_ITEM_CODE);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemKind, sectors, strategies, sectorId, strategyId, themeSlug, industrySlug, selectedItemCode]);

  // Cross-Border exchange row (HK / Overseas) — hidden by default. The user
  // expands it via the ▼ triangle next to the "Exchange" label. Cross-border
  // securities are excluded from the default "All (primary)" filter, so this
  // row is opt-in.
  const [showCrossBorder, setShowCrossBorder] = useState(false);

  const activeItems = useMemo(() => {
    // Helper: collect strategy-theme items (de-duplicated by code).
    const collectStrategyItems = (): Array<{ code: string; name: string }> => {
      if (!activeStrategy) return [];
      if (themeSlug) {
        const theme = activeStrategy.industries.find((t) => t.industry_slug === themeSlug);
        return theme ? theme.items : [];
      }
      const seen = new Set<string>();
      const all: Array<{ code: string; name: string }> = [];
      for (const th of activeStrategy.industries) {
        for (const it of th.items) {
          if (!seen.has(it.code)) { seen.add(it.code); all.push(it); }
        }
      }
      return all;
    };

    // Helper: collect industry items from the active sector (de-duplicated).
    const collectIndustryItems = (): Array<{ code: string; name: string }> => {
      if (!activeSector) return [];
      if (multiSelect && selectedIndustrySlugs.length > 0) {
        const selectedSlugs = new Set(selectedIndustrySlugs);
        const seen = new Set<string>();
        const all: Array<{ code: string; name: string }> = [];
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
      const all: Array<{ code: string; name: string }> = [];
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
    const all: Array<{ code: string; name: string }> = [];
    for (const it of collectIndustryItems()) {
      if (!seen.has(it.code)) { seen.add(it.code); all.push(it); }
    }
    for (const it of collectStrategyItems()) {
      if (!seen.has(it.code)) { seen.add(it.code); all.push(it); }
    }
    return all;
  }, [activeColumn, activeSector, activeStrategy, industrySlug, themeSlug, multiSelect, selectedIndustrySlugs, mutuallyExclusive]);

  const totalItemPages = Math.max(1, Math.ceil(activeItems.length / ITEMS_PAGE_SIZE));
  const itemPageClamped = Math.min(itemPage, totalItemPages);
  const pagedItems = activeItems.slice(
    (itemPageClamped - 1) * ITEMS_PAGE_SIZE,
    itemPageClamped * ITEMS_PAGE_SIZE,
  );

  // --- Industry helpers ---
  const isIndustrySelected = (slug: string): boolean =>
    multiSelect ? selectedIndustrySlugs.includes(slug) : industrySlug === slug;

  const handleIndustryClick = (slug: string) => {
    if (multiSelect) {
      const current = selectedIndustrySlugs;
      const next = current.includes(slug)
        ? current.filter((s) => s !== slug)
        : [...current, slug];
      onMultiIndustryChange?.(next);
    } else {
      onIndustryChange(slug);
    }
  };

  const handleAllClick = () => {
    if (!multiSelect) { onIndustryChange(null); return; }
    if (!activeSector) return;
    const sectorSlugs = activeSector.industries.map((i) => i.industry_slug);
    const allSelected = sectorSlugs.every((s) => selectedIndustrySlugs.includes(s));
    if (allSelected) {
      onMultiIndustryChange?.(selectedIndustrySlugs.filter((s) => !sectorSlugs.includes(s)));
    } else {
      onMultiIndustryChange?.(Array.from(new Set([...selectedIndustrySlugs, ...sectorSlugs])));
    }
  };

  const allChipSelected = multiSelect && activeSector
    ? activeSector.industries.every((i) => selectedIndustrySlugs.includes(i.industry_slug))
    : industrySlug === null;

  const selectedCountBySector = new Map<string, number>();
  if (multiSelect) {
    for (const s of sectors) {
      let n = 0;
      for (const ind of s.industries) {
        if (selectedIndustrySlugs.includes(ind.industry_slug)) n++;
      }
      if (n > 0) selectedCountBySector.set(s.sector_id, n);
    }
  }

  // --- Mutual exclusivity handlers ---
  // Clicking a sector chip clears any strategy-column selection, and vice
  // versa. When strategies is empty, the strategy-clear calls are no-ops.
  // When `mutuallyExclusive` is FALSE, both columns can be selected
  // simultaneously — cross-column clears are skipped.
  const handleSectorClick = (id: string | null) => {
    onSectorChange(id);
    if (id && mutuallyExclusive) {
      onStrategyChange?.(null);
      onThemeChange?.(null);
    }
  };

  const handleStrategyClick = (id: string | null) => {
    onStrategyChange?.(id);
    if (id && mutuallyExclusive) {
      onSectorChange(null);
      onIndustryChange(null);
    }
  };

  // --- Render: sector chips (LEFT column L1) ---
  const renderSectorChips = () => sectors.map((s) => {
    const isActive = s.sector_id === sectorId;
    const selectedCount = selectedCountBySector.get(s.sector_id) ?? 0;
    const hasSelections = multiSelect && selectedCount > 0;
    const label = hasSelections && !isActive
      ? `${s.sector_label} (${s.count}) · ${selectedCount}`
      : `${s.sector_label} (${s.count})`;
    return (
      <Chip
        key={s.sector_id}
        label={label}
        size={chipSize}
        color={isActive ? "primary" : hasSelections ? "secondary" : "default"}
        variant={isActive ? "filled" : "outlined"}
        onClick={() => handleSectorClick(s.sector_id)}
        sx={{ fontSize: chipFontSize, ...(hasSelections && !isActive ? { borderWidth: 2 } : {}) }}
      />
    );
  });

  // --- Render: industry chips (LEFT column L2) ---
  const renderIndustryChips = () => {
    if (!activeSector) return null;
    return (
      <ChipRow label={`Industry${multiSelect ? " · multi" : ""}`} chipSize={chipSize}>
        <Chip
          label={`All (${activeSector.count})`}
          size={chipSize}
          color={allChipSelected ? "secondary" : "default"}
          variant={allChipSelected ? "filled" : "outlined"}
          onClick={handleAllClick}
          sx={{ fontSize: chipFontSize }}
        />
        {activeSector.industries.map((ind) => {
          const selected = isIndustrySelected(ind.industry_slug);
          return (
            <Chip
              key={ind.industry_id}
              label={`${ind.industry_label.split("  ")[0] ?? ind.industry_label} (${ind.count})`}
              size={chipSize}
              color={selected ? "secondary" : "default"}
              variant={selected ? "filled" : "outlined"}
              onClick={() => handleIndustryClick(ind.industry_slug)}
              sx={{ fontSize: chipFontSize }}
            />
          );
        })}
      </ChipRow>
    );
  };

  // --- Render: strategy chips (RIGHT column L1) ---
  const renderStrategyChips = () => strategies!.map((s) => {
    const isActive = s.sector_id === strategyId;
    return (
      <Chip
        key={s.sector_id}
        label={`${s.sector_label} (${s.count})`}
        size={chipSize}
        color={isActive ? "primary" : "default"}
        variant={isActive ? "filled" : "outlined"}
        onClick={() => handleStrategyClick(s.sector_id)}
        sx={{ fontSize: chipFontSize }}
      />
    );
  });

  // --- Render: theme chips (RIGHT column L2) ---
  const renderThemeChips = () => {
    if (!activeStrategy) return null;
    return (
      <ChipRow label="Theme" chipSize={chipSize}>
        <Chip
          label={`All (${activeStrategy.count})`}
          size={chipSize}
          color={!themeSlug ? "secondary" : "default"}
          variant={!themeSlug ? "filled" : "outlined"}
          onClick={() => onThemeChange?.(null)}
          sx={{ fontSize: chipFontSize }}
        />
        {activeStrategy.industries.map((th) => {
          const selected = themeSlug === th.industry_slug;
          return (
            <Chip
              key={th.industry_id}
              label={`${th.industry_label} (${th.count})`}
              size={chipSize}
              color={selected ? "secondary" : "default"}
              variant={selected ? "filled" : "outlined"}
              onClick={() => onThemeChange?.(th.industry_slug)}
              sx={{ fontSize: chipFontSize }}
            />
          );
        })}
      </ChipRow>
    );
  };

  // --- Render: L3 security-level chips ---
  const renderItems = () => {
    if (!itemKind || activeItems.length === 0) return null;
    // In multi-select mode, "All" is selected (filled) when NO codes are
    // picked (no filter = show all). In single-select mode, "All" is selected
    // when `selectedItemCode` is null.
    const allSelected = multiSelectItems
      ? selectedItemCodes.length === 0
      : !selectedItemCode;
    // Per-code selection check (mode-aware).
    const isCodeSelected = (code: string) =>
      multiSelectItems
        ? selectedItemCodes.some(
            (c) => c.toUpperCase() === code.toUpperCase(),
          )
        : !!selectedItemCode &&
          selectedItemCode.toUpperCase() === code.toUpperCase();
    // Per-code click handler (mode-aware). Multi-select toggles membership;
    // single-select replaces.
    const handleCodeClick = (code: string) => {
      if (multiSelectItems) {
        const upper = code.toUpperCase();
        const next = selectedItemCodes.some(
          (c) => c.toUpperCase() === upper,
        )
          ? selectedItemCodes.filter(
              (c) => c.toUpperCase() !== upper,
            )
          : [...selectedItemCodes, code];
        onMultiItemSelected?.(next);
      } else {
        onItemSelected?.(code);
      }
    };
    const handleAllClick = () => {
      if (multiSelectItems) {
        onMultiItemSelected?.([]);
      } else {
        onClearItemSelection?.();
      }
    };
    // "All <industry>" quick-toggle chips: one per selected industry in the
    // active sector. Each is filled (the industry IS selected at L2 — that's
    // why its indices are visible here). Clicking REMOVES the industry from
    // the L2 multi-select (unselects the industry). Re-selecting is done from
    // the L2 industry row.
    const allIndustryChips =
      showAllIndustryChips && multiSelect && multiSelectItems && activeSector
        ? activeSector.industries.filter((ind) =>
            selectedIndustrySlugs.includes(ind.industry_slug),
          )
        : [];
    const handleAllIndustryClick = (slug: string) => {
      onMultiIndustryChange?.(
        selectedIndustrySlugs.filter((s) => s !== slug),
      );
    };
    return (
      <>
        <Box sx={{ display: "flex", gap: 0.5, alignItems: "center", flexWrap: "wrap" }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600, minWidth: 56, fontSize: chipFontSize }}>
            {itemKind}
            {multiSelectItems ? " · multi" : ""}
          </Typography>
          {/* "All (N)" chip: clears the L3 individual-index filter. HIDDEN when
              showAllIndustryChips is on (Industry Sentiments) — there the L3
              row uses per-industry "All <industry> (N)" chips instead, and the
              generic All is redundant. */}
          {!showAllIndustryChips && (
            <Chip
              label={`All (${activeItems.length})`}
              size={chipSize}
              color={allSelected ? "secondary" : "default"}
              variant={allSelected ? "filled" : "outlined"}
              onClick={handleAllClick}
              sx={{ fontSize: chipFontSize }}
            />
          )}
          {allIndustryChips.map((ind) => (
            <Chip
              key={`all-ind-${ind.industry_id}`}
              label={`All ${ind.industry_label.split("  ")[0] ?? ind.industry_label} (${ind.count})`}
              size={chipSize}
              color="secondary"
              variant="filled"
              onClick={() => handleAllIndustryClick(ind.industry_slug)}
              sx={{ fontSize: chipFontSize }}
            />
          ))}
          {multiSelectItems && selectedItemCodes.length > 0 && (
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: chipFontSize }}>
              {selectedItemCodes.length} selected
            </Typography>
          )}
          {totalItemPages > 1 && (
            <Pagination
              count={totalItemPages}
              page={itemPageClamped}
              onChange={(_, v) => setItemPage(v)}
              size="small"
              siblingCount={0}
              boundaryCount={1}
              sx={{
                ml: "auto",
                "& .MuiPagination-ul": { fontSize: chipFontSize, gap: 0.25 },
                "& .MuiButtonBase-root": { minWidth: 22, height: 22, padding: 0, fontSize: chipFontSize },
              }}
            />
          )}
        </Box>
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "repeat(auto-fill, minmax(140px, 1fr))",
              sm: "repeat(auto-fill, minmax(180px, 1fr))",
            },
            gap: 0.5,
            pl: { xs: 1, sm: 0 },
            ml: { xs: 0, sm: 7 },
          }}
        >
          {pagedItems.map((it) => {
            const isSel = isCodeSelected(it.code);
            return (
              <Chip
                key={it.code}
                label={`${it.code} ${it.name}`}
                size={chipSize}
                color={isSel ? "primary" : "default"}
                variant={isSel ? "filled" : "outlined"}
                onClick={() => handleCodeClick(it.code)}
                sx={{
                  fontSize: chipFontSize,
                  width: "100%",
                  justifyContent: "flex-start",
                  "& .MuiChip-label": {
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    pr: 0.5,
                  },
                }}
              />
            );
          })}
        </Box>
      </>
    );
  };

  // --- Similar Indices (L3 expansion, Index only) ---
  // Shown beneath the Index chips when exactly one index is selected. In
  // multi-select mode, only when a single code is picked (similar indices are
  // per-subject; ambiguous for a multi-code selection). Clicking a similar
  // chip selects that index (replaces the selection in both modes).
  const similarCode =
    itemKind === "Index"
      ? (multiSelectItems
          ? (selectedItemCodes.length === 1 ? selectedItemCodes[0] : null)
          : selectedItemCode)
      : null;

  // --- Locate a code in the classification tree (sector→industry→items or
  // strategy→theme→items). Returns the first match so the nav can "jump" to
  // the clicked similar index's sector/industry. Searches the industry
  // (LEFT) column first, then the strategy (RIGHT) column. ---
  const findCodeLocation = (code: string): {
    sectorId: string;
    industrySlug: string;
    isStrategy: boolean;
  } | null => {
    for (const sec of sectors) {
      for (const ind of sec.industries) {
        if (ind.items.some((it) => it.code === code)) {
          return { sectorId: sec.sector_id, industrySlug: ind.industry_slug, isStrategy: false };
        }
      }
    }
    if (strategies) {
      for (const sec of strategies) {
        for (const ind of sec.industries) {
          if (ind.items.some((it) => it.code === code)) {
            return { sectorId: sec.sector_id, industrySlug: ind.industry_slug, isStrategy: true };
          }
        }
      }
    }
    return null;
  };

  const handleSimilarSelect = (code: string) => {
    // Jump the L1/L2 nav to the clicked index's sector/industry FIRST, so the
    // user sees where it belongs. Only in single-select mode (multi-select L2
    // manages an array — clobbering it would discard the user's selection).
    // NOTE: parent handlers (e.g. IndexPage) clear the selected item code on
    // sector/industry change, so onItemSelected must be called LAST to win.
    if (!multiSelect) {
      const loc = findCodeLocation(code);
      if (loc) {
        if (loc.isStrategy) {
          onStrategyChange?.(loc.sectorId);
          onThemeChange?.(loc.industrySlug);
          if (mutuallyExclusive) {
            onSectorChange(null);
            onIndustryChange(null);
          }
        } else {
          onSectorChange(loc.sectorId);
          onIndustryChange(loc.industrySlug);
          if (mutuallyExclusive) {
            onStrategyChange?.(null);
            onThemeChange?.(null);
          }
        }
      }
    }

    // Select the code LAST (after sector/industry) so it survives the parent's
    // setSearchCode(null) in its sector/industry change handlers.
    if (multiSelectItems) {
      onMultiItemSelected?.([code]);
    } else {
      onItemSelected?.(code);
    }
  };

  // --- Main render ---
  return (
    <Stack
      spacing={rowSpacing}
      sx={{
        p: 1.5,
        mb: 2,
        bgcolor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        ...sx,
      }}
    >
      {/* Exchange filter — TWO rows mirroring the DB-derived
          `is_primary_exchange` flag (see classify.ts).
            Row 1 (primary, is_primary_exchange=TRUE):  All (primary) / SSE / SZSE / BSE
            Row 2 (cross-border, is_primary_exchange=FALSE): HK / Overseas  [hidden by default]
          Row 2 is hidden by default — click the ▼ triangle next to "Exchange"
          to expand. Indented under row 1 to indicate hierarchy. Both rows
          drive the same single `exchange` filter state. "All (primary)" is the
          DEFAULT (value="PRIMARY") — matches SS/STAR/SZ/GEM/BJ, excluding
          cross-border so HK/Overseas securities are opt-in. */}
      {showExchange && (
        <>
          <ChipRow
            label="Exchange"
            chipSize={chipSize}
            labelAdornment={
              <Box
                component="span"
                onClick={() => setShowCrossBorder((v) => !v)}
                title={showCrossBorder ? "Hide cross-border exchanges" : "Show cross-border exchanges (HK / Overseas)"}
                sx={{
                  cursor: "pointer",
                  ml: 0.25,
                  fontSize: chipSize === "small" ? "0.6rem" : "0.7rem",
                  lineHeight: 1,
                  userSelect: "none",
                  color: "text.secondary",
                  display: "inline-flex",
                  alignItems: "center",
                }}
              >
                {showCrossBorder ? "▼" : "▶"}
              </Box>
            }
          >
            {PRIMARY_EXCHANGE_OPTIONS.map((opt) => (
              <Chip
                key={opt.label}
                label={opt.label}
                size={chipSize}
                color={exchange === opt.value ? "primary" : "default"}
                variant={exchange === opt.value ? "filled" : "outlined"}
                onClick={() => onExchangeChange(opt.value)}
                sx={{ fontSize: chipFontSize }}
              />
            ))}
            {/* Inline loading spinner — shown while the classification tree
                is (re)fetched (initial mount or exchange change). Pushed to
                the right end of the row so it sits after all exchange chips. */}
            {loading && (
              <Box sx={{ display: "inline-flex", alignItems: "center", ml: "auto", pl: 1 }}>
                <CircularProgress size={14} sx={{ color: "text.secondary" }} />
              </Box>
            )}
          </ChipRow>
          {showCrossBorder && (
            <Box sx={{ pl: 3.5 }}>
              <ChipRow label="Cross-Border" chipSize={chipSize}>
                {SECONDARY_EXCHANGE_OPTIONS.map((opt) => (
                  <Chip
                    key={opt.label}
                    label={opt.label}
                    size={chipSize}
                    color={exchange === opt.value ? "primary" : "default"}
                    variant={exchange === opt.value ? "filled" : "outlined"}
                    onClick={() => onExchangeChange(opt.value)}
                    sx={{ fontSize: chipFontSize }}
                  />
                ))}
              </ChipRow>
            </Box>
          )}
        </>
      )}

      {/* Classification columns */}
      {hasStrategyColumn ? (
        <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" }, gap: 2 }}>
          {/* LEFT: Sector → Industry */}
          <Stack spacing={rowSpacing}>
            <ChipRow label="Sector" chipSize={chipSize}>{renderSectorChips()}</ChipRow>
            {renderIndustryChips()}
          </Stack>
          {/* RIGHT: Strategy → Theme */}
          <Stack spacing={rowSpacing}>
            <ChipRow label="Strategy" chipSize={chipSize}>{renderStrategyChips()}</ChipRow>
            {renderThemeChips()}
          </Stack>
        </Box>
      ) : (
        <Stack spacing={rowSpacing}>
          <ChipRow label="Sector" chipSize={chipSize}>{renderSectorChips()}</ChipRow>
          {renderIndustryChips()}
        </Stack>
      )}

      {/* L3 security-level chips */}
      {renderItems()}

      {/* Similar Indices — expandable top-5 by mutual shared composition
          weight, shown beneath the Index chips when one index is selected.
          Constrained to the LEFT column width so it sits under the
          Sector/Industry nav (not spanning the full two-column layout). */}
      {similarCode && (
        <Box sx={{ alignSelf: "stretch", maxWidth: { md: "50%" } }}>
          <SimilarIndicesList
            code={similarCode}
            chipSize={chipSize}
            chipFontSize={chipFontSize}
            onSelectedCode={handleSimilarSelect}
          />
        </Box>
      )}
    </Stack>
  );
}
