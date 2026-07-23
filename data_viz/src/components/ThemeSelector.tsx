/**
 * Two-level theme selector — L1 sector chips → L2 industry chips.
 *
 * Reusable across ETF and Index pages. Reads the two-level taxonomy tree
 * (SectorNode[]) from the backend, which is precomputed by the Python
 * classification scripts and stored in stats.etf_meta / stats.index_meta.
 *
 * Row 1: L1 sector chips (e.g. 金融, 科技, 医药, 宽基, 创业板 …).
 *        Clicking a sector selects it and reveals its L2 industries in Row 2.
 * Row 2: L2 industry chips for the selected sector (e.g. 银行, 证券, 保险 …).
 *        "All" chip clears the industry filter (shows all items in the sector).
 */
import { Box, Chip, Stack, Typography } from "@mui/material";
import type { SectorNode } from "../../shared/types";

interface Props {
  sectors: SectorNode[];
  sectorId: string | null;
  industrySlug: string | null;
  onSectorChange: (id: string | null) => void;
  onIndustryChange: (slug: string | null) => void;
}

export default function ThemeSelector({
  sectors,
  sectorId,
  industrySlug,
  onSectorChange,
  onIndustryChange,
}: Props) {
  const activeSector = sectors.find((s) => s.sector_id === sectorId) ?? null;

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
      {/* Row 1: L1 sector chips */}
      <Box sx={{ display: "flex", gap: 0.75, flexWrap: "wrap", alignItems: "center" }}>
        <Typography
          variant="subtitle2"
          sx={{ fontWeight: 600, minWidth: 56, fontSize: "0.75rem" }}
        >
          Sector
        </Typography>
        {sectors.map((s) => (
          <Chip
            key={s.sector_id}
            label={`${s.sector_label} (${s.count})`}
            size="small"
            color={s.sector_id === sectorId ? "primary" : "default"}
            variant={s.sector_id === sectorId ? "filled" : "outlined"}
            onClick={() => onSectorChange(s.sector_id)}
            sx={{ fontSize: "0.7rem" }}
          />
        ))}
      </Box>

      {/* Row 2: L2 industry chips (only for the selected sector) */}
      {activeSector && (
        <Box sx={{ display: "flex", gap: 0.5, flexWrap: "wrap", alignItems: "center" }}>
          <Typography
            variant="subtitle2"
            sx={{ fontWeight: 600, minWidth: 56, fontSize: "0.75rem" }}
          >
            Industry
          </Typography>
          <Chip
            label={`All (${activeSector.count})`}
            size="small"
            color={industrySlug === null ? "secondary" : "default"}
            variant={industrySlug === null ? "filled" : "outlined"}
            onClick={() => onIndustryChange(null)}
            sx={{ fontSize: "0.7rem" }}
          />
          {activeSector.industries.map((ind) => (
            <Chip
              key={ind.industry_id}
              label={`${ind.industry_label.split("  ")[0] ?? ind.industry_label} (${ind.count})`}
              size="small"
              color={ind.industry_slug === industrySlug ? "secondary" : "default"}
              variant={ind.industry_slug === industrySlug ? "filled" : "outlined"}
              onClick={() => onIndustryChange(ind.industry_slug)}
              sx={{ fontSize: "0.7rem" }}
            />
          ))}
        </Box>
      )}
    </Stack>
  );
}
