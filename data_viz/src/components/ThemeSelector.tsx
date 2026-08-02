/**
 * Two-level theme selector — L1 sector chips → L2 industry chips,
 * plus an exchange filter row.
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
 */
import { Box, Chip, Stack, Typography } from "@mui/material";
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
}

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
}: Props) {
  const activeSector = sectors.find((s) => s.sector_id === sectorId) ?? null;

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
    </Stack>
  );
}
