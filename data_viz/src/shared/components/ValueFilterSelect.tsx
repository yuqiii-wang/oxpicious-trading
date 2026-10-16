/**
 * ValueFilterSelect — toolbar dropdown for the config / monitor tables:
 * filter rows by a column's closed-vocabulary value (e.g. the service
 * freq). The options are passed in (distinct values over the loaded rows,
 * sorted, each with its row count — the caller derives them since it owns
 * the rows); "(all)" is the no-filter state. Mirrors DirPathFilterSelect's
 * closed-state caption style.
 */
import { Box, MenuItem, TextField, Typography } from "@mui/material";

export interface ValueFilterOption {
  value: string;
  count: number;
}

interface ValueFilterSelectProps {
  /** Field label shown on the closed select. */
  label: string;
  options: ValueFilterOption[];
  /** Selected value — "" = all. */
  value: string;
  onChange: (next: string) => void;
  /** Fixed pixel width of the select (default 150 — long values like
   *  file names need more). */
  width?: number;
}

export default function ValueFilterSelect({
  label,
  options,
  value,
  onChange,
  width = 150,
}: ValueFilterSelectProps) {
  return (
    <TextField
      select
      size="small"
      label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      // Closed state shows the value only — the row count lives in the
      // open menu's rows.
      SelectProps={{
        renderValue: (v: string) =>
          v === "" ? (
            <Typography variant="caption" color="text.secondary">
              (all)
            </Typography>
          ) : (
            <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
              {v}
            </Typography>
          ),
      }}
      sx={{ width }}
    >
      <MenuItem value="">
        <Typography variant="caption" color="text.secondary">
          (all)
        </Typography>
      </MenuItem>
      {options.map((o) => (
        <MenuItem key={o.value} value={o.value}>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, width: "100%" }}>
            <Typography variant="caption" sx={{ fontFamily: "monospace" }}>
              {o.value}
            </Typography>
            <Box sx={{ flexGrow: 1 }} />
            <Typography variant="caption" color="text.secondary">
              {o.count}
            </Typography>
          </Box>
        </MenuItem>
      ))}
    </TextField>
  );
}
