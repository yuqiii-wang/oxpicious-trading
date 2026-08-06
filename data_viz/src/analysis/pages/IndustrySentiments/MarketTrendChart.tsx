/**
 * MarketTrendChart — the sole plot in "Market Trend" mode.
 *
 * Layout (top → bottom):
 *   1. Combined overview chart — all four broad-market indices' closes
 *      rebased to 100 (left axis, lines) + trading amount embedded as
 *      stacked bars on a right axis (proportional aggregation). A
 *      multi-select dropdown above the chart controls which indices are
 *      drawn (tick to show/hide individual indexes).
 *   2. Per-index OHLC charts (IndexPanel style: shared `ohlcSeries` +
 *      MA5/MA20/MA60/MA120 + trading amount bars on a twin axis), one
 *      per index. An Absolute / % Change toggle in the card header
 *      controls the OHLC rebase mode for these charts.
 *
 * A shared date-range slider at the bottom windows all charts in lockstep.
 *
 * Data is fetched in parallel via the index-baseline combined endpoint (one
 * request per index), which returns full OHLC + MAs + trading_amount from
 * stats.v_index_baseline.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Checkbox,
  CircularProgress,
  FormControl,
  InputLabel,
  ListItemText,
  MenuItem,
  Select,
  Slider,
  Stack,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import { fetchIndicesCombined } from "@/lib/api-client";
import type { IndexBaselineRow } from "../../../../shared/types";
import type { MarketTrendChartProps } from "./types";
import { MARKET_TREND_INDICES } from "./constants";
import type { OhlcMode } from "@/lib/ohlc";
import {
  buildMarketTrendOption,
  buildMarketOhlcOption,
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
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");

  // Which indices are visible on the combined overview chart. Default: all.
  const [visibleCodes, setVisibleCodes] = useState<string[]>(
    MARKET_TREND_INDICES.map((m) => m.code),
  );

  // Shared date window — [startIdx, endIdx] indexes into allDates. All
  // charts are windowed by the SAME slider so they stay aligned.
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
            visibleCodes,
            themeMode,
          )
        : null,
    [windowedDatasets, windowedAllDates, visibleCodes, themeMode],
  );

  const loadedCount = datasets.filter((d) => d.rows.length > 0).length;

  const subtitle = loading
    ? "Loading market indices…"
    : `${loadedCount} of ${MARKET_TREND_INDICES.length} indices loaded · OHLC + MAs · combined overview with embedded trading amount` +
      (loadedCount < MARKET_TREND_INDICES.length
        ? ` · ${MARKET_TREND_INDICES.length - loadedCount} with no data (skipped)`
        : "");

  return (
    <ChartCard
      title="Market Trend"
      subtitle={subtitle}
      action={<OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />}
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
          {/* --- 1st plot: Combined close + embedded trading amount --- */}
          {trendOption && (
            <Box>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  px: 0.5,
                  mb: -0.5,
                  gap: 1,
                }}
              >
                <Typography
                  variant="caption"
                  sx={{ fontSize: "0.72rem", fontWeight: 600 }}
                >
                  Close (rebased = 100) + Trading Amount (stacked)
                </Typography>
                <FormControl size="small" sx={{ minWidth: 180 }}>
                  <InputLabel sx={{ fontSize: "0.72rem" }}>Indices</InputLabel>
                  <Select
                    multiple
                    label="Indices"
                    value={visibleCodes}
                    onChange={(e) => {
                      const v = e.target.value;
                      const val = Array.isArray(v) ? v : [v];
                      // Keep at least one selected.
                      if (val.length > 0) setVisibleCodes(val);
                    }}
                    renderValue={(selected) =>
                      (selected as string[])
                        .map(
                          (code) =>
                            MARKET_TREND_INDICES.find((m) => m.code === code)
                              ?.name ?? code,
                        )
                        .join(", ")
                    }
                    sx={{ fontSize: "0.72rem", height: 28 }}
                    MenuProps={{
                      PaperProps: { sx: { maxHeight: 220 } },
                    }}
                  >
                    {MARKET_TREND_INDICES.map((m) => (
                      <MenuItem key={m.code} value={m.code} sx={{ py: 0.25 }}>
                        <Checkbox
                          checked={visibleCodes.includes(m.code)}
                          size="small"
                          sx={{ color: m.color }}
                          style={{ color: m.color }}
                        />
                        <ListItemText
                          primary={m.name}
                          sx={{ fontSize: "0.75rem" }}
                          primaryTypographyProps={{ fontSize: "0.75rem" }}
                        />
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Box>
              <EChart option={trendOption} height={300} />
            </Box>
          )}

          {/* --- Per-index OHLC charts (IndexPanel style) --- */}
          {windowedDatasets.map((ds) => {
            const option =
              ds.rows.length > 0
                ? buildMarketOhlcOption(
                    toIndexSeriesData(ds.code, ds.rows),
                    ohlcMode,
                    themeMode,
                  )
                : null;
            return (
              <Box key={ds.code}>
                <Typography
                  variant="caption"
                  sx={{
                    display: "block",
                    px: 0.5,
                    mb: -0.5,
                    fontSize: "0.72rem",
                    fontWeight: 600,
                    color: ds.color,
                  }}
                >
                  {ds.code} · {ds.name}
                  {ds.rows.length === 0 && " (no data)"}
                </Typography>
                {option ? (
                  <EChart option={option} height={200} />
                ) : (
                  <Box
                    sx={{
                      height: 200,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                    }}
                  >
                    <Typography variant="body2" color="text.secondary">
                      No data
                    </Typography>
                  </Box>
                )}
              </Box>
            );
          })}

          {/* Shared date-range slider — windows all charts in lockstep. */}
          {maxIdx > 0 && (
            <Box sx={{ px: 1, mt: 0.5 }}>
              <Slider
                value={range}
                onChange={(_, v) => setRange(v as [number, number])}
                min={0}
                max={maxIdx}
                size="small"
                valueLabelDisplay="auto"
                valueLabelFormat={(idx) => allDates[idx] ?? ""}
                sx={{ mt: 0.5, "& .MuiSlider-valueLabel": { fontSize: "0.7rem" } }}
              />
              <Stack direction="row" justifyContent="space-between" sx={{ mt: -0.5 }}>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                  {allDates[range[0]] ?? "—"}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem" }}>
                  {allDates[range[1]] ?? "—"}
                </Typography>
              </Stack>
            </Box>
          )}
        </Stack>
      )}
    </ChartCard>
  );
}
