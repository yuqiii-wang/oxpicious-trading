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
 * The trigger button + items are styled for a primary.main header cell
 * (white icon/badge) — all tables using these menus have blue headers.
 */
import { useState } from "react";
import FilterListIcon from "@mui/icons-material/FilterList";
import {
  Badge,
  Box,
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
  onChange: (next: string[]) => void;
}

export function HeaderFilterMenu({ label, values, selected, onChange }: HeaderFilterMenuProps) {
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
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
          {values.map((v) => (
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
              <ListItemText primary={v} primaryTypographyProps={{ fontSize: "0.66rem" }} />
            </MenuItem>
          ))}
        </MenuList>
      </Popover>
    </Box>
  );
}

export default HeaderFilterMenu;
