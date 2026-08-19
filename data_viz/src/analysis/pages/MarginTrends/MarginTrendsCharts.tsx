/**
 * MarginTrendsCharts — the 2-plot single-industry margin trends view.
 *
 * Layout (top → bottom):
 *   1. Margin trends — one line per security (indices or ETFs) in the
 *      industry. Toggle Balance | Buy. Selected securities (for the 2nd
 *      plot) are highlighted; the rest render as muted background lines.
 *   2. Pairwise correlation — one line per selected security pair, read
 *      from analysis.margin_industry_correlation (precomputed). Window
 *      toggle 5/20/60/120/255d. Requires ≥2 securities selected.
 *
 * Controls:
 *   • Attribution toggle: Index | ETF (drives both plots; reloads series)
 *   • Series toggle: Balance | Buy (1st plot value + 2nd plot corr column)
 *   • Window toggle: 5d | 20d | 60d | 120d | 255d — sits beside the 2nd plot
 *   • Security multi-select (Autocomplete): pick ≥2 securities for 2nd plot
 *
 * Refactored: data logic → useMarginData hook, ECharts option builders → chartOptions.
 */
import React, { useMemo } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  Chip,
  CircularProgress,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import EChart from "@/components/EChart";
import type { EChartsOption } from "echarts";
import type { MarginSecurity } from "@shared/types";
import { useMarginData } from "./useMarginData";
import { buildTrendChartOption, buildCorrChartOption, pivotSeriesData } from "./chartOptions";
import type { MarginTrendsChartsProps } from "./types";
import { CORR_WINDOWS, SERIES_OPTIONS } from "./constants";
import type { MarginSeries, CorrWindow } from "./constants";

export function MarginTrendsCharts({ industryId, themeMode, attribution, selectedItemCode }: MarginTrendsChartsProps) {
  const isSingleItemMode = !!selectedItemCode;

  const {
    seriesData,
    corrData,
    trendsData,
    loadingSeries,
    loadingCorr,
    errorSeries,
    errorCorr,
    series,
    setSeries,
    corrWindow,
    setCorrWindow,
    selectedCodes,
    setSelectedCodes,
  } = useMarginData(industryId, attribution, selectedItemCode);

  // ---- Derived data ----
  const displaySecurities = useMemo(() => {
    if (!seriesData) return [];
    if (isSingleItemMode && selectedItemCode) {
      return seriesData.securities.filter((s) => s.code === selectedItemCode);
    }
    return seriesData.securities;
  }, [seriesData, isSingleItemMode, selectedItemCode]);

  const seriesPivot = useMemo(() => {
    if (!seriesData || seriesData.rows.length === 0) return null;
    const targetCodes = isSingleItemMode && selectedItemCode
      ? new Set([selectedItemCode])
      : null;
    return pivotSeriesData(seriesData, series, targetCodes);
  }, [seriesData, series, isSingleItemMode, selectedItemCode]);

  // ---- ECharts options ----
  const trendsOption: EChartsOption | null = useMemo(() => {
    if (!seriesData || !seriesPivot) return null;
    return buildTrendChartOption(
      seriesData,
      seriesPivot,
      displaySecurities,
      selectedCodes,
      attribution,
      series,
      themeMode,
      trendsData,
      isSingleItemMode,
      selectedItemCode,
    );
  }, [seriesData, seriesPivot, displaySecurities, selectedCodes, attribution, series, themeMode, trendsData, isSingleItemMode, selectedItemCode]);

  const corrOption: EChartsOption | null = useMemo(() => {
    if (!corrData || corrData.rows.length === 0 || corrData.pairs.length === 0) return null;
    return buildCorrChartOption(corrData, displaySecurities, series, themeMode);
  }, [corrData, displaySecurities, series, themeMode]);

  // ---- Autocomplete value ----
  const selectedSecs: MarginSecurity[] = useMemo(() => {
    if (!seriesData) return [];
    return selectedCodes
      .map((code) => seriesData.securities.find((s) => s.code === code))
      .filter((s): s is MarginSecurity => s != null);
  }, [selectedCodes, seriesData]);

  return (
    <Box>
      {/* ---- Controls ---- */}
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
            onChange={(_, v: MarginSeries | null) => v && setSeries(v)}
          >
            {SERIES_OPTIONS.map((s) => (
              <ToggleButton key={s} value={s}>
                {s === "balance" ? "Balance" : "Buy"}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Box>

        {/* Security multi-select — hidden in single-item mode */}
        {!isSingleItemMode && seriesData && seriesData.securities.length > 0 && (
          <Autocomplete
            multiple
            size="small"
            sx={{ minWidth: 320, maxWidth: 480, flexGrow: 1 }}
            options={seriesData.securities}
            value={selectedSecs}
            getOptionLabel={(opt) => `${opt.label} (${opt.code})`}
            isOptionEqualToValue={(opt, val) => opt.code === val.code}
            onChange={(_, val: MarginSecurity[]) => {
              setSelectedCodes(val.map((s) => s.code));
            }}
            renderTags={(value, getTagProps) =>
              value.map((opt, idx) => {
                const tagProps = getTagProps({ index: idx });
                return (
                  <Chip
                    {...tagProps}
                    label={opt.label || opt.code}
                    size="small"
                    color={selectedCodes.length >= 2 ? "primary" : "default"}
                  />
                );
              })
            }
            renderInput={(params) => (
              <TextField
                {...params}
                placeholder={
                  selectedCodes.length === 0
                    ? "Select ≥2 securities for correlation"
                    : selectedCodes.length < 2
                      ? `Select ${2 - selectedCodes.length} more for correlation`
                      : "Add or remove securities"
                }
              />
            )}
          />
        )}
      </Box>

      {/* ---- Errors ---- */}
      {errorSeries && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          Failed to load series: {errorSeries}
        </Alert>
      )}
      {errorCorr && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          Failed to load correlation: {errorCorr}
        </Alert>
      )}

      {/* ---- 1st plot: margin trends ---- */}
      {loadingSeries && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={28} />
        </Box>
      )}
      {!loadingSeries && seriesData && seriesData.rows.length > 0 && (
        <Stack spacing={1.5}>
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
              {seriesData.industry_label} — {attribution === "index" ? "Index" : "ETF"}{" "}
              RONGZI {series === "balance" ? "Balance (融资余额)" : "Buy (融资买入额)"}{" "}
              + Close Price
              {isSingleItemMode && selectedItemCode && (
                <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                  · {selectedItemCode}
                </Typography>
              )}
              {!isSingleItemMode && selectedCodes.length > 0 && (
                <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                  ({seriesData.securities.length} securities; {selectedCodes.length} selected)
                </Typography>
              )}
            </Typography>
            {trendsOption && <EChart option={trendsOption} height={460} />}
          </Box>

          {/* ---- 2nd plot: pairwise correlation (hidden in single-item mode) ---- */}
          {!isSingleItemMode && (
          <Box>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 0.5, flexWrap: "wrap" }}>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                Pairwise {series === "balance" ? "Balance" : "Buy"} Correlation ({corrWindow}d)
                {corrData && corrData.pairs.length > 0 && (
                  <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                    ({corrData.pairs.length} pairs)
                  </Typography>
                )}
              </Typography>
              <ToggleButtonGroup
                value={corrWindow}
                exclusive
                size="small"
                onChange={(_, v: CorrWindow | null) => v && setCorrWindow(v)}
              >
                {CORR_WINDOWS.map((w) => (
                  <ToggleButton key={w} value={w}>{w}d</ToggleButton>
                ))}
              </ToggleButtonGroup>
            </Box>
            {selectedCodes.length < 2 ? (
              <Tooltip title="Correlation needs at least 2 selected securities.">
                <Alert severity="info" sx={{ py: 0.5 }}>
                  Select at least 2 securities above to see their pairwise correlation.
                </Alert>
              </Tooltip>
            ) : loadingCorr ? (
              <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
                <CircularProgress size={28} />
              </Box>
            ) : corrData && corrData.rows.length === 0 ? (
              <Alert severity="warning" sx={{ py: 0.5 }}>
                No correlation rows for the selected securities under {attribution} attribution.
                Try a different attribution or security set.
              </Alert>
            ) : (
              corrOption && <EChart option={corrOption} height={300} />
            )}
          </Box>
          )}
        </Stack>
      )}

      {!loadingSeries && !errorSeries && seriesData && seriesData.rows.length === 0 && (
        <Alert severity="warning">
          No margin data for industry "{industryId}" under {attribution} attribution.
          Run the Python build script (analyze.margins) to populate.
        </Alert>
      )}
    </Box>
  );
}
