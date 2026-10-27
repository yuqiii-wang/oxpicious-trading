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
import { useMemo, useRef, useState } from "react";
import { Box, Typography } from "@mui/material";
import type { ECharts } from "echarts";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import type { MarginSecurity, MarginUnderlyingEntry } from "@shared/types";
import { INSIGHT_PAGES, useAiAskAddon, type AiAskSpec } from "@/shared/ai-ask";
import { useDocs } from "@/shared/docs";
import { useMarginData } from "../hooks/useMarginData";
import { pivotSeriesData, deriveAnalytics } from "../chart/derive";
import { buildTrendChartOption } from "../chart/trendChartOption";
import type { MarginAttribution } from "../constants";
import { SERIES_OPTIONS } from "../constants";
import type { MarginValueMode } from "../constants";
import { MarginTrendControls } from "./MarginTrendControls";
import { HypedVsDrainedChart } from "./HypedVsDrainedChart";

/** Max companion regime charts rendered (one per selected security). */
const MAX_REGIME_CHARTS = 3;

export interface MarginTrendsChartsProps {
  industryId: string;
  attribution: MarginAttribution;
  /** When set, show only this single security (single-item mode). */
  selectedItemCode?: string | null;
  /** Exchange attribution: the mapped broad-market underlying code for the
   *  daily-return / hyped-vs-drained views (refetches on change). */
  underlyingCode?: string | null;
  /** Options + change handler for the underlying dropdown in the controls
   *  row; render it only when all three underlying props are given. */
  underlyingOptions?: MarginUnderlyingEntry[];
  onUnderlyingChange?: (code: string) => void;
}

export function MarginTrendsCharts({
  industryId,
  attribution,
  selectedItemCode,
  underlyingCode,
  underlyingOptions,
  onUnderlyingChange,
}: MarginTrendsChartsProps) {
  const themeMode = useChartThemeMode();
  const isSingleItemMode = !!selectedItemCode;
  // Raw | Normalized value mode — only rendered (and only effective) for
  // the industry attribution, the only one backed by
  // analysis.margin_industry_stats' *_norm member-average columns.
  const [valueMode, setValueMode] = useState<MarginValueMode>("raw");
  const normalized = valueMode === "normalized" && attribution === "industry";

  const {
    seriesData,
    rawSeriesData,
    loadingSeries,
    errorSeries,
    series,
    setSeries,
    selectedCodes,
    setSelectedCodes,
  } = useMarginData(industryId, attribution, selectedItemCode, underlyingCode, normalized);

  const displaySecurities = useMemo<MarginSecurity[]>(() => {
    if (!seriesData) return [];
    if (isSingleItemMode && selectedItemCode) {
      return seriesData.securities.filter((s) => s.code === selectedItemCode);
    }
    return seriesData.securities;
  }, [seriesData, isSingleItemMode, selectedItemCode]);

  // ---- Derivation: pivots (margin lines) + analytics (regimes) ----------
  // Pivots + member dots come from the DISPLAYED response; the
  // hyped-vs-drained analytics (returns / vol / buy-share terciles) ALWAYS
  // derive from the RAW response, so they stay identical across the
  // Raw | Normalized toggle.
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
  const analyticsSource = rawSeriesData ?? seriesData;
  const analytics = useMemo(
    () => (analyticsSource ? deriveAnalytics(analyticsSource) : null),
    [analyticsSource],
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
          normalized,
        })
      : null),
    [
      seriesData, seriesPivot, analytics, displaySecurities, selectedCodes,
      attribution, series, themeMode, isSingleItemMode, selectedItemCode,
      normalized,
    ],
  );

  const selectedSecs = useMemo<MarginSecurity[]>(() => {
    if (!seriesData) return [];
    return selectedCodes
      .map((code) => seriesData.securities.find((s) => s.code === code))
      .filter((s): s is MarginSecurity => s != null);
  }, [selectedCodes, seriesData]);

  // ---- AI Ask (insight: derivatives.margin-composite) — the "?" rides
  // the section title strip inside the bare BaseChart. Authored text:
  // static.docs `ui.margin.trends`. ----
  const trendsChartRef = useRef<ECharts | null>(null);
  const docs = useDocs();
  const trendsAiAskSpec = useMemo<AiAskSpec>(
    () => ({
      insight: INSIGHT_PAGES.marginComposite,
      intro: docs.text("ui.margin.trends"),
      instruments: [{ code: industryId, assetClass: "industry" }],
      state: {
        attribution,
        series: series === "balance" ? "RZ balance" : "RZ buy",
        value_mode: normalized ? "normalized (member-avg)" : "raw (SUM)",
      },
      notes: docs.notes("ui.margin.trends"),
    }),
    [docs, industryId, attribution, series, normalized],
  );
  const trendsAiAskAddon = useAiAskAddon({
    title: "Margin trend composite",
    option: trendsOption,
    spec: trendsOption != null ? trendsAiAskSpec : null,
    getInstance: () => trendsChartRef.current,
  });

  const regimeCodes = useMemo(() => selectedCodes.slice(0, MAX_REGIME_CHARTS), [selectedCodes]);

  return (
    <Box>
      <MarginTrendControls
        series={series}
        onSeriesChange={setSeries}
        seriesOptions={SERIES_OPTIONS}
        valueMode={attribution === "industry" ? valueMode : undefined}
        onValueModeChange={attribution === "industry" ? setValueMode : undefined}
        securities={displaySecurities}
        selectedSecurities={selectedSecs}
        onSelectedChange={(secs) => setSelectedCodes(secs.map((s) => s.code))}
        underlyingOptions={underlyingOptions}
        underlyingCode={underlyingCode}
        onUnderlyingChange={onUnderlyingChange}
        showAutocomplete={!isSingleItemMode && !!seriesData
          && seriesData.rows.length > 0
          // One "security" (the exchange itself) leaves nothing to highlight.
          && seriesData.securities.length > 1}
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
        onReady={(inst) => {
          trendsChartRef.current = inst;
        }}
      >
        {!loadingSeries && seriesData && seriesData.rows.length > 0 && (
          <Box>
            <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
              {trendsAiAskAddon}
              {seriesData.industry_label}
              {seriesData.underlying_label && (
                <Typography
                  component="span"
                  sx={{ fontWeight: 400 }}
                  title="mapped underlying for the daily-return / hyped-vs-drained views"
                >
                  {" · "}{seriesData.underlying_label}
                </Typography>
              )}
              {" — "}
              {attribution === "index"
                ? "Index"
                : attribution === "etf"
                  ? "ETF"
                  : attribution === "exchange"
                    ? "Exchange (official market-wide total)"
                    : normalized
                      ? "Industry (member-avg stock+ETF)"
                      : "Industry (SUM stock+ETF)"}{" "}
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
              vol.${seriesData.rows.some((r) => r.n_members != null) ? " Dots = member-count change (the population behind the industry averages shifted)." : ""}`}
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
