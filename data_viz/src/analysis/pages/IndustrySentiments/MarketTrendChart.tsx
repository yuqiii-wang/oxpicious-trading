/**
 * MarketTrendChart — the sole plot in "Market Trend" mode.
 *
 * Layout (top → bottom):
 *   1. Combined overview chart — all four broad-market indices' closes
 *      rebased to 100 (left axis, lines) + trading amount embedded as
 *      stacked bars on a right axis (proportional aggregation).
 *
 * A shared date-range slider at the bottom windows the chart.
 *
 * Data is fetched in parallel via the index-baseline combined endpoint (one
 * request per index), which returns full OHLC + MAs + trading_amount from
 * stats.v_index_baseline.
 *
 * SUB-VIEW TOGGLE: "Overview" (default) vs "Hypes & Drains". The latter
 * shows the pre-computed top-5 HYPE / bottom-5 DRAIN industries against a
 * broad-market benchmark — sourced from analysis.industry_hypes_and_drains.
 * The benchmark dropdown (same Autocomplete as Benchmark Attribution) is
 * only visible in "Hypes & Drains" sub-view.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Autocomplete,
  Box,
  CircularProgress,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import {
  fetchIndicesCombined,
  fetchIndustryAttributionBenchmarks,
} from "@/lib/api-client";
import type {
  IndexBaselineRow,
  IndustryAttributionBenchmarkEntry,
} from "../../../../shared/types";
import type { MarketTrendChartProps } from "./types";
import { MARKET_TREND_INDICES } from "./constants";
import {
  buildMarketTrendOption,
  toIndexSeriesData,
} from "./marketTrendOption";
import { HypesAndDrainsChart } from "./HypesAndDrainsChart";

/** Sub-view of Market Trend mode. */
type MarketTrendSubView = "overview" | "hypes_drains";

interface IndexData {
  code: string;
  name: string;
  color: string;
  rows: IndexBaselineRow[];
}

export function MarketTrendChart({ themeMode }: MarketTrendChartProps) {
  const [datasets, setDatasets] = useState<IndexData[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Lowkey toggle: show/hide the embedded trading-amount stacked bars.
  // Default ON to preserve the prior combined-view behavior.
  const [showAmt, setShowAmt] = useState(true);

  // Sub-view toggle: "overview" (default — 4 broad-market indices) vs
  // "hypes_drains" (pre-computed top-5 HYPE / bottom-5 DRAIN industries
  // against a broad-market benchmark).
  const [subView, setSubView] = useState<MarketTrendSubView>("overview");

  // Benchmark dropdown state (for the Hypes & Drains sub-view). Uses the
  // SAME broad-market benchmark list as Benchmark Attribution.
  const [benchmarks, setBenchmarks] = useState<IndustryAttributionBenchmarkEntry[]>([]);
  const [benchmarkCode, setBenchmarkCode] = useState<string>("000300");

  // Fetch the benchmark list once when the user enters Hypes & Drains mode.
  useEffect(() => {
    if (subView !== "hypes_drains" || benchmarks.length > 0) return;
    let cancelled = false;
    fetchIndustryAttributionBenchmarks()
      .then((resp) => {
        if (cancelled) return;
        setBenchmarks(resp.benchmarks);
      })
      .catch(() => {
        // Non-fatal — the dropdown will be empty but the default
        // benchmarkCode (000300) still works via the API.
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subView]);

  // Fetch all four indices' OHLC series in parallel via the index-baseline
  // combined endpoint (one request per index, returns full OHLC + MAs +
  // trading_amount). Keyed on the joined code string so it only fires once.
  const codesKey = MARKET_TREND_INDICES.map((m) => m.code).join(",");
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all(
      MARKET_TREND_INDICES.map((m) =>
        fetchIndicesCombined(
          null, null, null, null,
          1, 1,
          m.code,
          null,
        ).then((resp) => ({
          code: m.code,
          name: m.name,
          color: m.color,
          rows: resp.indices[0]?.rows ?? [],
        })),
      ),
    )
      .then((results) => {
        if (cancelled) return;
        setDatasets(results);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [codesKey]);

  // Unified date axis — sorted union of all four indices' dates.
  const allDates = useMemo(() => {
    const set = new Set<string>();
    for (const ds of datasets) {
      for (const r of ds.rows) set.add(r.date);
    }
    return Array.from(set).sort();
  }, [datasets]);

  // Combined overview chart option (close lines + embedded trading amount
  // when `showAmt` is ON). Passes the FULL allDates + datasets — the chart's
  // in-chart dataZoom handles viewport control.
  const trendOption = useMemo(
    () =>
      datasets.length > 0 && allDates.length > 0
        ? buildMarketTrendOption(
            allDates,
            datasets.map((d) => toIndexSeriesData(d.code, d.rows)),
            MARKET_TREND_INDICES.map((m) => m.code),
            themeMode,
            showAmt,
          )
        : null,
    [datasets, allDates, themeMode, showAmt],
  );

  const loadedCount = datasets.filter((d) => d.rows.length > 0).length;

  const subtitle = loading
    ? "Loading market indices…"
    : subView === "hypes_drains"
      ? "Pre-computed top-5 HYPE / bottom-5 DRAIN industries vs broad-market benchmark"
      : `${loadedCount} of ${MARKET_TREND_INDICES.length} indices loaded · combined overview` +
        (showAmt ? " with embedded trading amount" : "") +
        (loadedCount < MARKET_TREND_INDICES.length
          ? ` · ${MARKET_TREND_INDICES.length - loadedCount} with no data (skipped)`
          : "");

  // The currently selected benchmark entry (for the Autocomplete value).
  const selectedBenchmark = useMemo(
    () => benchmarks.find((b) => b.benchmark_code === benchmarkCode) ?? null,
    [benchmarks, benchmarkCode],
  );

  return (
    <ChartCard
      title="Market Trend"
      subtitle={subtitle}
    >
      {/* --- Sub-view toggle: Overview vs Hypes & Drains --- */}
      {/* When Hypes & Drains is active, a benchmark Autocomplete dropdown
          appears next to it (same dropdown as Benchmark Attribution — all
          broad-market ★ benchmarks). */}
      <Box
        sx={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 1,
          mb: 1,
          flexWrap: "wrap",
        }}
      >
        <ToggleButtonGroup
          value={subView}
          exclusive
          size="small"
          onChange={(_, v: MarketTrendSubView | null) => {
            if (v) setSubView(v);
          }}
        >
          <ToggleButton value="overview" sx={{ height: 28, px: 1.5, fontSize: "0.72rem" }}>
            Overview
          </ToggleButton>
          <ToggleButton value="hypes_drains" sx={{ height: 28, px: 1.5, fontSize: "0.72rem" }}>
            Hypes &amp; Drains
          </ToggleButton>
        </ToggleButtonGroup>
        {subView === "hypes_drains" && (
          <Autocomplete
            size="small"
            sx={{ minWidth: 260, flex: "1 1 260px", maxWidth: 400 }}
            options={benchmarks}
            getOptionLabel={(b) =>
              `${b.benchmark_name} (${b.benchmark_code})${b.is_broad_market === true ? " ★" : ""}`
            }
            isOptionEqualToValue={(a, b) => a.benchmark_code === b.benchmark_code}
            value={selectedBenchmark}
            onChange={(_, newValue) => {
              if (newValue) setBenchmarkCode(newValue.benchmark_code);
            }}
            renderInput={(params) => (
              <TextField
                {...params}
                size="small"
                placeholder="Select benchmark"
                sx={{ "& .MuiOutlinedInput-input": { fontSize: "0.75rem", py: 0.5 } }}
              />
            )}
          />
        )}
      </Box>

      {/* --- Overview sub-view --- */}
      {subView === "overview" && (
        <>
          {loading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          )}
          {error && (
            <Alert severity="error" sx={{ py: 0.5 }}>
              Failed to load market trend data: {error}
            </Alert>
          )}
          {!loading && !error && (
            <Stack spacing={1.5}>
              {/* --- Combined close + embedded trading amount --- */}
              {trendOption && (
                <Box>
                  <Box
                    sx={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      gap: 1,
                      mb: -0.5,
                      px: 0.5,
                    }}
                  >
                    <Typography
                      variant="caption"
                      sx={{ fontSize: "0.72rem", fontWeight: 600, display: "block" }}
                    >
                      Close (rebased = 100){showAmt ? " + Trading Amount (stacked)" : ""}
                    </Typography>
                    <ToggleButton
                      size="small"
                      value="showAmt"
                      selected={showAmt}
                      onClick={() => setShowAmt((v) => !v)}
                      sx={{
                        height: 20,
                        px: 0.75,
                        "& .MuiToggleButton-label": { fontSize: "0.7rem" },
                        textTransform: "none",
                      }}
                    >
                      Trading Amt
                    </ToggleButton>
                  </Box>
                  <EChart option={trendOption} height={300} />
                </Box>
              )}
            </Stack>
          )}
        </>
      )}

      {/* --- Hypes & Drains sub-view --- */}
      {subView === "hypes_drains" && (
        <HypesAndDrainsChart benchmarkCode={benchmarkCode} themeMode={themeMode} />
      )}
    </ChartCard>
  );
}
