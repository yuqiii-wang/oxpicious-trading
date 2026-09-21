/**
 * PostChip — the small outlined chip every post feed uses (source, code,
 * model, keyword and count chips). One place owns the sizing so the feeds
 * stay visually identical; per-call sx merges after the defaults.
 */
import type { ChipProps, SxProps, Theme } from "@mui/material";
import { Chip } from "@mui/material";

export default function PostChip({
  label,
  color = "default",
  title,
  sx,
  ...rest
}: Omit<ChipProps, "size" | "variant"> & { sx?: SxProps<Theme> }) {
  return (
    <Chip
      {...rest}
      label={label}
      title={title}
      color={color}
      size="small"
      variant="outlined"
      sx={{ fontSize: "0.65rem", height: 18, ...(sx as object) }}
    />
  );
}
