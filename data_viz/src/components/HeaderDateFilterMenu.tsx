/**
 * HeaderDateFilterMenu — column-header date-RANGE filter (one of the three
 * shared header-filter types, alongside HeaderFilterMenu's ticks and
 * HeaderNumericRangeFilterMenu's numeric range): the column label followed
 * by a tiny filter button that opens a compact range popover — a caption
 * header with a Clear action, and From/To date inputs (calendar adornment,
 * direction arrow between) whose outlines highlight primary while the
 * corresponding bound is actively set. Rows whose value (a
 * lexicographically comparable date string — "2026-01" for months,
 * "2026-01-15" for dates) falls inside [from, to] inclusive match; both
 * bounds empty = no filter (all rows shown). The inputs are PREFILLED with
 * the data's earliest/latest value (prefillFrom/prefillTo) while the
 * corresponding bound is unset, so the range never looks empty; clearing an
 * input returns to the prefill display and unbounds that side. The filter
 * button is highlighted only while a bound is actually set.
 */
import { useState } from "react";
import ArrowRightAltIcon from "@mui/icons-material/ArrowRightAlt";
import CalendarMonthIcon from "@mui/icons-material/CalendarMonth";
import FilterListIcon from "@mui/icons-material/FilterList";
import {
  Badge,
  Box,
  Button,
  Popover,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { headerFilterButtonSx } from "@/components/HeaderFilterMenu";

export interface HeaderDateFilterMenuProps {
  /** Column label rendered before the filter button (also the popover title). */
  label: string;
  /** Active inclusive lower/upper bound — null = unbounded on that side. */
  from: string | null;
  to: string | null;
  /** Prefill shown while the bound is unset — the data's earliest/latest
   *  value. Shown in the input, but not treated as an active bound. */
  prefillFrom?: string | null;
  prefillTo?: string | null;
  /** Input granularity — "month" renders month inputs (values YYYY-MM),
   *  "date" (default) renders date inputs (values YYYY-MM-DD). */
  granularity?: "date" | "month";
  onChange: (next: { from: string | null; to: string | null }) => void;
}

export function HeaderDateFilterMenu({
  label,
  from,
  to,
  prefillFrom = null,
  prefillTo = null,
  granularity = "date",
  onChange,
}: HeaderDateFilterMenuProps) {
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const active = from != null || to != null;

  /** One bound input — calendar adornment, From/To label, primary outline
   *  while that bound is actively set (vs. showing the prefill). */
  const boundInput = (
    side: "from" | "to",
  ) => {
    const bound = side === "from" ? from : to;
    const prefill = side === "from" ? prefillFrom : prefillTo;
    return (
      <TextField
        type={granularity}
        size="small"
        label={side === "from" ? "From" : "To"}
        value={bound ?? prefill ?? ""}
        onChange={(e) =>
          onChange(
            side === "from"
              ? { from: e.target.value || null, to }
              : { from, to: e.target.value || null },
          )
        }
        InputProps={{
          sx: { fontSize: "0.7rem" },
          startAdornment: (
            <CalendarMonthIcon
              sx={{ fontSize: "0.8rem", mr: 0.4, color: "action.active" }}
            />
          ),
        }}
        InputLabelProps={{ sx: { fontSize: "0.62rem" } }}
        sx={{
          width: 132,
          "& .MuiOutlinedInput-root": { borderRadius: 1 },
          ...(bound != null && {
            "& .MuiOutlinedInput-notchedOutline": { borderColor: "primary.main" },
          }),
        }}
      />
    );
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
        slotProps={{ paper: { sx: { borderRadius: 1.5, mt: 0.5 } } }}
      >
        <Box sx={{ p: 1.25 }}>
          {/* Popover header — caption title + Clear (enabled while active;
              clearing unbounds both sides, inputs fall back to prefill). */}
          <Stack
            direction="row"
            alignItems="center"
            justifyContent="space-between"
            sx={{ mb: 0.75, gap: 1 }}
          >
            <Typography
              sx={{
                fontSize: "0.6rem",
                fontWeight: 600,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                color: "text.secondary",
              }}
            >
              {label} · range
            </Typography>
            <Button
              size="small"
              disabled={!active}
              onClick={() => onChange({ from: null, to: null })}
              sx={{
                minWidth: 0,
                py: 0.1,
                px: 0.75,
                fontSize: "0.62rem",
                textTransform: "none",
                lineHeight: 1.4,
              }}
            >
              Clear
            </Button>
          </Stack>
          <Stack direction="row" spacing={0.5} alignItems="center">
            {boundInput("from")}
            <ArrowRightAltIcon sx={{ fontSize: "0.95rem", color: "text.disabled" }} />
            {boundInput("to")}
          </Stack>
        </Box>
      </Popover>
    </Box>
  );
}

export default HeaderDateFilterMenu;
