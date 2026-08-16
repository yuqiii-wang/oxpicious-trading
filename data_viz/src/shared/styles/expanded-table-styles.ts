/**
 * Shared "expanded table" styles — the visual baseline used by tables that
 * render inside an expanded card section (e.g. LinkedEtfsList on the Index
 * Baseline page, Monthly PE & Dividend Stats on the PE & Dividend page).
 *
 * Visual contract:
 *   • TableContainer — thin divider border, rounded corners, scrollable,
 *     optional maxHeight for the scroll viewport.
 *   • Header cells   — primary.main background (on the cell, so it survives
 *     MUI's stickyHeader default bg override), white bold compact text.
 *   • Body rows      — alternating paper / hover bg, hover lifts to
 *     action.selected.
 *   • Numeric cells  — monospace, no-wrap.
 *   • Aggregation row (optional) — sticky pinned to top with primary.dark bg.
 *
 * The header bg lives on the CELL (not the row) so it renders correctly even
 * under `<Table stickyHeader>` (MUI injects a background on sticky header
 * cells that would otherwise hide a row-level bg). `bgcolor: "primary.main"`
 * is a palette path resolved by MUI's sx at render time, so no theme import
 * is needed here. The aggregation-row sx is theme-dependent (primary.dark)
 * and exposed as a function taking the MUI theme.
 */
import type { Theme } from "@mui/material/styles";

/** Header cell sx — primary.main bg, white text, bold, compact, no-wrap. */
export const expandedTableHeadCellSx = {
  color: "#fff",
  fontWeight: 600,
  fontSize: "0.7rem",
  whiteSpace: "nowrap",
  bgcolor: "primary.main",
} as const;

/** Body cell sx — compact, no-wrap. */
export const expandedTableBodyCellSx = {
  fontSize: "0.72rem",
  whiteSpace: "nowrap",
} as const;

/** Numeric body cell sx — compact, monospace, no-wrap. */
export const expandedTableNumCellSx = {
  ...expandedTableBodyCellSx,
  fontFamily: "monospace",
} as const;

/**
 * TableContainer sx — bordered, rounded, scrollable.
 * Pass `maxHeight` (px) to enable a scroll viewport; omit for unbounded
 * height (grows with content).
 */
export const expandedTableContainerSx = (maxHeight?: number) => ({
  border: "1px solid",
  borderColor: "divider",
  borderRadius: 1,
  overflow: "auto",
  ...(maxHeight != null ? { maxHeight } : {}),
});

/**
 * Body row sx — alternating paper / hover background with hover highlight.
 * `idx` is the 0-based row index (used for zebra striping).
 */
export const expandedTableBodyRowSx = (idx: number) => ({
  bgcolor: idx % 2 === 0 ? "background.paper" : "action.hover",
  "&:hover": { bgcolor: "action.selected" },
});

/**
 * Sticky aggregation-row cell sx — pinned to the top of the scroll viewport
 * with a primary.dark background so index-level totals stay visible while
 * body rows scroll beneath. Pass the MUI theme.
 */
export const expandedTableAggCellSx = (theme: Theme) => ({
  fontSize: "0.7rem",
  fontWeight: 700,
  whiteSpace: "nowrap",
  color: "#fff",
  position: "sticky",
  top: 0,
  zIndex: 2,
  bgcolor: theme.palette.primary.dark,
  borderBottom: "2px solid",
  borderColor: "divider",
});
