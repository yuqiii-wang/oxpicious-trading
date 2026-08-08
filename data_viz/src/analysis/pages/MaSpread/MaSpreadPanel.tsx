/**
 * MaSpreadPanel — one card per code: pair chips + two-curve chart with
 * green/red fill between them + date-range slider + Bollinger envelope.
 *
 * Each panel renders (top → bottom):
 *   1. 9 pair chips arranged as a 2-row grid aligned by long MA — the Price
 *      row (Price/MA5 … Price/MA255) above the MA5 row (MA5/MA20 …
 *      MA5/MA255, with the MA5 column empty). A "Trend Study" column header
 *      sits above the MA60 column (shared by Price/MA60 and MA5/MA60) and
 *      highlights when either MA60 pair is active. Clicking a chip selects
 *      the pair shown in the chart below.
 *   2. Two-curve chart (short + long MA) with green fill when short > long
 *      (growth) and red fill when short < long (decline). The tooltip shows
 *      each series' slope (1st derivative) and curvature (2nd derivative)
 *      — including price's own slope/curvature for Price/MA pairs.
 *   3. Bollinger envelope (Price/MA pairs only): ±k×σ dashed lines around
 *      the long MA, with a faint fill between them. k is selected from a
 *      dropdown in the card's top-right corner (0 = hidden, 2 = standard
 *      Bollinger, max 3, step 0.5). MA5/MA pairs do not show the envelope
 *      (σ is of price, not of an MA-of-MA) and the dropdown is hidden.
 *   4. Date-range slider at the bottom of the plot — drives all 9 pairs
 *      (they share one date axis).
 *
 * Fetches its own chart data on mount via fetchMovAveSpreadChart(code, secType).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import DateRangeSlider from "@/components/DateRangeSlider";
import EChart from "@/components/EChart";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import { UP_COLOR } from "@/theme/chart-palette";
import { fmtNum, fmtPct } from "@/lib/series";
import { fetchMovAveSpreadChart } from "@/lib/api-client";
import type { OhlcMode } from "@/lib/ohlc";
import type {
  MovAveSpreadChartResponse,
  MovAveSpreadPairSeries,
} from "../../../../shared/types";
import type { PanelProps } from "./types";
import { buildPairOption, type TradingAmtMode } from "./chartOption";

/** Bollinger multiplier options for the top-right dropdown (0.0 … 3.0, step 0.5).
 *  0.0 = band hidden; 2.0 = standard Bollinger. */
const BOLL_K_OPTIONS = [0, 0.5, 1, 1.5, 2, 2.5, 3];

/**
 * Long-MA column order used to lay out the 9 pair chips as a 2-row grid
 * aligned by long MA (so Price/MA60 and MA5/MA60 share one column). The
 * MA5 row leaves the MA5 column empty (no MA5/MA5 pair exists).
 */
const LONG_MA_ORDER = [5, 20, 60, 120, 255] as const;
/** Column index of MA60 in LONG_MA_ORDER — gets the "Trend Study" header. */
const TREND_STUDY_COL = 2;

export function MaSpreadPanel({ code, name, secType, themeMode }: PanelProps) {
  // ---- Chart data ---------------------------------------------------------
  const [chartData, setChartData] = useState<MovAveSpreadChartResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Which of the 9 pairs is shown in the single plot (default 0 = Price/MA5).
  const [selectedPairIdx, setSelectedPairIdx] = useState(0);

  // Date-range slider state — two indices into the first pair's rows array.
  // The slider drives ALL pairs (they share the same date axis).
  const firstPairRows = chartData?.pairs[0]?.rows ?? [];
  const maxIdx = firstPairRows.length - 1;
  const [range, setRange] = useState<[number, number]>([0, maxIdx]);

  // Bollinger multiplier k in MA ± k×σ. Default 2 (standard Bollinger).
  // 0 hides the envelope. Only affects Price/MA pairs (ma_short === 0);
  // MA5/MA pairs don't get the envelope and the dropdown is hidden.
  // Options: 0, 0.5, 1, 1.5, 2, 2.5, 3 (step 0.5).
  const [bollingerK, setBollingerK] = useState(2);

  // Trading amount display mode: off / lowkey / highlight.
  // Defaults to "lowkey" — shows subtle bars by default.
  const [tradingAmtMode, setTradingAmtMode] = useState<TradingAmtMode>("lowkey");

  // OHLC display mode — "percentage" (default) rebases OHLC + MAs to % change
  // from the first valid close; "absolute" shows raw prices.
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");

  // Hovered date index (into the filtered/sliced rows of the selected pair).
  // Drives the single last-extreme triangle marker shown on hover. Reset
  // whenever the data window changes (range / code / secType) since indices
  // shift across slices.
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  // Fetch chart data on mount and whenever the code/sec_type changes.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchMovAveSpreadChart(code, secType)
      .then((d) => {
        if (cancelled) return;
        setChartData(d);
        const m = Math.max(0, (d.pairs[0]?.rows.length ?? 1) - 1);
        setRange([0, m]);
        setSelectedPairIdx(0);
        setHoveredIdx(null);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setChartData(null);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType]);

  // Clear the hover marker when the date-range slider moves (the hovered index
  // is relative to the sliced window and may no longer be valid).
  useEffect(() => {
    setHoveredIdx(null);
  }, [range]);

  // Track the hovered date index via ECharts' `updateAxisPointer` event so we
  // can draw a single last-extreme triangle at the hovered date's
  // date_of_last_extreme. Fires only when the axis pointer snaps to a new
  // category (date), not on every pixel move — low overhead.
  const handleAxisPointer = useCallback((params: unknown) => {
    const p = params as {
      axesInfo?: Array<{
        seriesDataIndices?: Array<{ dataIndex: number }>;
      }>;
    };
    const axes = p?.axesInfo;
    if (!axes || axes.length === 0) {
      setHoveredIdx(null);
      return;
    }
    const indices = axes[0]?.seriesDataIndices;
    if (!indices || indices.length === 0) {
      setHoveredIdx(null);
      return;
    }
    setHoveredIdx(indices[0].dataIndex);
  }, []);

  const chartEvents = useMemo(
    () => ({ updateAxisPointer: handleAxisPointer }),
    [handleAxisPointer],
  );

  // Slice each pair's rows to the selected date window.
  const filteredPairs = useMemo(() => {
    if (!chartData) return [];
    return chartData.pairs.map((p) => ({
      ...p,
      rows: p.rows.slice(range[0], range[1] + 1),
    }));
  }, [chartData, range]);

  // Lookup from `${ma_short}-${ma_long}` → index in filteredPairs, used to
  // place each pair chip in its long-MA column of the 2-row pair grid.
  const pairIndexMap = useMemo(() => {
    const m = new Map<string, number>();
    filteredPairs.forEach((p, i) => m.set(`${p.ma_short}-${p.ma_long}`, i));
    return m;
  }, [filteredPairs]);

  // Clamp selectedPairIdx to valid range.
  const safePairIdx = Math.min(selectedPairIdx, Math.max(0, filteredPairs.length - 1));
  const selectedPair = filteredPairs[safePairIdx];
  // True when the active pair is a Price/MA60 or MA5/MA60 "trend study" pair —
  // highlights the Trend Study column header.
  const trendStudyActive = selectedPair?.ma_long === 60;

  // Optional secondary stat row from the latest snapshot of all 9 pairs —
  // surfaced as a small caption so the user can scan the page quickly.
  const latestSummary = chartData?.pairs[safePairIdx]?.rows.slice(-1)[0] ?? null;

  const subtitle = chartData
    ? `${chartData.code} · ${chartData.name || name || "—"} · ${firstPairRows.length} bars` +
      (firstPairRows.length > 0
        ? ` · ${firstPairRows[0].date} → ${firstPairRows[firstPairRows.length - 1].date}`
        : "")
    : `${code} · ${name || "—"}`;

  // Bollinger dropdown + Trading Amt toggle shown in the card header's top-right corner.
  const bollAction = !loading && !error && selectedPair ? (
    <Stack direction="row" alignItems="center" spacing={1} sx={{ mr: 0.5 }}>
      {selectedPair.ma_short === 0 && (
        <Stack direction="row" alignItems="center" spacing={0.5}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ fontSize: "0.7rem", whiteSpace: "nowrap" }}
          >
            Bollinger
          </Typography>
          <Select
            size="small"
            value={bollingerK}
            onChange={(e) => setBollingerK(e.target.value as number)}
            sx={{
              height: 26,
              fontSize: "0.75rem",
              "& .MuiSelect-select": { py: 0.25, px: 1, fontSize: "0.75rem" },
            }}
            renderValue={(v) =>
              v === 0 ? "Off" : `${Number(v).toFixed(1)}σ`
            }
          >
            {BOLL_K_OPTIONS.map((k) => (
              <MenuItem key={k} value={k} sx={{ fontSize: "0.75rem", py: 0.25 }}>
                {k === 0 ? "Off (0.0)" : `${k.toFixed(1)}σ`}
              </MenuItem>
            ))}
          </Select>
        </Stack>
      )}
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ fontSize: "0.7rem", whiteSpace: "nowrap" }}
        >
          Amt
        </Typography>
        <Chip
          label={tradingAmtMode === "off" ? "Off" : tradingAmtMode === "lowkey" ? "Low" : "High"}
          size="small"
          clickable
          color={tradingAmtMode === "off" ? "default" : "primary"}
          variant={tradingAmtMode === "highlight" ? "filled" : "outlined"}
          onClick={() => {
            const next: TradingAmtMode =
              tradingAmtMode === "off" ? "lowkey"
              : tradingAmtMode === "lowkey" ? "highlight"
              : "off";
            setTradingAmtMode(next);
          }}
          sx={{ fontSize: "0.7rem", height: 22 }}
        />
      </Stack>
      <OhlcModeToggle value={ohlcMode} onChange={setOhlcMode} />
    </Stack>
  ) : undefined;

  // Render a single pair chip (used in the 2-row pair grid). The chip fills
  // its grid column: display:flex overrides MUI's default inline-flex so
  // width:100% takes effect, and the label is centered within.
  const renderPairChip = (pair: MovAveSpreadPairSeries, idx: number) => {
    const active = idx === safePairIdx;
    return (
      <Chip
        label={pair.pair_label}
        clickable
        size="small"
        color={active ? "primary" : "default"}
        variant={active ? "filled" : "outlined"}
        onClick={() => setSelectedPairIdx(idx)}
        sx={{
          fontSize: "0.7rem",
          height: 24,
          width: "100%",
          display: "flex",
          justifyContent: "center",
        }}
      />
    );
  };

  return (
    <ChartCard
      title={selectedPair ? selectedPair.pair_label : "MA-Spread"}
      subtitle={subtitle}
      action={bollAction}
    >
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={24} />
        </Box>
      )}
      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}

      {/* Pair chips — two rows aligned by long MA (Price row + MA5 row),
          with a "Trend Study" column header above the MA60 column. Moved to
          the top of the card so the time slider can sit at the bottom. */}
      {!loading && !error && filteredPairs.length > 0 && (
        <Box sx={{ mt: 1, mb: 0.5 }}>
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ mb: 0.5, display: "block", fontSize: "0.7rem" }}
          >
            Pairs — click to switch
          </Typography>
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: "repeat(5, minmax(0, 1fr))",
              gap: 0.75,
              alignItems: "center",
            }}
          >
            {/* Header row: "Trend Study" label above the MA60 column. */}
            {LONG_MA_ORDER.map((maLong, col) => (
              <Box
                key={`hdr-${maLong}`}
                sx={{ gridColumn: col + 1, textAlign: "center", minHeight: 18 }}
              >
                {col === TREND_STUDY_COL && (
                  <Typography
                    variant="caption"
                    component="span"
                    sx={{
                      fontSize: "0.65rem",
                      fontWeight: 700,
                      px: 1,
                      py: 0.25,
                      borderRadius: 1,
                      display: "inline-block",
                      color: trendStudyActive ? "#fff" : "#B71C1C",
                      bgcolor: trendStudyActive
                        ? "rgba(229, 57, 53, 0.85)"
                        : "rgba(229, 57, 53, 0.10)",
                      border: "1px solid rgba(229, 57, 53, 0.35)",
                    }}
                  >
                    Trend Study
                  </Typography>
                )}
              </Box>
            ))}
            {/* Price row (ma_short = 0): one chip per long-MA column. */}
            {LONG_MA_ORDER.map((maLong, col) => {
              const idx = pairIndexMap.get(`0-${maLong}`);
              return (
                <Box key={`price-${col}`} sx={{ gridColumn: col + 1 }}>
                  {idx != null && renderPairChip(filteredPairs[idx], idx)}
                </Box>
              );
            })}
            {/* MA5 row (ma_short = 5): no MA5/MA5 pair — col 0 left empty. */}
            {LONG_MA_ORDER.map((maLong, col) => {
              if (maLong === 5) {
                return <Box key={`ma5-empty-${col}`} sx={{ gridColumn: col + 1 }} />;
              }
              const idx = pairIndexMap.get(`5-${maLong}`);
              return (
                <Box key={`ma5-${col}`} sx={{ gridColumn: col + 1 }}>
                  {idx != null && renderPairChip(filteredPairs[idx], idx)}
                </Box>
              );
            })}
          </Box>
        </Box>
      )}

      {!loading && !error && selectedPair && selectedPair.rows.length > 0 && (
        <EChart
          option={buildPairOption({
            pair: selectedPair,
            themeMode,
            bollingerK,
            tradingAmtMode,
            valleyLows: chartData?.valley_lows,
            hoveredIdx,
            ohlcMode,
          })}
          height={420}
          onEvents={chartEvents}
        />
      )}

      {!loading && !error && selectedPair && selectedPair.rows.length === 0 && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}>
          <Typography variant="caption" color="text.secondary">
            No data for {selectedPair.pair_label} in this date range.
          </Typography>
        </Box>
      )}

      {/* Latest-snapshot summary line for the selected pair. */}
      {!loading && !error && latestSummary && (
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mt: 0.5, fontSize: "0.7rem" }}
        >
          {selectedPair?.pair_label} @ {latestSummary.date} · short {fmtNum(latestSummary.short_value)} ·
          long {fmtNum(latestSummary.long_value)} · gap{" "}
          <Box
            component="span"
            sx={{
              color: latestSummary.gap_value == null ? "text.disabled" : UP_COLOR,
              fontWeight: 600,
            }}
          >
            {latestSummary.gap_value == null
              ? "—"
              : fmtPct(latestSummary.gap_value * 100, 2)}
          </Box>
        </Typography>
      )}

      {/* Date-range slider — moved to the bottom of the plot. Drives all
          9 pairs (they share one date axis). */}
      {!loading && !error && (
        <DateRangeSlider
          value={range}
          onChange={setRange}
          max={maxIdx}
          dates={firstPairRows.map((r) => r.date)}
        />
      )}
    </ChartCard>
  );
}
