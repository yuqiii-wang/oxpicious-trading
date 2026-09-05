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
 *   4. OHLC Window section beneath the Trading Amt/MA section — an
 *      "OHLC Window" row label (same style as the Trading Amt/MA label)
 *      on its own full-width row, with the window buttons (20 … 1275d)
 *      on a new row below it. The buttons keep the period-column
 *      alignment of the pair chips — 20d sits under the MA20 column, …,
 *      1275d in the last column. Clicking one enables that rolling
 *      window's High/Low envelope on the chart and arms the roof/floor
 *      interaction — clicking a date on the chart draws the trendline
 *      through the window's top + 2nd highs (the roof) and top + 2nd
 *      lows (the floor) from history, converging and stopping at the
 *      clicked date (two points determining a line).
 *   5. Market Hype section beneath the OHLC Window row — a "Market Hype"
 *      row label with check-in window buttons (5/20/60/120/255d,
 *      period-column aligned, same chip style as every other button
 *      row). Clicking toggles that window's light purple markArea over
 *      the chart's hyped date periods (analysis.mov_ave_market_hypes);
 *      MULTIPLE windows can be enabled at once — overlapping shades
 *      stack darker. The caption below reports each enabled window's
 *      stats and the latest date's hyped state.
 *   6. High/Low Streaks section beneath the Market Hype row — NESTED
 *      buttons: the first layer holds the band lookback periods
 *      (255/500/750/1275, period-column aligned with the OHLC row);
 *      clicking one expands a second layer of band tightness pcts
 *      (1/5/10%). Selecting a pct fills the LATEST date's trailing
 *      period-row window with its top/bottom pct% price zones (light
 *      purple above high_val, light yellow below low_val) and draws the
 *      WHOLE-WINDOW LONG BREAK STREAK per side — the in-window DB streaks
 *      merged into ONE span (first start → last end, gaps between them
 *      tolerated), shaded as a single horizontal band from the window's
 *      constant band edge to the merged extreme. Drawing each DB streak
 *      separately (each to its own peak against its own month's moving
 *      band) fractured into slivers, so only the merged span is drawn.
 *      Toggled per side via the chart legend. Clicking
 *      a chart date anchors the window to the trailing rows before that
 *      date ("show before that date"; click again to return to the
 *      latest). The caption reports the window span and each side's
 *      streak span / streak count / days / extreme.
 *   7. Date-range slider at the bottom of the plot — drives all 9 pairs
 *      (they share one date axis).
 *
 * Fetches its own chart data on mount via fetchMovAveSpreadChart(code, secType).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
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
import EChart from "@/components/EChart";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import AnalysisRunButton from "@/components/AnalysisRunButton";
import { UP_COLOR } from "@/theme/chart-palette";
import { fmtNum, fmtPct } from "@/lib/series";
import { fetchMovAveSpreadChart, invalidateCacheForUrl } from "@/lib/api-client";
import type { OhlcMode } from "@/lib/ohlc";
import type {
  ForecastKind,
  MovAveSpreadChartResponse,
  MovAveSpreadPairSeries,
} from "@shared/types";
import type { PanelProps } from "./types";
import {
  OHLC_WINDOWS,
  HYPE_WINDOWS,
  HIGH_LOW_STREAK_PERIODS,
  HIGH_LOW_STREAK_PCTS,
} from "./constants";
import { buildPairOption, buildAmtEnvelopeOption, type TradingAmtMode } from "./chartOption";
import {
  computeBreakStreaks,
  computeStreakBandWindow,
  type LongBandStreak,
} from "@/shared/charts/streakBands";
import { ForecastTable } from "./ForecastTable";

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

/**
 * Long-EMA column order for the 9 EMA pair chips. EMA windows are
 * 6/20/60/120/255 (EMA6 replaces MA5 — EMAs use 6 instead of 5 as the
 * short window). Same structure as LONG_MA_ORDER but with 6 in column 0.
 */
const LONG_EMA_ORDER = [6, 20, 60, 120, 255] as const;

/**
 * Shared period-column grid — one column per period (5, 20, 60, 120, 255,
 * 500, 750, 1275) so every button row aligns vertically by period. Pair
 * rows only fill the first 5 columns (their long MAs stop at 255); the
 * OHLC Window row spans the full width (its 20d … 1275d buttons continue
 * into the extra columns under their matching periods).
 */
const PERIOD_GRID_SX: CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(8, minmax(0, 1fr))",
  gap: 0.75,
  alignItems: "center",
};

/**
 * Shared Chip sx for every period button (pair chips + OHLC-window
 * buttons): fill its grid cell with a centered label, compact size.
 */
const PERIOD_CHIP_SX: CSSProperties = {
  fontSize: "0.7rem",
  height: 24,
  width: "100%",
  display: "flex",
  justifyContent: "center",
};

export function MaSpreadPanel({ code, name, secType, themeMode }: PanelProps) {
  // ---- Chart data ---------------------------------------------------------
  const [chartData, setChartData] = useState<MovAveSpreadChartResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Bumped by the per-security AnalysisRunButton after a rebuild run —
  // retriggers the chart fetch (the cache entry is invalidated first in
  // the completion handler).
  const [refreshKey, setRefreshKey] = useState(0);

  // Which of the 9 pairs is shown in the single plot (default 0 = Price/MA5).
  const [selectedPairIdx, setSelectedPairIdx] = useState(0);

  // The first pair's rows — used for the card subtitle (date range + bar count)
  // and as the x-axis dates for the chart (all pairs share one date axis).
  const firstPairRows = chartData?.pairs[0]?.rows ?? [];

  // Whether this security has analysis rows — drives the bold highlight of
  // the per-security build button (AnalysisRunButton). Loading counts as
  // "present" so the button doesn't bold-flicker on every fetch.
  const hasAnalysisData = loading || firstPairRows.length > 0;

  // Refetch after a per-security analysis rebuild (AnalysisRunButton):
  // drop the cached chart response, then bump the refresh key.
  const handleAnalysisRunCompleted = useCallback(() => {
    invalidateCacheForUrl(
      `/api/analysis/mov-ave-spread/chart?code=${code}&sec_type=${secType}`,
    );
    setRefreshKey((k) => k + 1);
  }, [code, secType]);

  // Bollinger multiplier k in MA ± k×σ. Default 2 (standard Bollinger).
  // 0 hides the envelope. Affects Price/MA and Price/EMA pairs (ma_short === 0);
  // MA5/MA and EMA6/EMA pairs don't get the envelope and the dropdown is hidden.
  // Options: 0, 0.5, 1, 1.5, 2, 2.5, 3 (step 0.5).
  const [bollingerK, setBollingerK] = useState(2);

  // Trading amount display toggle: "lowkey" (on) shows subtle bars, "off" hides them.
  // Defaults to "lowkey" — shows subtle bars by default.
  const [tradingAmtMode, setTradingAmtMode] = useState<TradingAmtMode>("lowkey");

  // OHLC display mode — "percentage" (default) rebases OHLC + MAs to % change
  // from the first valid close; "absolute" shows raw prices.
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");

  // Hovered date index (into the full rows of the selected pair — the chart's
  // x-axis is now the full data, with an in-chart dataZoom slider for
  // viewport control). Drives the single last-extreme triangle marker shown
  // on hover.
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  // Enabled rolling-OHLC window (trading days) — null = off. Selected via
  // the OHLC-window button row beneath the Trading Amt/MA section.
  const [ohlcWindow, setOhlcWindow] = useState<number | null>(null);

  // Clicked chart date index (into the full rows — shared date axis). The
  // roof/floor trendlines of the enabled window are drawn to (and stop at)
  // this date. Clicking the same date again clears it.
  const [ohlcClickIdx, setOhlcClickIdx] = useState<number | null>(null);

  // ENABLED market-hype check-in windows (trading days) — empty = off.
  // Selected via the Market Hype button row beneath the OHLC Window row;
  // each enabled window shades the chart's hyped date periods light
  // purple (multi-select — overlapping windows' shades stack darker).
  const [hypeWindows, setHypeWindows] = useState<number[]>([]);

  // High/Low Streaks nested buttons: layer 1 = band lookback period
  // (trading rows, null = row off), layer 2 = band tightness pct (percent,
  // null = no shading yet). Selecting a period expands the pct layer;
  // clicking the active period again collapses it (and clears the pct).
  const [streakPeriod, setStreakPeriod] = useState<number | null>(null);
  const [streakPct, setStreakPct] = useState<number | null>(null);

  const toggleStreakPeriod = useCallback((w: number) => {
    setStreakPeriod((prev) => {
      if (prev === w) {
        setStreakPct(null);
        return null;
      }
      return w;
    });
  }, []);

  const toggleStreakPct = useCallback((p: number) => {
    setStreakPct((prev) => (prev === p ? null : p));
  }, []);

  // 2nd-plot selector (beneath the spread chart): which forecast bucket
  // table to show — "" = none, "mov_rsi" = RSI extreme-percentile
  // buckets, "mov_std" = Bollinger-breach buckets, "mov_gap" = N-day
  // price-return extreme-percentile buckets, "px_vol" = σ-speed ×
  // 量比-z state cells (analysis_forecasts).
  const [forecastKind, setForecastKind] = useState<ForecastKind | "">("");

  // Toggle one hype check-in window in the enabled set (multi-select).
  const toggleHypeWindow = useCallback((w: number) => {
    setHypeWindows((prev) =>
      prev.includes(w) ? prev.filter((x) => x !== w) : [...prev, w],
    );
  }, []);

  // Fetch chart data on mount and whenever the code/sec_type changes.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchMovAveSpreadChart(code, secType)
      .then((d) => {
        if (cancelled) return;
        setChartData(d);
        setSelectedPairIdx(0);
        setHoveredIdx(null);
        setOhlcClickIdx(null);
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
  }, [code, secType, refreshKey]);

  // Track the hovered date index via ECharts' `updateAxisPointer` event so we
  // can draw a single last-extreme triangle at the hovered date's
  // date_of_last_extreme_500days. Fires only when the axis pointer snaps to a new
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

  // Canvas-level chart click: sets the anchor date. Only armed while an
  // OHLC window or the High/Low Streaks row is enabled; clicking the
  // already-selected date clears the anchor (toggle, back to the latest
  // date's window), clicking another date moves it.
  const handleCanvasClick = useCallback(
    (dataIdx: number) => {
      if (ohlcWindow == null && streakPeriod == null) return;
      setOhlcClickIdx((prev) => (prev === dataIdx ? null : dataIdx));
    },
    [ohlcWindow, streakPeriod],
  );

  // The full pairs list (no slicing — the chart's in-chart dataZoom handles
  // viewport control). Used for the pair chips, the pair index lookup, and the
  // chart option builder.
  const pairs = chartData?.pairs ?? [];

  // Lookup from `${kind}-${ma_short}-${ma_long}` → index in pairs, used to
  // place each pair chip in its long-MA column of the 2-row pair grid.
  // kind prefix ("price" | "ema" | "amt") separates the 3 pair families
  // that share the same (ma_short, ma_long) — e.g. Price/MA20 (price-0-20)
  // vs Price/EMA20 (ema-0-20).
  const pairIndexMap = useMemo(() => {
    const m = new Map<string, number>();
    pairs.forEach((p, i) => {
      const kind = p.kind ?? "price";
      m.set(`${kind}-${p.ma_short}-${p.ma_long}`, i);
    });
    return m;
  }, [pairs]);

  // ---- Market-hype data (analysis.mov_ave_market_hypes via
  // chartData.hypeEpisodes) ----
  // One episode list per check-in window — each episode is a maximal run
  // of consecutive hyped dates (startDate/endDate = first/last satisfied
  // dates). Windows with no episodes are absent from the map.
  const hypeEpisodes = chartData?.hypeEpisodes ?? null;

  // The chart's latest date (all pairs share one date axis) — the yardstick
  // for "currently hyped".
  const lastChartDate =
    pairs.length > 0 && pairs[0].rows.length > 0
      ? pairs[0].rows[pairs[0].rows.length - 1].date
      : null;

  // Whether ANY hype data exists for this code (buttons are disabled when
  // the table has no episodes for it — before the table's first build, or a
  // code that was never hyped in any window).
  const hasHypeData =
    hypeEpisodes != null &&
    Object.values(hypeEpisodes).some((eps) => eps.length > 0);

  // Each window's CURRENT hyped state — TRUE when an episode of that window
  // still covers the chart's latest date (a code's trailing episode extends
  // as new hyped dates arrive, so its endDate IS the latest hyped date).
  // Drives the "currently hyped" note in the caption under the selected
  // hype window.
  const latestHypeFlags = useMemo(() => {
    const m = new Map<number, boolean>();
    if (hypeEpisodes == null || lastChartDate == null) return m;
    for (const w of HYPE_WINDOWS) {
      m.set(
        w,
        (hypeEpisodes[w] ?? []).some((ep) => ep.endDate >= lastChartDate),
      );
    }
    return m;
  }, [hypeEpisodes, lastChartDate]);

  // Stats for the caption under the hype buttons, per ENABLED window:
  // number of hyped TRADING days in the full history (episode spans), the
  // per-leg check-in day counts (amt / σ legs — diagnostics for which leg
  // drove the episodes), and the last hyped date.
  const hypeWindowStats = useMemo(() => {
    const m = new Map<
      number,
      {
        count: number;
        amtDays: number | null;
        stdDays: number | null;
        lastDate: string | null;
      }
    >();
    if (hypeEpisodes == null) return m;
    for (const w of HYPE_WINDOWS) {
      if (!hypeWindows.includes(w)) continue;
      const eps = hypeEpisodes[w] ?? [];
      let count = 0;
      let amtDays = 0;
      let stdDays = 0;
      let lastDate: string | null = null;
      let hasLegData = false;
      for (const ep of eps) {
        count += ep.hypeDays;
        if (ep.tradingAmtHypeDays != null && ep.stdHypeDays != null) {
          hasLegData = true;
          amtDays += ep.tradingAmtHypeDays;
          stdDays += ep.stdHypeDays;
        }
        if (lastDate == null || ep.endDate > lastDate) lastDate = ep.endDate;
      }
      m.set(w, {
        count,
        amtDays: hasLegData ? amtDays : null,
        stdDays: hasLegData ? stdDays : null,
        lastDate,
      });
    }
    return m;
  }, [hypeWindows, hypeEpisodes]);

  // Exclusive upper bound (trading days) of a hype window's episode-span
  // bucket: the window's own length as the minimum, the next window as the
  // exclusive maximum (255d's tail is 5100 = the whole ±10y base).
  const hypeBucketUpper = useCallback((w: number): number => {
    const next =
      HYPE_WINDOWS[HYPE_WINDOWS.indexOf(w as (typeof HYPE_WINDOWS)[number]) + 1] ??
      5100;
    return next - 1;
  }, []);

  // Enabled hype windows in HYPE_WINDOWS order (stable caption ordering).
  const enabledHypeWindows = useMemo(
    () => HYPE_WINDOWS.filter((w) => hypeWindows.includes(w)),
    [hypeWindows],
  );

  // ---- High/Low Streaks data (analysis.mov_ave_high_low_pct_streaks via
  // chartData.highLowStreaks) ----
  // FLAT per-streak list across ALL (period, pctType) combos — the nested
  // buttons select a combo, and the WINDOW-CONFINED subset is merged per
  // DB band-break streak rows (analysis.mov_ave_high_low_pct_streaks,
  // tested against each month's OWN moving band) — NOT used for shading
  // (the break bands are detected client-side vs the anchor window's
  // static edge, see longStreaks below); they only gate the buttons'
  // availability.
  const highLowStreaks = chartData?.highLowStreaks ?? null;
  const hasStreakData = highLowStreaks != null && highLowStreaks.length > 0;

  // The anchor-date band window shown by default (latest date) or when a
  // chart date is clicked (trailing streakPeriod rows before it) — bounds
  // both the light zones and the per-streak break bands.
  const streakWin = useMemo(() => {
    if (streakPeriod == null || streakPct == null) return null;
    return computeStreakBandWindow(
      firstPairRows,
      streakPeriod,
      streakPct,
      ohlcClickIdx,
    );
  }, [firstPairRows, streakPeriod, streakPct, ohlcClickIdx]);

  // The BREAK STREAKS for the selected (period, pct) combo — detected
  // CLIENT-SIDE against the anchor window's own static band edges (the
  // same edges the light zones draw): days whose close (short_value on
  // the price pairs) is above high_val / below low_val, consolidated
  // with the ≤5-day in-band bridge. This guarantees the shading only
  // ever covers price the chart shows inside the drawn zone — the DB
  // streak rows (tested against each month's OWN moving band) would
  // shade old breakouts that sit below today's static edge. Each band
  // spans the window's whole vertical excursion (constant band edge →
  // the window's top/bottom price, same extent as the light zones).
  const longStreaks = useMemo<{
    high: LongBandStreak[];
    low: LongBandStreak[];
  } | null>(() => {
    if (streakPeriod == null || streakPct == null || streakWin == null) return null;
    return computeBreakStreaks(firstPairRows, streakWin);
  }, [firstPairRows, streakWin]);

  // Clamp selectedPairIdx to valid range.
  const safePairIdx = Math.min(selectedPairIdx, Math.max(0, pairs.length - 1));
  const selectedPair = pairs[safePairIdx];
  // True when the active pair is a Price/MA60 or MA5/MA60 "trend study" pair —
  // highlights the Trend Study column header.
  const trendStudyActive = selectedPair?.ma_long === 60 && selectedPair?.kind === "price";
  // True when an Amt/MA pair is selected — price chips are frozen (disabled).
  const amtPairSelected = selectedPair?.kind === "amt";

  // When tradingAmtMode is toggled off while an Amt/MA pair is selected,
  // reset to Price/MA5 (pair index 0) so the chart doesn't stay stuck on an
  // amt pair whose chips are now hidden.
  useEffect(() => {
    if (tradingAmtMode === "off" && amtPairSelected) {
      setSelectedPairIdx(0);
    }
  }, [tradingAmtMode, amtPairSelected]);

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
      {(selectedPair.ma_short === 0 || selectedPair.kind === "amt") && (
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
          label={tradingAmtMode === "off" ? "Off" : "On"}
          size="small"
          clickable
          color={tradingAmtMode === "off" ? "default" : "primary"}
          variant={tradingAmtMode === "off" ? "outlined" : "filled"}
          onClick={() => {
            setTradingAmtMode(tradingAmtMode === "off" ? "lowkey" : "off");
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
  // Price and Amt chips are ALWAYS clickable. Clicking an already-active
  // Amt/MA chip toggles it off ("unclick") and recovers the normal OHLC
  // price style by falling back to Price/MA5 (pair index 0). Clicking an
  // Amt chip switches the chart to the amt-envelope style with a lowkey
  // OHLC reference.
  const renderPairChip = (
    pair: MovAveSpreadPairSeries,
    idx: number,
  ) => {
    const active = idx === safePairIdx;
    const isAmt = pair.kind === "amt";
    return (
      <Chip
        label={pair.pair_label}
        clickable
        size="small"
        color={active ? "primary" : "default"}
        variant={active ? "filled" : "outlined"}
        onClick={() => {
          // Toggle off an active Amt/MA chip → recover OHLC price style.
          if (active && isAmt) {
            setSelectedPairIdx(0);
          } else {
            setSelectedPairIdx(idx);
          }
        }}
        sx={PERIOD_CHIP_SX}
      />
    );
  };

  return (
    <ChartCard
      title={selectedPair ? selectedPair.pair_label : "MA-Spread"}
      subtitle={subtitle}
      action={
        bollAction ? (
          <Stack direction="row" alignItems="center">
            {bollAction}
            <AnalysisRunButton
              module="mov_ave_spread"
              secType={secType}
              code={code}
              hasData={hasAnalysisData}
              onCompleted={handleAnalysisRunCompleted}
            />
          </Stack>
        ) : (
          <AnalysisRunButton
            module="mov_ave_spread"
            secType={secType}
            code={code}
            hasData={hasAnalysisData}
            onCompleted={handleAnalysisRunCompleted}
          />
        )
      }
    >
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={24} />
        </Box>
      )}
      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}

      {/* Pair chips — Simple MA section (2 rows) + Exponential MA section
          (2 rows) + optional Trading Amt/MA row. Moved to the top of the
          card so the time slider can sit at the bottom. */}
      {!loading && !error && pairs.length > 0 && (
        <Box sx={{ mt: 1, mb: 0.5 }}>
          {/* ---- Simple MA section ---- */}
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ mb: 0.5, display: "block", fontSize: "0.7rem" }}
          >
            Pairs (Simple MA) — click to switch
          </Typography>
          <Box sx={PERIOD_GRID_SX}>
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
            {/* Price row (ma_short = 0): one chip per long-MA column.
                Always clickable — selecting one recovers the normal OHLC
                price style (exits the amt-envelope view). */}
            {LONG_MA_ORDER.map((maLong, col) => {
              const idx = pairIndexMap.get(`price-0-${maLong}`);
              return (
                <Box key={`price-${col}`} sx={{ gridColumn: col + 1 }}>
                  {idx != null && renderPairChip(pairs[idx], idx)}
                </Box>
              );
            })}
            {/* MA5 row (ma_short = 5): no MA5/MA5 pair — col 0 left empty.
                Always clickable. */}
            {LONG_MA_ORDER.map((maLong, col) => {
              if (maLong === 5) {
                return <Box key={`ma5-empty-${col}`} sx={{ gridColumn: col + 1 }} />;
              }
              const idx = pairIndexMap.get(`price-5-${maLong}`);
              return (
                <Box key={`ma5-${col}`} sx={{ gridColumn: col + 1 }}>
                  {idx != null && renderPairChip(pairs[idx], idx)}
                </Box>
              );
            })}
          </Box>

          {/* ---- Exponential MA section ---- */}
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ mt: 1, mb: 0.5, display: "block", fontSize: "0.7rem" }}
          >
            Pairs (Exponential MA) — click to switch
          </Typography>
          <Box sx={PERIOD_GRID_SX}>
            {/* Price/EMA row (ma_short = 0): one chip per long-EMA column. */}
            {LONG_EMA_ORDER.map((emaLong, col) => {
              const idx = pairIndexMap.get(`ema-0-${emaLong}`);
              return (
                <Box key={`ema-price-${col}`} sx={{ gridColumn: col + 1 }}>
                  {idx != null && renderPairChip(pairs[idx], idx)}
                </Box>
              );
            })}
            {/* EMA6/EMA row (ma_short = 6): no EMA6/EMA6 pair — col 0 empty. */}
            {LONG_EMA_ORDER.map((emaLong, col) => {
              if (emaLong === 6) {
                return <Box key={`ema6-empty-${col}`} sx={{ gridColumn: col + 1 }} />;
              }
              const idx = pairIndexMap.get(`ema-6-${emaLong}`);
              return (
                <Box key={`ema6-${col}`} sx={{ gridColumn: col + 1 }}>
                  {idx != null && renderPairChip(pairs[idx], idx)}
                </Box>
              );
            })}
          </Box>

          {/* ---- Trading Amt/MA section (optional, toggle-driven) ---- */}
          {tradingAmtMode !== "off" && (
            <Box sx={{ mt: 1 }}>
              <Box sx={PERIOD_GRID_SX}>
                {/* Row label */}
                <Box sx={{ gridColumn: "1 / -1", mb: 0.5 }}>
                  <Typography
                    variant="caption"
                    component="span"
                    sx={{
                      fontSize: "0.65rem",
                      color: amtPairSelected ? "primary.main" : "text.secondary",
                      fontWeight: amtPairSelected ? 700 : 400,
                    }}
                  >
                    Trading Amt/MA
                  </Typography>
                </Box>
                {LONG_MA_ORDER.map((maLong, col) => {
                  const idx = pairIndexMap.get(`amt--1-${maLong}`);
                  return (
                    <Box key={`amt-${col}`} sx={{ gridColumn: col + 1 }}>
                      {idx != null && renderPairChip(pairs[idx], idx)}
                    </Box>
                  );
                })}
              </Box>
            </Box>
          )}

          {/* ---- Rolling-OHLC window buttons ----
              Row label ("OHLC Window") spans the full grid width on its own
              row — same style as the Trading Amt/MA label — with the window
              buttons on a new row below it. The buttons keep the
              period-column alignment of the pair chips: 20d sits under the
              MA20 column, …, 1275d in the last column. */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            {/* Row label */}
            <Box sx={{ gridColumn: "1 / -1", mb: 0.5 }}>
              <Typography
                variant="caption"
                component="span"
                sx={{
                  fontSize: "0.65rem",
                  color: ohlcWindow != null ? "primary.main" : "text.secondary",
                  fontWeight: ohlcWindow != null ? 700 : 400,
                }}
              >
                OHLC Window
              </Typography>
            </Box>
            {OHLC_WINDOWS.map((w, col) => (
              <Chip
                key={w}
                label={`${w}d`}
                size="small"
                clickable
                color={ohlcWindow === w ? "primary" : "default"}
                variant={ohlcWindow === w ? "filled" : "outlined"}
                onClick={() =>
                  setOhlcWindow((prev) => (prev === w ? null : w))
                }
                sx={{ gridColumn: col + 2, ...PERIOD_CHIP_SX }}
              />
            ))}
          </Box>
          {ohlcWindow != null && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              click a date on the chart to draw roof/floor trendlines
              {ohlcClickIdx != null && firstPairRows[ohlcClickIdx]
                ? ` · anchor ${firstPairRows[ohlcClickIdx].date}`
                : ""}
            </Typography>
          )}

          {/* ---- Market Hype buttons (multi-select) ----
              Same layout and chip style as the OHLC Window row: full-width
              row label on its own line, then the check-in window buttons
              (5/20/60/120/255d) aligned with the pair chips' MA columns —
              each window doubles as an episode-span BUCKET (its own length
              as the minimum, the next window as the exclusive maximum), so
              each calendar turmoil lands in exactly the bucket matching
              its length. Clicking a button toggles that window's light
              purple shading of the chart's hyped date periods — MULTIPLE
              windows can be enabled at once and their shades overlap
              (stacking darker where they coincide). The latest date's
              hyped state is reported in the caption below, not on the
              buttons. */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            {/* Row label */}
            <Box sx={{ gridColumn: "1 / -1", mb: 0.5 }}>
              <Typography
                variant="caption"
                component="span"
                sx={{
                  fontSize: "0.65rem",
                  color: hypeWindows.length > 0 ? "primary.main" : "text.secondary",
                  fontWeight: hypeWindows.length > 0 ? 700 : 400,
                }}
              >
                Market Hype
              </Typography>
            </Box>
            {HYPE_WINDOWS.map((w, col) => (
              <Chip
                key={w}
                label={`${w}d`}
                size="small"
                clickable
                disabled={!hasHypeData}
                color={hypeWindows.includes(w) ? "primary" : "default"}
                variant={hypeWindows.includes(w) ? "filled" : "outlined"}
                onClick={() => toggleHypeWindow(w)}
                sx={{ gridColumn: col + 1, ...PERIOD_CHIP_SX }}
              />
            ))}
          </Box>
          {enabledHypeWindows.map((w) => {
            const st = hypeWindowStats.get(w);
            return (
              <Typography
                key={w}
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
              >
                light purple shading marks hyped periods ({w}d bucket ·
                episodes spanning {w}-{hypeBucketUpper(w)} trading days ·
                trading amt + volatility check-ins vs their centered 20y
                (±10y) percentiles){st ? ` · ${st.count} hyped ${
                  st.count === 1 ? "day" : "days"
                }` +
                  (st.amtDays != null && st.stdDays != null
                    ? ` (amt ${st.amtDays} · σ ${st.stdDays})`
                    : "") +
                  (st.lastDate ? ` · last ${st.lastDate}` : "")
                : ""}
                {latestHypeFlags.get(w) === true ? " · currently hyped" : ""}
              </Typography>
            );
          })}
          {!hasHypeData && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              no hype data yet — run{" "}
              <code>python -m analyze.mov_ave_spread</code> to build
              analysis.mov_ave_market_hypes
            </Typography>
          )}

          {/* ---- High/Low Streaks buttons (nested, single-select) ----
              Same grid and chip style as the other button rows: the row
              label spans the full width, then the first layer holds the
              band lookback periods (255/500/750/1275d) aligned with the
              OHLC row's matching columns. Clicking a period EXPANDS the
              second layer — band tightness pcts (1/5/10%) on the row
              beneath — and clicking the active period collapses it again.
              Selecting a pct fills the LATEST date's trailing-period window
              with its top/bottom pct% price zones (light purple above
              high_val, light yellow below low_val) and draws that combo's
              break streaks darker inside; clicking a chart date anchors
              the window to the trailing rows before that date. */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            {/* Row label */}
            <Box sx={{ gridColumn: "1 / -1", mb: 0.5 }}>
              <Typography
                variant="caption"
                component="span"
                sx={{
                  fontSize: "0.65rem",
                  color: streakPeriod != null ? "primary.main" : "text.secondary",
                  fontWeight: streakPeriod != null ? 700 : 400,
                }}
              >
                High/Low Streaks
              </Typography>
            </Box>
            {/* Layer 1 — periods, columns 5-8 (aligned with the OHLC row's
                255d/500d/750d/1275d buttons). */}
            {HIGH_LOW_STREAK_PERIODS.map((w, col) => (
              <Chip
                key={w}
                label={`${w}d`}
                size="small"
                clickable
                disabled={!hasStreakData}
                color={streakPeriod === w ? "primary" : "default"}
                variant={streakPeriod === w ? "filled" : "outlined"}
                onClick={() => toggleStreakPeriod(w)}
                sx={{ gridColumn: col + 5, ...PERIOD_CHIP_SX }}
              />
            ))}
            {/* Layer 2 — pcts, expanded beneath the periods when one is
                active (columns 5-7, under the first three periods). */}
            {streakPeriod != null &&
              HIGH_LOW_STREAK_PCTS.map((p, col) => (
                <Chip
                  key={p}
                  label={`${p}%`}
                  size="small"
                  clickable
                  color={streakPct === p ? "primary" : "default"}
                  variant={streakPct === p ? "filled" : "outlined"}
                  onClick={() => toggleStreakPct(p)}
                  sx={{ gridColumn: col + 5, ...PERIOD_CHIP_SX }}
                />
              ))}
          </Box>
          {streakPeriod != null && streakPct == null && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              pick a band tightness (pct) to shade break streaks
            </Typography>
          )}
          {streakPeriod != null && streakPct != null && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              light purple/yellow = trailing {streakPeriod}d top/bottom{" "}
              {streakPct}% zones
              {streakWin ? ` (${streakWin.startDate} → ${streakWin.endDate})` : ""},
              darker = break streaks (each shaded over its own span ·
              ≤5-day in-band gaps bridged) ·{" "}
              {longStreaks
                ? (longStreaks.high.length > 0 || longStreaks.low.length > 0
                    ? [
                        longStreaks.high.length > 0
                          ? `high ${longStreaks.high.length} streak${longStreaks.high.length === 1 ? "" : "s"} · ${longStreaks.high.reduce((a, s) => a + s.days, 0)}d · peak ${fmtNum(longStreaks.high.reduce((a, s) => Math.max(a, s.extreme), -Infinity))} · ${longStreaks.high.slice(0, 2).map((s) => `${s.startDate.slice(2)}→${s.endDate.slice(2)}`).join(", ")}${longStreaks.high.length > 2 ? `, +${longStreaks.high.length - 2} more` : ""}`
                          : "high none",
                        longStreaks.low.length > 0
                          ? `low ${longStreaks.low.length} streak${longStreaks.low.length === 1 ? "" : "s"} · ${longStreaks.low.reduce((a, s) => a + s.days, 0)}d · trough ${fmtNum(longStreaks.low.reduce((a, s) => Math.min(a, s.extreme), Infinity))} · ${longStreaks.low.slice(0, 2).map((s) => `${s.startDate.slice(2)}→${s.endDate.slice(2)}`).join(", ")}${longStreaks.low.length > 2 ? `, +${longStreaks.low.length - 2} more` : ""}`
                          : "low none",
                      ].join(" · ")
                    : "no streaks in this window")
                : "no window"}
              {streakWin
                ? ohlcClickIdx != null
                  ? ` · anchored to ${streakWin.endDate} (click again to clear)`
                  : " · click a chart date to anchor the window"
                : ""}
            </Typography>
          )}
          {!hasStreakData && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              no streak data yet — run{" "}
              <code>python -m analyze.mov_ave_spread</code> to build
              analysis.mov_ave_high_low_pct_streaks
            </Typography>
          )}
        </Box>
      )}

      {!loading && !error && selectedPair && selectedPair.rows.length > 0 && (
        <EChart
          option={
            amtPairSelected
              ? buildAmtEnvelopeOption({
                  pair: selectedPair,
                  themeMode,
                  ohlcMode,
                  bollingerK,
                  hypeWindows,
                  hypeEpisodes: chartData?.hypeEpisodes ?? null,
                  longStreaks,
                  streakPeriod,
                  streakPct,
                  streakAnchorIdx: ohlcClickIdx,
                })
              : buildPairOption({
                  pair: selectedPair,
                  themeMode,
                  bollingerK,
                  tradingAmtMode,
                  hoveredIdx,
                  ohlcMode,
                  ohlcWindow,
                  ohlcClickIdx,
                  ohlcRows: chartData?.ohlc ?? null,
                  hypeWindows,
                  hypeEpisodes: chartData?.hypeEpisodes ?? null,
                  longStreaks,
                  streakPeriod,
                  streakPct,
                  streakAnchorIdx: ohlcClickIdx,
                })
          }
          height={420}
          onEvents={chartEvents}
          onCanvasClick={handleCanvasClick}
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

      {/* ---- 2nd plot: forecast bucket table (analysis_forecasts) ----
          Dropdown beneath the spread chart selects which bucket family to
          show — RSI extreme-percentile buckets (mov_rsi), Bollinger
          breach buckets (mov_std), N-day price-return extreme-percentile
          buckets (mov_gap), σ-speed × 量比-z state cells (px_vol) or
          margin-buy intensity z states (margin_ratio).
          Selecting one mounts ForecastTable,
          which lists the latest 12 stat_months of this code's buckets
          (config + is_market_hyped [+ excess/mean-t-z cols] → forecast
          results). */}
      {!loading && !error && (
        <Box sx={{ mt: 1.5 }}>
          <Stack direction="row" alignItems="center" spacing={1}>
            <Typography
              variant="caption"
              sx={{
                fontSize: "0.65rem",
                color: forecastKind ? "primary.main" : "text.secondary",
                fontWeight: forecastKind ? 700 : 400,
              }}
            >
              Forecast
            </Typography>
            <Select
              size="small"
              value={forecastKind}
              onChange={(e) => setForecastKind(e.target.value as ForecastKind | "")}
              sx={{
                height: 26,
                fontSize: "0.7rem",
                "& .MuiSelect-select": { py: 0.25, px: 1, fontSize: "0.7rem" },
              }}
            >
              <MenuItem value="" sx={{ fontSize: "0.7rem", py: 0.25 }}>
                off
              </MenuItem>
              <MenuItem value="mov_rsi" sx={{ fontSize: "0.7rem", py: 0.25 }}>
                RSI extremes (mov_rsi)
              </MenuItem>
              <MenuItem value="mov_std" sx={{ fontSize: "0.7rem", py: 0.25 }}>
                Bollinger breach (mov_std)
              </MenuItem>
              <MenuItem value="mov_gap" sx={{ fontSize: "0.7rem", py: 0.25 }}>
                N-day return extremes (mov_gap)
              </MenuItem>
              <MenuItem value="px_vol" sx={{ fontSize: "0.7rem", py: 0.25 }}>
                Price×volume states (px_vol)
              </MenuItem>
              <MenuItem value="margin_ratio" sx={{ fontSize: "0.7rem", py: 0.25 }}>
                Margin-buy ratio states (margin_ratio)
              </MenuItem>
            </Select>
            {forecastKind && (
              <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.62rem" }}>
                tick the header dropdowns to filter buckets (month header = month selector) ·
                mean/high/low forward change + P(&gt;1% reversal)
              </Typography>
            )}
          </Stack>
          {forecastKind && (
            <Box sx={{ mt: 0.75 }}>
              <ForecastTable code={code} secType={secType} kind={forecastKind} />
            </Box>
          )}
        </Box>
      )}
    </ChartCard>
  );
}
