/**
 * tickDotItem — the header-filter tick-ITEM renderer for the signal
 * panels' labeled-vocabulary columns (the rules table's trend / regime
 * and the history table's regime): a colored dot before the raw label,
 * the color read from the column's own accent palette (the trend chip
 * slots / regimeBands accents). Cosmetic only — the tick filters match
 * the RAW value; this decorates the menu. Pure .tsx (no theme read:
 * callers close over their palette), kept in the package so the panels
 * share one decoration.
 */
import type { ReactNode } from "react";
import { Box } from "@mui/material";

/** Build the renderItem for a ticks filter: a colored dot before the
 *  raw value (rows without an accent — unknown labels — render the
 *  bare label). */
export function tickDotItem(
  colorOf: (v: string) => string | undefined,
): (v: string) => ReactNode {
  return (v: string) => {
    const color = colorOf(v);
    return (
      <Box
        component="span"
        sx={{ display: "inline-flex", alignItems: "center", gap: 0.5 }}
      >
        {color != null && (
          <Box
            component="span"
            sx={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              bgcolor: color,
              flexShrink: 0,
              display: "inline-block",
            }}
          />
        )}
        {v}
      </Box>
    );
  };
}
