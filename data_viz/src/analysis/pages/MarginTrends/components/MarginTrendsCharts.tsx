/**
 * MarginTrendsCharts — composition root of the single-industry margin view.
 *
 * Pure wiring: data comes from hooks/useMarginData, derivation from
 * chart/derive, options from chart/trendChartOption + hypedDrainedOption,
 * controls from MarginTrendControls. Renders:
 *   • controls row (Balance | Buy toggle + security multi-select)
 *   • main composite chart (margin lines top; intensity-coded daily return
 *     bars + 20d vol line bottom)
 *   • one HypedVsDrainedChart per selected security (capped at 3)
 */
import { useMemo } from "react";
import { Box, Typography } from "@mui/material";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { MarginSecurity } from "@shared/types";
import { useMarginData } from "../hooks/useMarginData";
import { pivotSeriesData, deriveAnalytics } from "../chart/derive";
import { buildTrendChartOption } from "../chart/trendChartOption";
import type { MarginAttribution } from "../constants";
import { SERIES_OPTIONS } from "../constants";
import { MarginTrendControls } from "./MarginTrendControls";
import { HypedVsDrainedChart } from "./HypedVsDrainedChart";

/** Max companion regime charts rendered (one per selected security). */
const MAX_REGIME_CHARTS = 3;

export interface MarginTrendsChartsProps {
  industryId: string;
  attribution: MarginAttribution;
  /** When set, show only this single security (single-item mode). */
  selectedItemCode?: string | null;
}

export function MarginTrendsCharts({
  industryId,
  attribution,
  selectedItemCode,
}: MarginTrendsChartsProps) {
  const themeMode = useChartThemeMode();
  const isSingleItemMode = !!selectedItemCode;

  const {
    seriesData,
    loadingSeries,
    errorSeries,
    series,
    setSeries,
    selectedCodes,
    setSelectedCodes,
  } = useMarginData(industryId, attribution, selectedItemCode);

  const displaySecurities = useMemo<MarginSecurity[]>(() => {
    if (!seriesData) return [];
    if (isSingleItemMode && selectedItemCode) {
      return seriesData.securities.filter((s) => s.code === selectedItemCode);
    }
    return seriesData.securities;
  }, [seriesData, isSingleItemMode, selectedItemCode]);

  // ---- Derivation: pivots (margin lines) + analytics (regimes) ----------
  const targetCodes = useMemo(
    () => (isSingleItemMode && selectedItemCode ? new Set([selectedItemCode]) : null),
    [isSingleItemMode, selectedItemCode],
  );
  const seriesPivot = useMemo(
    () => (seriesData && seriesData.rows.length > 0
      ? pivotSeriesData(seriesData, series, targetCodes)
      : null),
    [seriesData, series, targetCodes],
  );
  const analytics = useMemo(
    () => (seriesData ? deriveAnalytics(seriesData) : null),
    [seriesData],
  );

  // ---- Main composite option --------------------------------------------
  const trendsOption = useMemo(
    () => (seriesData && seriesPivot && analytics
      ? buildTrendChartOption({
          seriesData,
          pivot: seriesPivot,
          analytics,
          displaySecurities,
          selectedCodes,
          attribution,
          series,
          themeMode,
          isSingleItemMode,
          selectedItemCode,
        })
      : null),
    [
      seriesData, seriesPivot, analytics, displaySecurities, selectedCodes,
      attribution, series, themeMode, isSingleItemMode, selectedItemCode,
    ],
  );

  const selectedSecs = useMemo<MarginSecurity[]>(() => {
    if (!seriesData) return [];
    return selectedCodes
      .map((code) => seriesData.securities.find((s) => s.code === code))
      .filter((s): s is MarginSecurity => s != null);
  }, [selectedCodes, seriesData]);

  const regimeCodes = useMemo(() => selectedCodes.slice(0, MAX_REGIME_CHARTS), [selectedCodes]);

  return (
    <Box>
      <MarginTrendControls
        series={series}
        onSeriesChange={setSeries}
        seriesOptions={SERIES_OPTIONS}
        securities={displaySecurities}
        selectedSecurities={selectedSecs}
        onSelectedChange={(secs) => setSelectedCodes(secs.map((s) => s.code))}
        showAutocomplete={!isSingleItemMode && !!seriesData && seriesData.rows.length > 0}
      />

      {/* ---- Main plot: margin trends + amplification view ----
          Loading / error / empty placeholders and the EChart are owned by the
          shared BaseChart (bare variant); the section title rides in children. */}
      <BaseChart
        variant="bare"
        option={trendsOption}
        height={560}
        loading={loadingSeries}
        error={errorSeries ? `Failed to load series: ${errorSeries}` : null}
        emptyText={
          seriesData
            ? `No margin data for industry "${industryId}" under ${attribution} attribution. Run the Python build script (analysis.margins) to populate.`
            : "No data"
        }
      >
        {!loadingSeries && seriesData && seriesData.rows.length > 0 && (
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
              {seriesData.industry_label} — {attribution === "index" ? "Index" : "ETF"}{" "}
              RONGZI {series === "balance" ? "RZ Balance (rongzi outstanding)" : "RZ Buy (rongzi daily buy flow)"}{" "}
              + Daily Returns
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
            <Typography variant="caption" color="text.secondary">
              {`Bar shade = margin intensity (RZ-buy share of turnover tercile:
              faint LOW → saturated HIGH); dashed line = trailing 20d return
              vol.`}
            </Typography>
          </Box>
        )}
      </BaseChart>

      {/* ---- Companion: hyped-vs-drained regime split per selected code ---- */}
      {!loadingSeries && !errorSeries && analytics && regimeCodes.length > 0 && (
        <Box sx={{ mt: 1.5, display: "flex", flexDirection: "column", gap: 1.5 }}>
          {regimeCodes.map((code) => {
            const sec = seriesData?.securities.find((s) => s.code === code);
            return (
              <HypedVsDrainedChart
                key={code}
                code={code}
                label={sec?.label || code}
                derived={analytics.byCode.get(code)}
              />
            );
          })}
        </Box>
      )}
    </Box>
  );
}
