/**
 * Two-level theme selector — L1 sector chips → L2 industry chips,
 * plus an exchange filter row, plus an optional L3 security-level row.
 *
 * Reusable across ETF and Index pages. Reads the two-level taxonomy tree
 * (SectorNode[]) from the backend, which is precomputed by the Python
 * classification scripts and stored in stats.sec_classification (type='etf'/'index').
 *
 * Row 0: Exchange chips (All, SSE, SZSE, BSE) — filters securities by listing
 *        exchange. "All" clears the exchange filter.
 * Row 1: L1 sector chips (e.g. 金融, 科技, 医药, 宽基, 创业板 …).
 *        Clicking a sector selects it and reveals its L2 industries in Row 2.
 * Row 2: L2 industry chips for the selected sector (e.g. 银行, 证券, 保险 …).
 *        "All" chip clears the industry filter (shows all items in the sector).
 * Row 3: (Optional) L3 security-level chips — when `itemKind` is set, renders
 *        one chip per individual Index/ETF under the active industry (or all
 *        items in the sector when industry="All"). Paginated 10 per page so
 *        large industries don't overflow the navbar. Clicking a chip narrows
 *        the parent page to display ONLY that security (via onItemSelected);
 *        the parent's "All" chip clears the selection.
 */
import { useEffect, useMemo, useState } from "react";
import { Box, Chip, Pagination, Stack, Typography } from "@mui/material";
import type { SectorNode } from "../../shared/types";

/** Exchange filter options — maps to sec_classification.exchange column.
 *  SS includes STAR (科创板), SZ includes GEM (创业板) — both are sub-boards
 *  of SSE and SZSE respectively. */
const EXCHANGE_OPTIONS: Array<{ value: string | null; label: string }> = [
  { value: null, label: "All" },
  { value: "SS", label: "SSE" },
  { value: "SZ", label: "SZSE" },
  { value: "BJ", label: "BSE" },
];

interface Props {
  sectors: SectorNode[];
  sectorId: string | null;
  industrySlug: string | null;
  exchange: string | null;
  onSectorChange: (id: string | null) => void;
  onIndustryChange: (slug: string | null) => void;
  onExchangeChange: (ex: string | null) => void;
  /** Multi-select mode (optional). When true, industry chips support multiple
   *  selections: `selectedIndustrySlugs` holds the current selection and
   *  `onMultiIndustryChange` is called on each toggle. The active sector stays
   *  single-select (it only controls which industries are visible in row 2),
   *  but selected industries PERSIST across sector switches — so the user can
   *  pick industries from multiple sectors. The "All" chip toggles ALL
   *  industries in the current sector (added to / removed from the selection).
   *  In multi-select mode, `industrySlug` and `onIndustryChange` are ignored. */
  multiSelect?: boolean;
  selectedIndustrySlugs?: string[];
  onMultiIndustryChange?: (slugs: string[]) => void;
  /** Optional L3 security-level row (Row 3). When set, renders one chip per
   *  individual Index/ETF/Stock under the active industry (or all items in
   *  the active sector when industry="All"), paginated 10 per page. Clicking
   *  a chip narrows the parent page to display ONLY that security. */
  itemKind?: "Index" | "ETF" | "Stock";
  /** Currently active security code (null = browsing mode). When set, the
   *  matching chip in Row 3 renders as selected and the "All" chip is
   *  de-emphasized. */
  selectedItemCode?: string | null;
  /** Called when the user clicks an item chip — parent page should switch
   *  to single-result mode for that code (bypassing sector/industry filter). */
  onItemSelected?: (code: string) => void;
  /** Called when the user clicks the "All" chip in Row 3 — parent page
   *  should clear single-result mode and return to paginated browsing. */
  onClearItemSelection?: () => void;
}

/** Number of L3 security chips shown per page in Row 3.
 *  Grid shows 8 chips per row, so 16 fills 2 rows before paginating. */
const ITEMS_PAGE_SIZE = 16;

export default function ThemeSelector({
  sectors,
  sectorId,
  industrySlug,
  exchange,
  onSectorChange,
  onIndustryChange,
  onExchangeChange,
  multiSelect = false,
  selectedIndustrySlugs = [],
  onMultiIndustryChange,
  itemKind,
  selectedItemCode = null,
  onItemSelected,
  onClearItemSelection,
}: Props) {
  const activeSector = sectors.find((s) => s.sector_id === sectorId) ?? null;

  // Row 3 pagination state — 1-based page index. Resets to page 1 whenever
  // the active sector or industry changes (the items list changes too).
  const [itemPage, setItemPage] = useState(1);
  useEffect(() => {
    setItemPage(1);
  }, [sectorId, industrySlug]);

  // Build the L3 items list for the active industry (or all items in the
  // active sector when industrySlug is null/"All"). De-duplicates by code
  // since an item may appear under multiple industries (multi-tag indices).
  const activeItems = useMemo(() => {
    if (!activeSector) return [];
    if (industrySlug) {
      const ind = activeSector.industries.find(
        (i) => i.industry_slug === industrySlug,
      );
      return ind ? ind.items : [];
    }
    const seen = new Set<string>();
    const all: Array<{ code: string; name: string }> = [];
    for (const ind of activeSector.industries) {
      for (const it of ind.items) {
        if (!seen.has(it.code)) {
          seen.add(it.code);
          all.push(it);
        }
      }
    }
    return all;
  }, [activeSector, industrySlug]);

  // Clamp page if items shrank (e.g. industry switched). Use Math.max to
  // guarantee at least 1 page so Pagination always has a valid value.
  const totalItemPages = Math.max(
    1,
    Math.ceil(activeItems.length / ITEMS_PAGE_SIZE),
  );
  const itemPageClamped = Math.min(itemPage, totalItemPages);
  const pagedItems = activeItems.slice(
    (itemPageClamped - 1) * ITEMS_PAGE_SIZE,
    itemPageClamped * ITEMS_PAGE_SIZE,
  );

  // Helper: is a given industry slug currently selected?
  const isIndustrySelected = (slug: string): boolean =>
    multiSelect
      ? selectedIndustrySlugs.includes(slug)
      : industrySlug === slug;

  // Helper: handle an industry chip click in either mode.
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

  // Helper: handle the "All" chip click in either mode.
  //  - single-select: clears the industry filter (null).
  //  - multi-select: toggles ALL industries in the current sector. If every
  //    industry in the sector is already selected, deselect them; otherwise
  //    add them to the selection (preserving other sectors' selections).
  const handleAllClick = () => {
    if (!multiSelect) {
      onIndustryChange(null);
      return;
    }
    if (!activeSector) return;
    const sectorSlugs = activeSector.industries.map((i) => i.industry_slug);
    const allSelected = sectorSlugs.every((s) => selectedIndustrySlugs.includes(s));
    if (allSelected) {
      onMultiIndustryChange?.(
        selectedIndustrySlugs.filter((s) => !sectorSlugs.includes(s)),
      );
    } else {
      onMultiIndustryChange?.(
        Array.from(new Set([...selectedIndustrySlugs, ...sectorSlugs])),
      );
    }
  };

  // Whether the "All" chip should render as selected in multi-select mode
  // (true only when EVERY industry in the active sector is selected).
  const allChipSelected = multiSelect && activeSector
    ? activeSector.industries.every((i) =>
        selectedIndustrySlugs.includes(i.industry_slug),
      )
    : industrySlug === null;

  // In multi-select mode, compute which sectors contain at least one selected
  // industry (so the user can see — at a glance — which sectors contribute to
  // the current multi-industry selection, even when those sectors are not the
  // active browsed one). Maps sector_id → count of selected industries.
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

  return (
    <Stack
      spacing={1}
      sx={{
        p: 1.5,
        mb: 2,
        bgcolor: "background.paper",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
      }}
    >
      {/* Row 0: Exchange chips */}
      <Box sx={{ display: "flex", gap: 0.75, flexWrap: "wrap", alignItems: "center" }}>
        <Typography
          variant="subtitle2"
          sx={{ fontWeight: 600, minWidth: 56, fontSize: "0.75rem" }}
        >
          Exchange
        </Typography>
        {EXCHANGE_OPTIONS.map((opt) => (
          <Chip
            key={opt.label}
            label={opt.label}
            size="small"
            color={exchange === opt.value ? "primary" : "default"}
            variant={exchange === opt.value ? "filled" : "outlined"}
            onClick={() => onExchangeChange(opt.value)}
            sx={{ fontSize: "0.7rem" }}
          />
        ))}
      </Box>

      {/* Row 1: L1 sector chips.
          In multi-select mode, three visual states:
            • Active (browsed) sector → primary filled (strongest emphasis).
            • Sector with ≥1 selected industry (not active) → secondary outlined
              with a "· N" count suffix (medium emphasis — shows it contributes
              to the multi-industry selection without being the browsed one).
            • Default (no selections, not active) → default outlined.
          Single-select mode keeps the original two-state behavior. */}
      <Box sx={{ display: "flex", gap: 0.75, flexWrap: "wrap", alignItems: "center" }}>
        <Typography
          variant="subtitle2"
          sx={{ fontWeight: 600, minWidth: 56, fontSize: "0.75rem" }}
        >
          Sector
        </Typography>
        {sectors.map((s) => {
          const isActive = s.sector_id === sectorId;
          const selectedCount = selectedCountBySector.get(s.sector_id) ?? 0;
          const hasSelections = multiSelect && selectedCount > 0;
          // Active wins over hasSelections for color emphasis.
          const chipColor = isActive
            ? "primary"
            : hasSelections
              ? "secondary"
              : "default";
          const chipVariant = isActive ? "filled" : "outlined";
          const label = hasSelections && !isActive
            ? `${s.sector_label} (${s.count}) · ${selectedCount}`
            : `${s.sector_label} (${s.count})`;
          return (
            <Chip
              key={s.sector_id}
              label={label}
              size="small"
              color={chipColor}
              variant={chipVariant}
              onClick={() => onSectorChange(s.sector_id)}
              sx={{
                fontSize: "0.7rem",
                // Subtle border emphasis for non-active sectors with selections
                // (secondary outlined is already distinct, but add a slightly
                // thicker border so it stands out from default outlined chips).
                ...(hasSelections && !isActive
                  ? { borderWidth: 2 }
                  : {}),
              }}
            />
          );
        })}
      </Box>

      {/* Row 2: L2 industry chips (only for the selected sector).
          In multi-select mode, chips toggle in/out of `selectedIndustrySlugs`
          and the selection persists when the active sector changes — letting
          the user pick industries from multiple sectors. The active sector
          only controls which industries are VISIBLE here. */}
      {activeSector && (
        <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", alignItems: "center" }}>
          <Typography
            variant="subtitle2"
            sx={{ fontWeight: 600, minWidth: 56, fontSize: "0.75rem" }}
          >
            Industry{multiSelect ? " · multi" : ""}
          </Typography>
          <Chip
            label={`All (${activeSector.count})`}
            size="small"
            color={allChipSelected ? "secondary" : "default"}
            variant={allChipSelected ? "filled" : "outlined"}
            onClick={handleAllClick}
            sx={{ fontSize: "0.7rem" }}
          />
          {activeSector.industries.map((ind) => {
            const selected = isIndustrySelected(ind.industry_slug);
            return (
              <Chip
                key={ind.industry_id}
                label={`${ind.industry_label.split("  ")[0] ?? ind.industry_label} (${ind.count})`}
                size="small"
                color={selected ? "secondary" : "default"}
                variant={selected ? "filled" : "outlined"}
                onClick={() => handleIndustryClick(ind.industry_slug)}
                sx={{ fontSize: "0.7rem" }}
              />
            );
          })}
        </Box>
      )}

      {/* Row 3: L3 security-level chips (only when `itemKind` is set).
          Renders one chip per individual Index/ETF under the active industry
          (or all items in the active sector when industry="All"), paginated
          10 per page so large industries don't overflow the navbar. Clicking
          a chip narrows the parent page to display ONLY that security; the
          "All" chip clears the selection (returns to paginated browsing).

          LAYOUT: Header row (label + All chip + pagination) is on its own
          flex row so it stays compact. Item chips live in a CSS grid with
          `minmax(180px, 1fr)` columns — every chip in the same column has
          the SAME width regardless of name length, so the grid looks
          perfectly aligned and wraps predictably. Long names truncate with
          ellipsis inside their fixed-width cell. */}
      {itemKind && activeItems.length > 0 && (
        <>
          <Box sx={{ display: "flex", gap: 0.5, alignItems: "center", flexWrap: "wrap" }}>
            <Typography
              variant="subtitle2"
              sx={{ fontWeight: 600, minWidth: 56, fontSize: "0.75rem" }}
            >
              {itemKind}
            </Typography>
            <Chip
              label={`All (${activeItems.length})`}
              size="small"
              color={!selectedItemCode ? "secondary" : "default"}
              variant={!selectedItemCode ? "filled" : "outlined"}
              onClick={() => onClearItemSelection?.()}
              sx={{ fontSize: "0.7rem" }}
            />
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
                  "& .MuiPagination-ul": { fontSize: "0.7rem", gap: 0.25 },
                  "& .MuiButtonBase-root": {
                    minWidth: 22,
                    height: 22,
                    padding: 0,
                    fontSize: "0.7rem",
                  },
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
              // Indent past the 56px label column so item chips align under
              // the industry-chips area (Row 2) rather than under the label.
              pl: { xs: 1, sm: 0 },
              ml: { xs: 0, sm: 7 },
            }}
          >
            {pagedItems.map((it) => {
              const isSel =
                !!selectedItemCode &&
                it.code.toUpperCase() === selectedItemCode.toUpperCase();
              return (
                <Chip
                  key={it.code}
                  label={`${it.code} ${it.name}`}
                  size="small"
                  color={isSel ? "primary" : "default"}
                  variant={isSel ? "filled" : "outlined"}
                  onClick={() => onItemSelected?.(it.code)}
                  sx={{
                    fontSize: "0.7rem",
                    // Fill the grid cell so every chip in a column shares the
                    // same width — names truncate with ellipsis when long.
                    width: "100%",
                    justifyContent: "flex-start",
                    "& .MuiChip-label": {
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      // Leave a hair of right padding so the ellipsis doesn't
                      // kiss the chip border.
                      pr: 0.5,
                    },
                  }}
                />
              );
            })}
          </Box>
        </>
      )}
    </Stack>
  );
}
