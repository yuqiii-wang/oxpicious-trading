/**
 * MarginTrendControls — the control row of the Margin Trends chart:
 * the Balance | Buy series toggle + the security multi-select Autocomplete
 * (highlight selector). Extracted from the former MarginTrendsCharts so the
 * composition component only wires data → options.
 */
import {
  Autocomplete,
  Box,
  Chip,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import type { MarginSecurity } from "@shared/types";
import type { MarginSeries } from "../constants";

export interface MarginTrendControlsProps {
  series: MarginSeries;
  onSeriesChange: (s: MarginSeries) => void;
  seriesOptions: MarginSeries[];
  securities: MarginSecurity[];
  selectedSecurities: MarginSecurity[];
  onSelectedChange: (secs: MarginSecurity[]) => void;
  /** Hide the Autocomplete (single-item mode shows exactly one security). */
  showAutocomplete: boolean;
}

export function MarginTrendControls({
  series,
  onSeriesChange,
  seriesOptions,
  securities,
  selectedSecurities,
  onSelectedChange,
  showAutocomplete,
}: MarginTrendControlsProps) {
  const selectedCodes = selectedSecurities.map((s) => s.code);
  return (
    <Box
      sx={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        gap: 2,
        flexWrap: "wrap",
        mb: 1,
      }}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
        <ToggleButtonGroup
          value={series}
          exclusive
          size="small"
          onChange={(_, v: MarginSeries | null) => v && onSeriesChange(v)}
        >
          {seriesOptions.map((s) => (
            <ToggleButton key={s} value={s}>
              {s === "balance" ? "Balance" : "Buy"}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          Highlight:
        </Typography>
      </Box>

      {showAutocomplete && securities.length > 0 && (
        <Autocomplete
          multiple
          size="small"
          sx={{ minWidth: 320, maxWidth: 480, flexGrow: 1 }}
          options={securities}
          value={selectedSecurities}
          getOptionLabel={(opt) => `${opt.label} (${opt.code})`}
          isOptionEqualToValue={(opt, val) => opt.code === val.code}
          onChange={(_, val) => onSelectedChange(val)}
          renderTags={(value, getTagProps) =>
            value.map((opt, idx) => {
              const { key: tagKey, ...tagProps } = getTagProps({ index: idx });
              return (
                <Chip
                  key={tagKey}
                  {...tagProps}
                  label={opt.label || opt.code}
                  size="small"
                  color={value.length >= 2 ? "primary" : "default"}
                />
              );
            })
          }
          renderInput={(params) => (
            <TextField
              {...params}
              placeholder={
                selectedCodes.length === 0
                  ? "Select securities to highlight"
                  : "Add or remove securities"
              }
            />
          )}
        />
      )}
    </Box>
  );
}
