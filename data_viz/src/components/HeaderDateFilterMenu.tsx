/**
 * HeaderDateFilterMenu — column-header date-RANGE filter (one of the three
 * shared header-filter types, alongside HeaderFilterMenu's ticks and
 * HeaderNumericRangeFilterMenu's numeric range): the column label followed
 * by a tiny filter button that opens a compact range popover — a caption
 * header with a row-order toggle (Ascending ⇄ Descending; makes this
 * column the table's ordering key, shared via useTableHeaderFilters) and a
 * Clear action, and From/To date inputs (calendar adornment, direction
 * arrow between) whose outlines highlight primary while the corresponding
 * bound is actively set. Rows whose value (a
 * lexicographically comparable date string — "2026-01" for months,
 * "2026-01-15" for dates) falls inside [from, to] inclusive match; both
 * bounds empty = no filter (all rows shown). The inputs are PREFILLED with
 * the data's earliest/latest value (prefillFrom/prefillTo) while the
 * corresponding bound is unset, so the range never looks empty; clearing an
 * input returns to the prefill display and unbounds that side. The filter
 * button is highlighted only while a bound is actually set.
 *
 * END-ONLY mode (frozenFromYears set) — a SINGLE editable end-period
 * input that selects rows whose stats period EQUALS it (emitted as the
 * inclusive [end, end] range, so exactly one month's rows show). The
 * lower bound is auto-FROZEN at (end − frozenFromYears years — the
 * stats' trailing lookback window, e.g. the forecast buckets' 10y
 * window) and reported as a muted caption only — the recorded stats
 * window behind the selected month, never a second editable input or
 * a filter bound. Clearing the end input unbounds both sides.
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
import { HeaderSortToggle, headerFilterButtonSx } from "@/components/HeaderFilterMenu";

/** "YYYY-MM[-DD]" minus N years — pure string math on the comparable
 *  date-string form (no Date/timezone round-trip); the frozen-min
 *  derivation of the END-ONLY mode. */
function minusYears(s: string, years: number): string {
  return `${Number(s.slice(0, 4)) - years}${s.slice(4)}`;
}

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
  /** END-ONLY mode: show ONE editable end-period input and auto-freeze
   *  the lower bound at (end − N years) — emitted as the from bound on
   *  every edit, never displayed as a second input. */
  frozenFromYears?: number;
  /** This column's row-order direction (the ordering key's real dir;
   *  other columns display the default "desc" they'd apply on click). */
  sortDir: "asc" | "desc";
  /** Order-toggle click — makes this column the table's ordering key and
   *  flips its direction asc ⇄ desc (shared hook state). */
  onToggleSort: () => void;
  onChange: (next: { from: string | null; to: string | null }) => void;
}

export function HeaderDateFilterMenu({
  label,
  from,
  to,
  prefillFrom = null,
  prefillTo = null,
  granularity = "date",
  frozenFromYears,
  sortDir,
  onToggleSort,
  onChange,
}: HeaderDateFilterMenuProps) {
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null);
  const endOnly = frozenFromYears != null;
  const active = from != null || to != null;

  /** One bound input — calendar adornment, side label, primary outline
   *  while that bound is actively set (vs. showing the prefill); the
   *  edit routing (which bounds the new value sets) is the caller's. */
  const boundInput = (
    side: "from" | "to",
    label: string,
    onEdit: (v: string | null) => void,
  ) => {
    const bound = side === "from" ? from : to;
    const prefill = side === "from" ? prefillFrom : prefillTo;
    return (
      <TextField
        type={granularity}
        size="small"
        label={label}
        value={bound ?? prefill ?? ""}
        onChange={(e) => onEdit(e.target.value || null)}
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
          title={`Filter ${label} ${endOnly ? "by end period" : "by range"}`}
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
          {/* Popover header — caption title + row-order toggle (Ascending ⇄
              Descending) + Clear (enabled while active; clearing unbounds
              both sides, inputs fall back to prefill). */}
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
              {label} · {endOnly ? "end period" : "range"}
            </Typography>
            <Stack direction="row" alignItems="center" spacing={0.25}>
              <HeaderSortToggle sortDir={sortDir} onToggle={onToggleSort} />
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
          </Stack>
          {endOnly ? (
            <Stack spacing={0.4}>
              {/* The ONLY editable date — the end period. Rows match the
                  stats period EQUALING it (the emitted [end, end] range);
                  the derived min (end − lookback) is the stats window
                  start, caption-only — never a filter bound. */}
              {boundInput("to", "end", (v) =>
                onChange({ from: v, to: v }),
              )}
              <Typography sx={{ fontSize: "0.6rem", lineHeight: 1.3, color: "text.disabled" }}>
                {to != null
                  ? `stats window ${minusYears(to, frozenFromYears!)} → ${to} · min frozen (${frozenFromYears}y lookback)`
                  : `auto min = end − ${frozenFromYears}y (frozen)`}
              </Typography>
            </Stack>
          ) : (
            <Stack direction="row" spacing={0.5} alignItems="center">
              {boundInput("from", "From", (v) => onChange({ from: v, to }))}
              <ArrowRightAltIcon sx={{ fontSize: "0.95rem", color: "text.disabled" }} />
              {boundInput("to", "To", (v) => onChange({ from, to: v }))}
            </Stack>
          )}
        </Box>
      </Popover>
    </Box>
  );
}

export default HeaderDateFilterMenu;
