/**
 * SectionLabel — one MA-Spread button-group title row.
 *
 * Renders the group's title (from groupDescriptions.ts) in the panel's
 * caption style — highlighted when the section's buttons are toggled on —
 * plus the shared InfoMark; clicking the mark opens the popover with the
 * group's description text. Titles and descriptions live in
 * groupDescriptions.ts, so the panel only passes the group id.
 */
import { Box, Typography } from "@mui/material";
import { InfoMark } from "@/shared/components/description";
import { MA_SPREAD_GROUP_INFO, type MaSpreadGroupId } from "./groupDescriptions";

interface SectionLabelProps {
  /** Which button group — picks the title and the popover description. */
  id: MaSpreadGroupId;
  /** True when the section's buttons are toggled on — primary color + bold. */
  active?: boolean;
  /** Text appended after the title (e.g. " — loading…", " — click to switch"). */
  suffix?: string;
  /** Standalone caption above its grid (the pair sections) instead of a
   *  full-width cell inside one — bigger font, no active highlight use. */
  standalone?: boolean;
  /** Top margin for standalone captions (the EMA section sits below the Simple one). */
  mt?: number;
}

export function SectionLabel({
  id,
  active = false,
  suffix,
  standalone = false,
  mt,
}: SectionLabelProps) {
  const group = MA_SPREAD_GROUP_INFO[id];

  return (
    <Box
      sx={{
        ...(standalone ? {} : { gridColumn: "1 / -1" }),
        ...(mt != null ? { mt } : {}),
        mb: 0.5,
      }}
    >
      <Typography
        variant="caption"
        component={standalone ? undefined : "span"}
        sx={{
          fontSize: standalone ? "0.7rem" : "0.65rem",
          display: standalone ? "block" : undefined,
          color: active ? "primary.main" : "text.secondary",
          fontWeight: active ? 700 : 400,
        }}
      >
        {group.title}
        {suffix}
        <InfoMark title={group.title} description={group.description} />
      </Typography>
    </Box>
  );
}
