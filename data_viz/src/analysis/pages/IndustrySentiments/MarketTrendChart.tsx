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
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import DateRangeSlider from "@/components/DateRangeSlider";
import EChart from "@/components/EChart";
import { fetchIndicesCombined } from "@/lib/api-client";
import type { IndexBaselineRow } from "../../../../shared/types";
import type { MarketTrendChartProps } from "./types";
import { MARKET_TREND_INDICES } from "./constants";
import {
  buildMarketTrendOption,
  toIndexSeriesData,
} from "./marketTrendOption";

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

  // Shared date window — [startIdx, endIdx] indexes into allDates.
  const [range, setRange] = useState<[number, number]>([0, 0]);

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

  useEffect(() => {
    setRange([0, Math.max(0, allDates.length - 1)]);
  }, [allDates]);

  const maxIdx = Math.max(0, allDates.length - 1);

  // Window each index's rows to the shared date range.
  const windowedDatasets = useMemo(() => {
    if (allDates.length === 0) return [];
    const loDate = allDates[range[0]];
    const hiDate = allDates[range[1]];
    return datasets.map((ds) => ({
      ...ds,
      rows: ds.rows.filter((r) => r.date >= loDate && r.date <= hiDate),
    }));
  }, [datasets, allDates, range]);

  const windowedAllDates = useMemo(() => {
    if (allDates.length === 0) return [];
    return allDates.slice(range[0], range[1] + 1);
  }, [allDates, range]);

  // Combined overview chart option (close lines + embedded trading amount).
  const trendOption = useMemo(
    () =>
      windowedDatasets.length > 0 && windowedAllDates.length > 0
        ? buildMarketTrendOption(
            windowedAllDates,
            windowedDatasets.map((d) => toIndexSeriesData(d.code, d.rows)),
            MARKET_TREND_INDICES.map((m) => m.code),
            themeMode,
          )
        : null,
    [windowedDatasets, windowedAllDates, themeMode],
  );

  const loadedCount = datasets.filter((d) => d.rows.length > 0).length;

  const subtitle = loading
    ? "Loading market indices…"
    : `${loadedCount} of ${MARKET_TREND_INDICES.length} indices loaded · combined overview with embedded trading amount` +
      (loadedCount < MARKET_TREND_INDICES.length
        ? ` · ${MARKET_TREND_INDICES.length - loadedCount} with no data (skipped)`
        : "");

  return (
    <ChartCard
      title="Market Trend"
      subtitle={subtitle}
    >
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
              <Typography
                variant="caption"
                sx={{ fontSize: "0.72rem", fontWeight: 600, px: 0.5, display: "block", mb: -0.5 }}
              >
                Close (rebased = 100) + Trading Amount (stacked)
              </Typography>
              <EChart option={trendOption} height={300} />
            </Box>
          )}

          {/* Shared date-range slider — windows the chart. */}
          <DateRangeSlider
            value={range}
            onChange={setRange}
            max={maxIdx}
            dates={allDates}
          />
        </Stack>
      )}
    </ChartCard>
  );
}
