/**
 * HeaderNumericRangeFilterMenu — column-header NUMERIC range filter (one of
 * the three shared header-filter types, alongside HeaderFilterMenu's ticks
 * and HeaderDateFilterMenu's date range): the column label followed by a
 * tiny filter button that opens min/max number inputs. Rows whose numeric
 * value falls inside [min, max] inclusive match; both bounds empty = no
 * filter (all rows shown); rows with a non-numeric/null value never match
 * an active range. The inputs are PREFILLED with the data's numeric min/max
 * (prefillMin/prefillMax) while the corresponding bound is unset, so the
 * range never looks empty; clearing an input returns to the prefill display
 * and unbounds that side. The filter button is highlighted only while a
 * bound is actually set.
 *
 * NOT for every numeric column — columns whose values form a small discrete
 * set (1% / 5% / window sizes / cooldowns) should use ticks instead; ranges
 * suit continuous magnitudes (prices, amounts, ratios).
 */
import { useState } from "react";
import FilterListIcon from "@mui/icons-material/FilterList";
import {
  Badge,
  Box,
  Popover,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { headerFilterButtonSx } from "@/components/HeaderFilterMenu";

export interface HeaderNumericRangeFilterMenuProps {
  /** Column label rendered before the filter button. */
  label: string;
  /** Active inclusive lower/upper bound — null = unbounded on that side. */
  min: number | null;
  max: number | null;
  /** Prefill shown while the bound is unset — the data's numeric min/max.
   *  Shown in the input, but not treated as an active bound. */
  prefillMin?: number | null;
  prefillMax?: number | null;
  onChange: (next: { min: number | null; max: number | null }) => void;
}

/** "" | "-3" | "12.5" — keep the raw text while typing, parse on change. */
function parseBound(raw: string): number | null {
  if (raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

export function HeaderNumericRangeFilterMenu({
  label,
  min,
  max,
  prefillMin = null,
  prefillMax = null,
  onChange,
}: HeaderNumericRangeFilterMenuProps) {
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const active = min != null || max != null;

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
        <Box
          component="button"
          onClick={(e) => setAnchorEl(e.currentTarget)}
          title={`Filter ${label} by range`}
          sx={headerFilterButtonSx(active)}
        >
          <FilterListIcon sx={{ fontSize: "0.85rem" }} />
        </Box>
      </Badge>
      <Popover
        open={anchorEl != null}
        anchorEl={anchorEl}
        onClose={() => setAnchorEl(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
      >
        <Stack direction="row" spacing={1} alignItems="center" sx={{ p: 1 }}>
          <Typography component="span" sx={{ fontSize: "0.66rem", color: "text.secondary" }}>
            min
          </Typography>
          <TextField
            type="number"
            size="small"
            value={min ?? prefillMin ?? ""}
            placeholder="min"
            onChange={(e) => onChange({ min: parseBound(e.target.value), max })}
            sx={{ width: 110 }}
            InputProps={{ sx: { fontSize: "0.7rem", py: 0.1 } }}
          />
          <Typography component="span" sx={{ fontSize: "0.66rem", color: "text.secondary" }}>
            max
          </Typography>
          <TextField
            type="number"
            size="small"
            value={max ?? prefillMax ?? ""}
            placeholder="max"
            onChange={(e) => onChange({ min, max: parseBound(e.target.value) })}
            sx={{ width: 110 }}
            InputProps={{ sx: { fontSize: "0.7rem", py: 0.1 } }}
          />
        </Stack>
      </Popover>
    </Box>
  );
}

export default HeaderNumericRangeFilterMenu;
