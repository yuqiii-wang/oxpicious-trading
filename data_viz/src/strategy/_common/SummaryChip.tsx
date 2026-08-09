/**
 * SummaryChip — compact label + value display for strategy summary stats.
 *
 * Used by every strategy page to show Total Return, Realized P&L, Trades,
 * Final Cash, etc. below the Run button.
 */
import { Box, Typography } from "@mui/material";

interface SummaryChipProps {
  label: string;
  value: string;
  color?: "default" | "success" | "error";
}

const COLOR_MAP: Record<string, string> = {
  default: "text.secondary",
  success: "success.main",
  error: "error.main",
};

export default function SummaryChip({ label, value, color = "default" }: SummaryChipProps) {
  return (
    <Box sx={{ display: "flex", flexDirection: "column" }}>
      <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
        {label}
      </Typography>
      <Typography variant="body2" sx={{ fontWeight: 700, color: COLOR_MAP[color] }}>
        {value}
      </Typography>
    </Box>
  );
}
