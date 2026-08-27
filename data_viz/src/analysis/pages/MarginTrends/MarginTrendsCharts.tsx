/**
 * MarginTrendsCharts — the single-plot single-industry margin trends view.
 *
 * Layout:
 *   Margin trends — one line per security (indices or ETFs) in the
 *   industry. Toggle Balance | Buy. Selected securities are highlighted;
 *   the rest render as muted background lines. Trend-episode shades
 *   (markArea) overlay selected series; in Buy mode each episode also
 *   draws its rz_buy_vs_trading_amt_ratio as dashed segments on a
 *   dedicated right-side % axis. A synced close-price grid sits below.
 *
 * Controls:
 *   • Attribution toggle: Index | ETF (reloads series)
 *   • Series toggle: Balance | Buy (plot value)
 *   • Security multi-select (Autocomplete): highlight securities
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
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import EChart from "@/components/EChart";
import type { EChartsOption } from "echarts";
import type { MarginSecurity } from "@shared/types";
import { useMarginData } from "./useMarginData";
import { buildTrendChartOption, pivotSeriesData } from "./chartOptions";
import type { MarginTrendsChartsProps } from "./types";
import { SERIES_OPTIONS } from "./constants";
import type { MarginSeries } from "./constants";

export function MarginTrendsCharts({ industryId, themeMode, attribution, selectedItemCode }: MarginTrendsChartsProps) {
  const isSingleItemMode = !!selectedItemCode;

  const {
    seriesData,
    trendsData,
    loadingSeries,
    errorSeries,
    series,
    setSeries,
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
                const { key: tagKey, ...tagProps } = getTagProps({ index: idx });
                return (
                  <Chip
                    key={tagKey}
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
                    ? "Select securities to highlight"
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

      {/* ---- Main plot: margin trends ---- */}
      {loadingSeries && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <CircularProgress size={28} />
        </Box>
      )}
      {!loadingSeries && seriesData && seriesData.rows.length > 0 && (
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
          {trendsOption && <EChart option={trendsOption} height={560} />}
        </Box>
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
