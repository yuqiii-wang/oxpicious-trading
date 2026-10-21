/**
 * MarginTrendControls — the control row of the Margin Trends chart:
 * the Balance | Buy series toggle, the (industry attribution) Raw |
 * Normalized value-mode toggle, the (exchange attribution) mapped
 * broad-market underlying Autocomplete, then the security multi-select
 * Autocomplete (highlight selector). Extracted from the former
 * MarginTrendsCharts so the composition component only wires data → options.
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
import type { MarginSecurity, MarginUnderlyingEntry } from "@shared/types";
import type { MarginSeries, MarginValueMode } from "../constants";

export interface MarginTrendControlsProps {
  series: MarginSeries;
  onSeriesChange: (s: MarginSeries) => void;
  seriesOptions: MarginSeries[];
  /** Raw | Normalized value-mode toggle (industry attribution only — the
   *  only one backed by analysis.margin_industry_stats); renders when
   *  all three props are given. */
  valueMode?: MarginValueMode;
  onValueModeChange?: (m: MarginValueMode) => void;
  securities: MarginSecurity[];
  selectedSecurities: MarginSecurity[];
  onSelectedChange: (secs: MarginSecurity[]) => void;
  /** Hide the Autocomplete (single-item mode shows exactly one security). */
  showAutocomplete: boolean;
  /** Broad-market underlying options + active code + change handler; the
   *  dropdown renders only when all three are given (exchange attribution).
   *  The underlying feeds the daily-return / hyped-vs-drained views. */
  underlyingOptions?: MarginUnderlyingEntry[];
  underlyingCode?: string | null;
  onUnderlyingChange?: (code: string) => void;
}

export function MarginTrendControls({
  series,
  onSeriesChange,
  seriesOptions,
  valueMode,
  onValueModeChange,
  securities,
  selectedSecurities,
  onSelectedChange,
  showAutocomplete,
  underlyingOptions,
  underlyingCode,
  onUnderlyingChange,
}: MarginTrendControlsProps) {
  const selectedCodes = selectedSecurities.map((s) => s.code);
  const showValueMode =
    valueMode != null && onValueModeChange != null;
  const showUnderlying =
    underlyingOptions != null && underlyingCode != null && onUnderlyingChange != null;
  const selectedUnderlying =
    underlyingOptions?.find((o) => o.code === underlyingCode) ?? null;
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
        {showValueMode && (
          <ToggleButtonGroup
            value={valueMode}
            exclusive
            size="small"
            onChange={(_, v: MarginValueMode | null) => v && onValueModeChange!(v)}
          >
            <ToggleButton value="raw" title="member-total curve: SUM(stock+etf)">
              Raw
            </ToggleButton>
            <ToggleButton
              value="normalized"
              title="per-member average: SUM of the per-side AVG over non-null non-zero members"
            >
              Normalized
            </ToggleButton>
          </ToggleButtonGroup>
        )}
        {showUnderlying && (
          <>
            <Typography
              variant="body2"
              sx={{ fontWeight: 600 }}
              title="broad-market proxy whose close/turnover/regime the daily-return and hyped-vs-drained views map onto"
            >
              Underlying:
            </Typography>
            <Autocomplete
              size="small"
              sx={{ minWidth: 220, maxWidth: 320 }}
              options={underlyingOptions ?? []}
              value={selectedUnderlying}
              getOptionLabel={(opt) => `${opt.name} (${opt.code})`}
              isOptionEqualToValue={(opt, val) => opt.code === val.code}
              onChange={(_, val) => val && onUnderlyingChange!(val.code)}
              renderInput={(params) => (
                <TextField {...params} placeholder="Broad-market proxy index" />
              )}
            />
          </>
        )}
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
