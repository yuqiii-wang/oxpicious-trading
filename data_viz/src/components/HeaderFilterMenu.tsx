/**
 * HeaderFilterMenu — a compact column-header filter for tables: the column
 * label followed by a tiny filter button that opens a checkbox (tick) list
 * of the column's distinct values. Ticking values filters the table's rows
 * to those matching ANY ticked value (multi-select, AND across columns).
 *
 * "(All)" is a consistent tri-state master checkbox:
 *   • checked  ⇔ everything shown (selection empty OR every value ticked —
 *     both are canonical "no filter" states, and the item rows then ALL
 *     render ticked to match);
 *   • indeterminate ⇔ a proper-subset filter is active (button badge on);
 *   • clicking it always resets to the empty selection (no filter).
 * Ticking/unticking items starts from the full value set when everything is
 * shown, and ticking the last missing item normalizes back to the empty
 * selection — so `selected` never stores the full set.
 *
 * The popup's first row toggles the ROW ORDER direction (Ascending ⇄
 * Descending): it makes this column the table's ordering key (see
 * useTableHeaderFilters) and flips its direction. The tick options are
 * listed in that direction too (descending reverses the caller's
 * ascending-sorted `values`); selection is unaffected by order.
 *
 * The trigger button + items are styled for a primary.main header cell
 * (white icon/badge) — all tables using these menus have blue headers.
 */
import { useState, type ReactNode } from "react";
import ArrowDownwardIcon from "@mui/icons-material/ArrowDownward";
import ArrowUpwardIcon from "@mui/icons-material/ArrowUpward";
import FilterListIcon from "@mui/icons-material/FilterList";
import {
  Badge,
  Box,
  Button,
  Checkbox,
  ListItemText,
  MenuItem,
  MenuList,
  Popover,
  Typography,
} from "@mui/material";

/** Shared trigger-button sx for ALL header filter menus (ticks / date /
 *  range) — one visual affordance: tiny FilterList icon, translucent white
 *  when the column's filter is inactive, solid white + secondary dot badge
 *  when active. Tuned for the primary.main header background every shared
 *  table uses. */
export const headerFilterButtonSx = (active: boolean) =>
  ({
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    border: "none",
    background: "transparent",
    p: 0,
    cursor: "pointer",
    color: active ? "#fff" : "rgba(255,255,255,0.55)",
    "&:hover": { color: "#fff" },
  }) as const;

export interface HeaderFilterMenuProps {
  /** Column label rendered before the filter button. */
  label: string;
  /** Distinct values of the column (pre-sorted by the caller). */
  values: string[];
  /** Currently ticked values — empty = no filter (all rows shown). */
  selected: string[];
  /** This column's row-order direction (the ordering key's real dir;
   *  other columns display the default "desc" they'd apply on click). */
  sortDir: "asc" | "desc";
  /** Optional per-item content override — receives the tick value and
   *  returns the item's label node (a colored dot + name, ...). Default
   *  renders the raw value string. */
  renderItem?: (v: string) => ReactNode;
  /** Order-row click — makes this column the table's ordering key and
   *  flips its direction asc ⇄ desc (shared hook state, see
   *  useTableHeaderFilters). */
  onToggleSort: () => void;
  onChange: (next: string[]) => void;
}

export function HeaderFilterMenu({
  label,
  values,
  selected,
  sortDir,
  renderItem,
  onToggleSort,
  onChange,
}: HeaderFilterMenuProps) {
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  // Tick options follow the row-order direction — descending just reverses
  // the caller's ascending-sorted list for display.
  const desc = sortDir === "desc";
  const shown = desc ? [...values].reverse() : values;
  // "All" state: nothing ticked (= everything shown) or every value ticked.
  const allShown = selected.length === 0 || selected.length >= values.length;
  // A proper-subset selection is an active filter.
  const active = !allShown;

  /** Toggle one item. When everything is shown the base is the FULL value
   *  set (so the first untick leaves all-but-one); ticking the last missing
   *  item normalizes back to the empty selection (canonical no-filter). */
  const toggle = (v: string) => {
    const base = allShown ? values : selected;
    const next = base.includes(v) ? base.filter((x) => x !== v) : [...base, v];
    onChange(next.length >= values.length ? [] : next);
  };

  return (
    <Box sx={{ display: "inline-flex", alignItems: "center", gap: 0.1 }}>
      <Typography component="span" variant="inherit">
        {label}
      </Typography>
      <Badge
        color="secondary"
        variant="dot"
        invisible={!active}
        overlap="circular"
        anchorOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <Box component="button" onClick={(e) => setAnchorEl(e.currentTarget)} title={`Filter by ${label}`} sx={headerFilterButtonSx(active)}>
          <FilterListIcon sx={{ fontSize: "0.85rem" }} />
        </Box>
      </Badge>
      <Popover
        open={anchorEl != null}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        slotProps={{ paper: { sx: { maxHeight: 260 } } }}
      >
        <MenuList dense disablePadding>
          {/* Order row — makes this column the table's ordering key and
              flips Ascending ⇄ Descending (tick options follow it too). */}
          <MenuItem
            dense
            title={`Row order — click for ${desc ? "ascending" : "descending"}`}
            onClick={onToggleSort}
            sx={{ fontSize: "0.66rem", py: 0.2, color: "text.secondary" }}
          >
            {desc ? (
              <ArrowDownwardIcon sx={{ fontSize: "0.9rem", p: 0.25, mr: 0.5 }} />
            ) : (
              <ArrowUpwardIcon sx={{ fontSize: "0.9rem", p: 0.25, mr: 0.5 }} />
            )}
            <ListItemText
              primary={desc ? "Descending" : "Ascending"}
              primaryTypographyProps={{ fontSize: "0.66rem" }}
            />
          </MenuItem>
          {/* (All) master checkbox — checked ⇔ everything shown (item rows
              then all render ticked to match), indeterminate while a
              proper-subset filter is active; click always resets to the
              empty selection (no filter). */}
          <MenuItem
            dense
            onClick={() => onChange([])}
            sx={{ fontSize: "0.66rem", py: 0.2 }}
          >
            <Checkbox
              size="small"
              sx={{ p: 0.25, mr: 0.5 }}
              checked={allShown}
              indeterminate={!allShown && selected.length > 0}
            />
            <ListItemText primary="(All)" primaryTypographyProps={{ fontSize: "0.66rem" }} />
          </MenuItem>
          {shown.map((v) => (
            <MenuItem
              key={v}
              dense
              onClick={() => toggle(v)}
              sx={{ fontSize: "0.66rem", py: 0.2 }}
            >
              <Checkbox
                size="small"
                sx={{ p: 0.25, mr: 0.5 }}
                checked={allShown || selected.includes(v)}
              />
              <ListItemText
                primary={renderItem ? renderItem(v) : v}
                primaryTypographyProps={{ fontSize: "0.66rem" }}
              />
            </MenuItem>
          ))}
        </MenuList>
      </Popover>
    </Box>
  );
}

/** Shared asc/desc row-order toggle for the box-style header filter popups
 *  (date / numeric range) — the sibling of the ticks menu's order MenuItem.
 *  Shows the column's current direction; clicking flips it (and makes the
 *  column the table's ordering key — state lives in useTableHeaderFilters).
 *  Styled to match the popups' tiny caption controls. */
export function HeaderSortToggle({
  sortDir,
  onToggle,
}: {
  sortDir: "asc" | "desc";
  onToggle: () => void;
}) {
  const asc = sortDir === "asc";
  return (
    <Button
      size="small"
      onClick={onToggle}
      title={`Row order — click for ${asc ? "descending" : "ascending"}`}
      sx={{
        minWidth: 0,
        py: 0.1,
        px: 0.5,
        fontSize: "0.62rem",
        textTransform: "none",
        lineHeight: 1.4,
        color: "text.secondary",
      }}
    >
      {asc ? (
        <ArrowUpwardIcon sx={{ fontSize: "0.8rem", mr: 0.25 }} />
      ) : (
        <ArrowDownwardIcon sx={{ fontSize: "0.8rem", mr: 0.25 }} />
      )}
      {asc ? "Ascending" : "Descending"}
    </Button>
  );
}

export default HeaderFilterMenu;
