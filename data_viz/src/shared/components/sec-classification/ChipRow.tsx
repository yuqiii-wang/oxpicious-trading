/**
 * ChipRow — a labeled row of chips: the shared chrome of every
 * SecClassificationNav row (label + optional adornment + children).
 * Split out of SecClassificationNav.tsx (2026-10-20, >900-line split);
 * exported for the INHERITED VARIANTS of the nav (e.g.
 * SecClassificationNavExchange) so they reuse the exact same chrome
 * instead of duplicating it.
 */
import type { ReactNode } from "react";
import { Box, Typography } from "@mui/material";

export interface ChipRowProps {
  label: string;
  chipSize: "small" | "medium";
  children: ReactNode;
  /** Optional element rendered immediately after the label (e.g. expand/collapse triangle). */
  labelAdornment?: ReactNode;
}

export function ChipRow({ label, chipSize, children, labelAdornment }: ChipRowProps) {
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
