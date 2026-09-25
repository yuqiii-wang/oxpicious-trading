/**
 * MaSpreadPanel — one card per code: pair chips + two-curve chart with
 * green/red fill between them + date-range slider + Bollinger envelope.
 *
 * Each panel renders (top → bottom):
 *   1. 9 pair chips arranged as a 2-row grid aligned by long MA — the Price
 *      row (Price/MA5 … Price/MA255) above the MA5 row (MA5/MA20 …
 *      MA5/MA255, with the MA5 column empty). Clicking a chip selects
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
 *   5. High/Low Streaks section beneath the OHLC Window row — NESTED
 *      buttons: the first layer holds the band lookback periods
 *      (60/120/255/500/750/1275, period-column aligned with the OHLC
 *      row);
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
 *   6. Date-range slider at the bottom of the plot — drives all 9 pairs
 *      (they share one date axis).
 *   7. Px-Vol States section beneath the High/Low Streaks row — the
 *      trading amt × price-change state family (the
 *      analysis_forecasts.px_vol_state engine, recomputed client-side from
 *      the chart rows — the DB stores bucket aggregates only): layer 1 =
 *      price speed (sharp/slow/flat rise/drop), layer 2 = amount state
 *      (increasing/flat/decreasing). Picking one of each shades the
 *      matching dates — green rise / red drop / gray flat, shade depth by
 *      combo strength (sharp × heavy = darkest, e.g. rising price +
 *      increasing amount = strong growth).
 *
 * Fetches its own chart data on mount via fetchMovAveSpreadChart(code, secType)
 * — the DEFAULT load carries just the 18 Simple-MA + EMA pair series and the
 * shared per-date tooltip metrics. The other metric groups load ON DEMAND:
 * the first pick of an Amt/MA chip, an OHLC Window button, a High/Low
 * Streaks period, or a Px-Vol state fetches that group via
 * fetchMovAveSpreadExtras (a regime button's first pick fetches the spans
 * via fetchMarketRegimeSpans); while any group is in flight every control
 * in the panel is disabled and the plot freezes under a translucent
 * spinner backdrop (BaseChart freezeOnLoading).
 *
 * Every button-group title carries a small info mark (SectionLabel) that
 * opens a popover with the group's description — the texts live in
 * groupDescriptions.ts, keyed by group id.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import {
  Box,
  Chip,
  MenuItem,
  Select,
  Stack,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import OhlcModeToggle from "@/components/OhlcModeToggle";
import AnalysisRunButton from "@/components/AnalysisRunButton";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import { useAiAskAddon } from "@/shared/ai-ask";
import type { AiAskSpec } from "@/shared/ai-ask";
import type { ECharts } from "echarts";
import { UP_COLOR } from "@/theme/chart-palette";
import {
  REGIME_ACCENT_COLORS,
  REGIME_LABELS,
  REGIME_SHADE_COLORS,
  ALL_REGIMES,
  regimeSpansToMarkArea,
} from "@/shared/charts/regimeBands";
import type { MarketRegime, MarketRegimeSpans } from "@shared/types";
import { fmtNum, fmtPct } from "@/lib/series";
import {
  fetchMovAveSpreadChart,
  fetchMovAveSpreadExtras,
  fetchMarketRegimeSpans,
  invalidateCacheForPrefix,
  invalidateCacheForUrl,
} from "@/lib/api-client";
import type { OhlcMode } from "@/lib/ohlc";
import type {
  MovAveSpreadChartResponse,
  MovAveSpreadPairSeries,
  MovAveSpreadAmtRow,
  MovAveSpreadOhlcRow,
  MovAveSpreadHighLowStreak,
  MovAveSpreadPriceVsAmtDay,
  MovAveSpreadMetric,
} from "@shared/types";
import type { PanelProps } from "./types";
import { SectionLabel } from "./SectionLabel";
import {
  OHLC_WINDOWS,
  HIGH_LOW_STREAK_PERIODS,
  HIGH_LOW_STREAK_PCTS,
} from "./constants";
import { buildPairOption, buildAmtEnvelopeOption, shortLabel, type TradingAmtMode } from "./chartOption";
import {
  computePxVolStates,
  pxVolMatchRuns,
  pxVolRunsToMarkArea,
  pxVolShadeColor,
  pxVolAccentColor,
  pxVolReading,
  PX_VOL_SPEED_OPTIONS,
  PX_VOL_VOL_OPTIONS,
  type PxVolSpeed,
  type PxVolVolState,
} from "./chartOption";
import {
  computeBreakStreaks,
  computeStreakBandWindow,
  type LongBandStreak,
} from "@/shared/charts/streakBands";

/** Bollinger multiplier options for the top-right dropdown (0.0 … 3.0, step 0.5).
 *  0.0 = band hidden; 2.0 = standard Bollinger. */
const BOLL_K_OPTIONS = [0, 0.5, 1, 1.5, 2, 2.5, 3];

/**
 * The on-demand metric groups (MovAveSpreadMetric) → the ExtrasState key
 * they populate. Each group feeds one control section and is fetched the
 * first time one of its buttons is picked:
 *   amt → the 5 Amt/MA chips · ohlc → the OHLC Window row ·
 *   streaks → the High/Low Streaks row · pxvol → the Px-Vol States row.
 */
const METRIC_STATE_KEY: Record<MovAveSpreadMetric, "amtPairs" | "ohlc" | "highLowStreaks" | "priceVsAmt"> = {
  amt: "amtPairs",
  ohlc: "ohlc",
  streaks: "highLowStreaks",
  pxvol: "priceVsAmt",
};

/** On-demand metric groups loaded so far (per code + refresh). */
interface ExtrasState {
  amtPairs?: MovAveSpreadPairSeries[];
  ohlc?: MovAveSpreadOhlcRow[];
  highLowStreaks?: MovAveSpreadHighLowStreak[];
  priceVsAmt?: MovAveSpreadPriceVsAmtDay[];
}

/**
 * Long-MA column order used to lay out the 9 pair chips as a 2-row grid
 * aligned by long MA (so Price/MA60 and MA5/MA60 share one column). The
 * MA5 row leaves the MA5 column empty (no MA5/MA5 pair exists).
 */
const LONG_MA_ORDER = [5, 20, 60, 120, 255] as const;

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

export function MaSpreadPanel({ code, name, secType }: PanelProps) {
  // Reactive light/dark theme — single source of truth for every option
  // builder call below (no prop drilling).
  const themeMode = useChartThemeMode();

  // ---- Chart data ---------------------------------------------------------
  const [chartData, setChartData] = useState<MovAveSpreadChartResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Bumped by the per-security AnalysisRunButton after a rebuild run —
  // retriggers the chart fetch (the cache entry is invalidated first in
  // the completion handler).
  const [refreshKey, setRefreshKey] = useState(0);

  // ---- On-demand metric groups (see METRIC_STATE_KEY) ---------------------
  // The default chart load carries just the 18 pair series + the shared
  // tooltip metrics. The Amt/MA pairs, OHLC extrema, streak rows, and
  // px-vol states load lazily: picking a section's button fetches its
  // group, and while ANY group is in flight every control is disabled and
  // the plot shows the shared spinner (see extrasLoading).
  const [extras, setExtras] = useState<ExtrasState>({});
  const [loadingMetrics, setLoadingMetrics] = useState<MovAveSpreadMetric[]>([]);
  const [metricError, setMetricError] = useState<string | null>(null);
  const extrasLoading = loadingMetrics.length > 0;
  const amtLoaded = extras.amtPairs != null;

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
  // drop the cached chart + extras responses, then bump the refresh key.
  const handleAnalysisRunCompleted = useCallback(() => {
    invalidateCacheForUrl(
      `/api/analysis/mov-ave-spread/chart?code=${code}&sec_type=${secType}`,
    );
    invalidateCacheForPrefix("/api/analysis/mov-ave-spread/extras");
    setRefreshKey((k) => k + 1);
  }, [code, secType]);

  // Fetch ONE on-demand metric group (no-op when already loaded or in
  // flight). Resolves after the group lands — callers chain their button's
  // state change onto it so the pick applies the moment the spinner stops.
  const ensureMetric = useCallback(
    (metric: MovAveSpreadMetric): Promise<void> => {
      if (extras[METRIC_STATE_KEY[metric]] != null) return Promise.resolve();
      setLoadingMetrics((prev) =>
        prev.includes(metric) ? prev : [...prev, metric],
      );
      setMetricError(null);
      return fetchMovAveSpreadExtras(code, secType, [metric])
        .then((d) => {
          setExtras((prev) => ({ ...prev, [METRIC_STATE_KEY[metric]]: d[METRIC_STATE_KEY[metric]] }));
        })
        .catch((e: Error) => {
          setMetricError(e.message);
        })
        .finally(() => {
          setLoadingMetrics((prev) => prev.filter((m) => m !== metric));
        });
    },
    [extras, code, secType],
  );

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

  // Enabled rolling-OHLC window (trading days) — null = off. Selected via
  // the OHLC-window button row beneath the Trading Amt/MA section.
  const [ohlcWindow, setOhlcWindow] = useState<number | null>(null);

  // Clicked chart date index (into the full rows — shared date axis). The
  // roof/floor trendlines of the enabled window are drawn to (and stop at)
  // this date. Clicking the same date again clears it.
  const [ohlcClickIdx, setOhlcClickIdx] = useState<number | null>(null);

  // High/Low Streaks nested buttons: layer 1 = band lookback period
  // (trading rows, null = row off), layer 2 = band tightness pct (percent,
  // null = no shading yet). Selecting a period expands the pct layer;
  // clicking the active period again collapses it (and clears the pct).
  const [streakPeriod, setStreakPeriod] = useState<number | null>(null);
  const [streakPct, setStreakPct] = useState<number | null>(null);

  // Px-Vol States nested buttons (trading amt × price change): layer 1 =
  // price speed (sharp/slow/flat rise/drop), layer 2 = trading-amount state
  // (increasing/flat/decreasing). Single-select per row; BOTH rows must be
  // picked before the chart shades the matching dates. The shades' color
  // strength follows the combo's weight (sharp × heavy = darkest).
  const [pxVolSpeed, setPxVolSpeed] = useState<PxVolSpeed | null>(null);
  const [pxVolVol, setPxVolVol] = useState<PxVolVolState | null>(null);

  const togglePxVolSpeed = useCallback((s: PxVolSpeed) => {
    setPxVolSpeed((prev) => (prev === s ? null : s));
  }, []);

  const togglePxVolVol = useCallback((v: PxVolVolState) => {
    setPxVolVol((prev) => (prev === v ? null : v));
  }, []);

  // Market Regimes buttons (stats.market_regimes): multi-select among ALL
  // regimes — calm included (its grey shade marks the market's calm
  // stretches; the other charts keep calm as the never-shaded background
  // via the shared SHADED_REGIMES). The first pick fetches the code's
  // contiguous same-regime spans once (GET /api/analysis/market-regimes)
  // and caches them per code.
  const [regimePicks, setRegimePicks] = useState<Set<MarketRegime>>(new Set());
  const [regimeSpans, setRegimeSpans] = useState<Partial<MarketRegimeSpans> | null>(null);
  const [regimesLoading, setRegimesLoading] = useState(false);

  // Tracks the panel's current (secType, code) so a slow regime-span
  // response for a previous code never lands after a switch.
  const regimeCodeKeyRef = useRef(`${secType}:${code}`);
  useEffect(() => {
    regimeCodeKeyRef.current = `${secType}:${code}`;
  }, [code, secType]);

  // Picking a regime fetches the spans ONCE (imperatively — an effect that
  // both sets and depends on its own loading flag would kill its in-flight
  // fetch on the very re-render it triggers, leaving the row stuck
  // disabled with the shades never drawn).
  const toggleRegime = useCallback(
    (r: MarketRegime) => {
      setRegimePicks((prev) => {
        const next = new Set(prev);
        if (next.has(r)) next.delete(r);
        else next.add(r);
        return next;
      });
      if (regimeSpans != null || regimesLoading) return;
      setRegimesLoading(true);
      const reqKey = `${secType}:${code}`;
      fetchMarketRegimeSpans(code, secType)
        .then((d) => {
          if (regimeCodeKeyRef.current === reqKey) setRegimeSpans(d.spans ?? {});
        })
        .catch(() => {
          if (regimeCodeKeyRef.current === reqKey) setRegimeSpans({});
        })
        .finally(() => {
          if (regimeCodeKeyRef.current === reqKey) setRegimesLoading(false);
        });
    },
    [code, secType, regimeSpans, regimesLoading],
  );

  useEffect(() => {
    setRegimePicks(new Set());
    setRegimeSpans(null);
    setRegimesLoading(false);
  }, [code, secType]);

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

  // ---- Click-through handlers: the FIRST pick of an unloaded section
  // loads its metric group first and applies the pick as soon as the fetch
  // resolves; later picks toggle directly. While the fetch is in flight
  // the freeze disables every control, so at most one load runs at a time.
  const handleOhlcWindowClick = useCallback(
    (w: number) => {
      if (extras.ohlc == null) {
        void ensureMetric("ohlc").then(() =>
          setOhlcWindow((prev) => (prev === w ? null : w)),
        );
      } else {
        setOhlcWindow((prev) => (prev === w ? null : w));
      }
    },
    [ensureMetric, extras.ohlc],
  );

  const handleStreakPeriodClick = useCallback(
    (w: number) => {
      if (extras.highLowStreaks == null) {
        void ensureMetric("streaks").then(() => toggleStreakPeriod(w));
      } else {
        toggleStreakPeriod(w);
      }
    },
    [ensureMetric, extras.highLowStreaks, toggleStreakPeriod],
  );

  const handlePxVolSpeedClick = useCallback(
    (s: PxVolSpeed) => {
      if (extras.priceVsAmt == null) {
        void ensureMetric("pxvol").then(() => togglePxVolSpeed(s));
      } else {
        togglePxVolSpeed(s);
      }
    },
    [ensureMetric, extras.priceVsAmt, togglePxVolSpeed],
  );

  const handlePxVolVolClick = useCallback(
    (v: PxVolVolState) => {
      if (extras.priceVsAmt == null) {
        void ensureMetric("pxvol").then(() => togglePxVolVol(v));
      } else {
        togglePxVolVol(v);
      }
    },
    [ensureMetric, extras.priceVsAmt, togglePxVolVol],
  );

  // Fetch chart data on mount and whenever the code/sec_type changes. The
  // on-demand metric groups reset with it — a new code starts from the
  // default pairs-only load.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setExtras({});
    setLoadingMetrics([]);
    setMetricError(null);
    fetchMovAveSpreadChart(code, secType)
      .then((d) => {
        if (cancelled) return;
        setChartData(d);
        setSelectedPairIdx(0);
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

  // The full pairs list — the 18 default series plus the 5 Amt/MA series
  // once the "amt" extras group has loaded (no slicing — the chart's
  // in-chart dataZoom handles viewport control). Used for the pair chips,
  // the pair index lookup, and the chart option builder.
  const pairs = useMemo(
    () => [...(chartData?.pairs ?? []), ...(extras.amtPairs ?? [])],
    [chartData, extras.amtPairs],
  );

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

  // ---- High/Low Streaks data ("streaks" extras group — loaded on demand
  // the first time a High/Low Streaks period button is picked) ----
  // FLAT per-streak list across ALL (period, pctType) combos — the nested
  // buttons select a combo, and the WINDOW-CONFINED subset is merged per
  // DB band-break streak rows (analysis.mov_ave_high_low_pct_streaks,
  // tested against each month's OWN moving band) — NOT used for shading
  // (the break bands are detected client-side vs the anchor window's
  // static edge, see longStreaks below); they only gate the buttons'
  // availability caption.
  const highLowStreaks = extras.highLowStreaks ?? null;
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

  // ---- Px-Vol States data (the analysis.mov_ave_price_vs_amt
  // registry — the px_vol family's DATE-LEVEL source of truth; the
  // forecast buckets in analysis_forecasts.px_vol_state store
  // aggregates only) ----
  // Per-date (speed, vol) state served by the chart endpoint
  // (chartData.priceVsAmt). Falls back to a client-side replication of
  // the registry's computation (computePxVolStates) for cached
  // responses that predate the field. Computed lazily (either Px-Vol
  // button picked).
  const pxVolStates = useMemo(() => {
    if (pxVolSpeed == null && pxVolVol == null) return null;
    const dbDays = extras.priceVsAmt;
    if (dbDays != null) {
      const byDate = new Map(dbDays.map((d) => [d.date, d]));
      return firstPairRows.map((r) => {
        const d = byDate.get(r.date);
        return d != null ? { speed: d.speed, vol: d.vol } : null;
      });
    }
    return computePxVolStates(
      firstPairRows.map((r) => r.short_value),
      firstPairRows.map((r) => r.trading_amount),
    );
  }, [chartData, extras.priceVsAmt, firstPairRows, pxVolSpeed, pxVolVol]);

  // The selected combo's consecutive matched runs + the chart overlay
  // (legend label + markArea rects shaded by the combo's strength color).
  const pxVolRuns = useMemo(() => {
    if (pxVolSpeed == null || pxVolVol == null || pxVolStates == null) return [];
    return pxVolMatchRuns(
      firstPairRows.map((r) => r.date),
      pxVolStates,
      pxVolSpeed,
      pxVolVol,
    );
  }, [firstPairRows, pxVolStates, pxVolSpeed, pxVolVol]);

  const pxVolShade = useMemo(() => {
    if (pxVolSpeed == null || pxVolVol == null || pxVolRuns.length === 0) return null;
    const speedOpt = PX_VOL_SPEED_OPTIONS.find((o) => o.key === pxVolSpeed);
    const volOpt = PX_VOL_VOL_OPTIONS.find((o) => o.key === pxVolVol);
    const label = `PxVol(${speedOpt?.label ?? pxVolSpeed}·${volOpt?.label ?? pxVolVol})`;
    return {
      label,
      accent: pxVolAccentColor(pxVolSpeed),
      data: pxVolRunsToMarkArea(pxVolRuns, pxVolShadeColor(pxVolSpeed, pxVolVol)),
    };
  }, [pxVolSpeed, pxVolVol, pxVolRuns]);

  // The picked regimes' span overlays (one legend series each — the
  // regimeBands palette's per-regime shade color, calm's grey included).
  const regimeShades = useMemo(() => {
    if (regimePicks.size === 0 || regimeSpans == null) return [];
    const out: Array<{ label: string; data: ReturnType<typeof regimeSpansToMarkArea>; accent: string }> = [];
    for (const r of ALL_REGIMES) {
      if (!regimePicks.has(r)) continue;
      const spans = regimeSpans[r] ?? [];
      out.push({
        label: `Regime(${REGIME_LABELS[r]})`,
        accent: REGIME_ACCENT_COLORS[r],
        data: regimeSpansToMarkArea(spans, REGIME_SHADE_COLORS[r]),
      });
    }
    return out;
  }, [regimePicks, regimeSpans]);

  // Clamp selectedPairIdx to valid range.
  const safePairIdx = Math.min(selectedPairIdx, Math.max(0, pairs.length - 1));
  const selectedPair = pairs[safePairIdx];
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

  // Chart option for the selected pair — null while loading / on error / when
  // the pair has no rows; BaseChart then renders the shared spinner / error
  // Alert / empty placeholder instead of the chart. Memoized so the AI Ask
  // plot info derives from the same object the chart renders.
  const chartOption = useMemo(
    () =>
      !loading && !error && selectedPair && selectedPair.rows.length > 0
        ? amtPairSelected
          ? buildAmtEnvelopeOption({
              pair: selectedPair as Omit<MovAveSpreadPairSeries, "rows"> & { rows: MovAveSpreadAmtRow[] },
              shared: chartData?.shared ?? null,
              themeMode,
              ohlcMode,
              bollingerK,
              longStreaks,
              streakPeriod,
              streakPct,
              streakAnchorIdx: ohlcClickIdx,
              pxVolShade,
              regimeShades,
            })
          : buildPairOption({
              pair: selectedPair,
              shared: chartData?.shared ?? null,
              themeMode,
              bollingerK,
              tradingAmtMode,
              ohlcMode,
              ohlcWindow,
              ohlcClickIdx,
              ohlcRows: extras.ohlc ?? null,
              longStreaks,
              streakPeriod,
              streakPct,
              streakAnchorIdx: ohlcClickIdx,
              pxVolShade,
              regimeShades,
            })
        : null,
    [
      loading,
      error,
      selectedPair,
      amtPairSelected,
      chartData,
      extras.ohlc,
      themeMode,
      bollingerK,
      tradingAmtMode,
      ohlcMode,
      ohlcWindow,
      ohlcClickIdx,
      longStreaks,
      streakPeriod,
      streakPct,
      pxVolShade,
      regimeShades,
    ],
  );

  // ---- AI Ask — wire the OUTER card (the chart body is a bare BaseChart).
  // State carries every in-plot control's CURRENT value so the modal / LLM
  // describe the view on screen.
  const maAiAskRef = useRef<ECharts | null>(null);
  const shortName = selectedPair ? shortLabel(selectedPair.ma_short, selectedPair.kind) : "";
  const longName = selectedPair
    ? selectedPair.kind === "ema"
      ? `EMA${selectedPair.ma_long}`
      : `MA${selectedPair.ma_long}`
    : "";
  const streakLabel =
    streakPeriod != null && streakPct != null ? `Streak(${streakPeriod}d·${streakPct}%)` : "";
  // Primitive (not the firstPairRows array) so the spec memo below stays
  // identity-stable.
  const anchorDate =
    ohlcClickIdx != null && firstPairRows[ohlcClickIdx]
      ? firstPairRows[ohlcClickIdx].date
      : undefined;
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        `MA-spread study for ${code} — pair ${selectedPair?.pair_label ?? "—"}: short curve vs ` +
        "long MA, green fill when short > long, red when short < long; tooltips report each " +
        "series' slope and curvature. Control overlays: Bollinger ±kσ envelope on the long " +
        "MA, click-anchored rolling High/Low trendlines (roof/floor), break-streak zones, " +
        "Px-Vol state shading (price speed × amount state). % mode: OHLC + MAs rebased to " +
        "% change from the first valid close; Amt pairs switch to an amount-envelope view " +
        "with a lowkey price reference. Indication: green fill = momentum regime long; " +
        "roof/floor breach = breakout; a deep streak = stretched — fade-prone.",
      instruments: [{ code, name: name || undefined }],
      series: amtPairSelected
        ? [
            { name: "Price", description: "lowkey (high+low)/2 close reference for the amt-envelope view" },
            { name: "Amt Above", unit: "亿", description: "daily trading amount at/above the selected Amt MA" },
            { name: "Amt Below", unit: "亿", description: "daily trading amount below the selected Amt MA" },
            ...[5, 20, 60, 120, 255].map((w) => ({
              name: `Amt MA${w}`,
              unit: "亿",
              description: `${w}-day trading-amount moving average (selected window emphasized)`,
            })),
          ]
        : [
            { name: shortName, description: "the pair's short leg (price or short MA/EMA)" },
            { name: longName, description: "the pair's long moving average" },
            { name: "Amt Up", unit: "亿", description: "daily trading amount on up days (lowkey bars)" },
            { name: "Amt Down", unit: "亿", description: "daily trading amount on down days (lowkey bars)" },
            { name: `Upper (+${bollingerK}σ)`, description: "long MA + k×σ Bollinger band edge" },
            { name: `Lower (−${bollingerK}σ)`, description: "long MA − k×σ Bollinger band edge" },
            ...(ohlcWindow != null
              ? [
                  { name: `High(${ohlcWindow}d)`, description: "rolling OHLC window high" },
                  { name: `Low(${ohlcWindow}d)`, description: "rolling OHLC window low" },
                  { name: `Roof(${ohlcWindow}d)`, description: "trendline through the window's top+2nd highs, stopping at the anchor date" },
                  { name: `Floor(${ohlcWindow}d)`, description: "trendline through the window's top+2nd lows, stopping at the anchor date" },
                ]
              : []),
            ...(streakLabel
              ? [
                  { name: `High ${streakLabel}`, description: "merged span of closes above the anchor window's static top pct zone" },
                  { name: `Low ${streakLabel}`, description: "merged span of closes below the anchor window's static bottom pct zone" },
                ]
              : []),
            ...(pxVolShade ? [{ name: pxVolShade.label, description: "dates matching the selected price-speed × amount-state combo" }] : []),
          ],
      state: {
        pair: selectedPair?.pair_label ?? "—",
        ohlc_mode: ohlcMode === "percentage" ? "% (rebased to first valid close)" : "absolute",
        bollinger_k: bollingerK,
        trading_amt: tradingAmtMode,
        ohlc_window: ohlcWindow ?? "off",
        anchor_date: anchorDate ?? "none",
        streak_combo: streakLabel || "off",
        px_vol: pxVolShade?.label ?? "off",
      },
      notes: [
        "All 9 pairs share one date axis; the in-chart dataZoom windows the view.",
        "Break streaks are detected client-side against the anchor window's static band edge (≤5-day in-band gaps bridged).",
      ],
    }),
    [
      code,
      name,
      selectedPair,
      amtPairSelected,
      shortName,
      longName,
      bollingerK,
      ohlcWindow,
      streakLabel,
      pxVolShade,
      ohlcMode,
      tradingAmtMode,
      anchorDate,
    ],
  );
  const aiAskAddon = useAiAskAddon({
    title: selectedPair ? selectedPair.pair_label : "MA-Spread",
    subtitle,
    option: chartOption,
    spec: aiAskSpec,
    getInstance: () => maAiAskRef.current,
  });

  // Render a single pair chip (used in the 2-row pair grid). The chip fills
  // its grid column: display:flex overrides MUI's default inline-flex so
  // width:100% takes effect, and the label is centered within.
  // Price and EMA chips are always loaded with the default chart. Amt/MA
  // chips belong to the "amt" extras group: the FIRST pick loads the group
  // (freezing every control until it resolves) and selects the chip as
  // soon as the data lands. Clicking an already-active Amt/MA chip toggles
  // it off ("unclick") and recovers the normal OHLC price style by falling
  // back to Price/MA5 (pair index 0).
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
        disabled={extrasLoading}
        color={active ? "primary" : "default"}
        variant={active ? "filled" : "outlined"}
        onClick={() => {
          if (isAmt && !amtLoaded) {
            void ensureMetric("amt").then(() => setSelectedPairIdx(idx));
            return;
          }
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
      titleAddon={aiAskAddon}
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
      {/* Chart body (loading / error / empty placeholders + the EChart) is
          owned by the shared BaseChart, embedded bare inside this card; the
          pair-chip / window / streak / px-vol controls go in its children
          slot (rendered above the chart body in every state). */}
      <BaseChart
        variant="bare"
        option={chartOption}
        height={420}
        loading={loading || extrasLoading || regimesLoading}
        freezeOnLoading
        error={error}
        emptyText={
          selectedPair
            ? `No data for ${selectedPair.pair_label} in this date range.`
            : "No data"
        }
        onCanvasClick={handleCanvasClick}
        onReady={(c) => {
          maAiAskRef.current = c;
        }}
      >
      {/* Pair chips — Simple MA section (2 rows) + Exponential MA section
          (2 rows) + optional Trading Amt/MA row. Moved to the top of the
          card so the time slider can sit at the bottom. */}
      {!loading && !error && pairs.length > 0 && (
        <Box sx={{ mt: 1, mb: 0.5 }}>
          {/* ---- Simple MA section ---- */}
          <SectionLabel id="pairsSimple" standalone suffix=" — click to switch" />
          <Box sx={PERIOD_GRID_SX}>
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
          <SectionLabel id="pairsEma" standalone mt={1} suffix=" — click to switch" />
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
                <SectionLabel
                  id="tradingAmt"
                  active={amtPairSelected}
                  suffix={
                    extrasLoading && !amtLoaded
                      ? " — loading…"
                      : !amtLoaded
                        ? " — click an Amt/MA chip to load"
                        : ""
                  }
                />
                {LONG_MA_ORDER.map((maLong, col) => {
                  const idx = pairIndexMap.get(`amt--1-${maLong}`);
                  if (idx == null && !amtLoaded) {
                    // Placeholder chip — the "amt" extras group isn't loaded
                    // yet. Clicking it fetches the group (freezing every
                    // control until it resolves) and selects the pair as
                    // soon as it lands (amt series append after the 18
                    // defaults, in this row's column order).
                    return (
                      <Box key={`amt-${col}`} sx={{ gridColumn: col + 1 }}>
                        <Chip
                          label={`Amt/MA${maLong}`}
                          size="small"
                          clickable
                          disabled={extrasLoading}
                          color="default"
                          variant="outlined"
                          onClick={() => {
                            void ensureMetric("amt").then(() =>
                              setSelectedPairIdx(18 + col),
                            );
                          }}
                          sx={PERIOD_CHIP_SX}
                        />
                      </Box>
                    );
                  }
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
            <SectionLabel id="ohlcWindow" active={ohlcWindow != null} />
            {OHLC_WINDOWS.map((w, col) => (
              <Chip
                key={w}
                label={`${w}d`}
                size="small"
                clickable
                disabled={extrasLoading}
                color={ohlcWindow === w ? "primary" : "default"}
                variant={ohlcWindow === w ? "filled" : "outlined"}
                onClick={() => handleOhlcWindowClick(w)}
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

          {/* ---- High/Low Streaks buttons (nested, single-select) ----
              Same grid and chip style as the other button rows: the row
              label spans the full width, then the first layer holds the
              band lookback periods (60/120/255/500/750/1275d) aligned
              with the OHLC row's matching columns. Clicking a period
              EXPANDS the second layer — band tightness pcts (1/5/10%)
              on the row beneath — and clicking the active period
              collapses it again. Selecting a pct fills the LATEST date's
              trailing-period window with its top/bottom pct% price zones
              (light purple above high_val, light yellow below low_val)
              and draws that combo's break streaks darker inside;
              clicking a chart date anchors the window to the trailing
              rows before that date. */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            {/* Row label */}
            <SectionLabel id="highLowStreaks" active={streakPeriod != null} />
            {/* Layer 1 — periods, columns 3-8 (aligned with the OHLC row's
                60d/120d/255d/500d/750d/1275d buttons). */}
            {HIGH_LOW_STREAK_PERIODS.map((w, col) => (
              <Chip
                key={w}
                label={`${w}d`}
                size="small"
                clickable
                disabled={extrasLoading}
                color={streakPeriod === w ? "primary" : "default"}
                variant={streakPeriod === w ? "filled" : "outlined"}
                onClick={() => handleStreakPeriodClick(w)}
                sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
              />
            ))}
            {/* Layer 2 — pcts, expanded beneath the periods when one is
                active (columns 3-5, under the first three periods). */}
            {streakPeriod != null &&
              HIGH_LOW_STREAK_PCTS.map((p, col) => (
                <Chip
                  key={p}
                  label={`${p}%`}
                  size="small"
                  clickable
                  disabled={extrasLoading}
                  color={streakPct === p ? "primary" : "default"}
                  variant={streakPct === p ? "filled" : "outlined"}
                  onClick={() => toggleStreakPct(p)}
                  sx={{ gridColumn: col + 3, ...PERIOD_CHIP_SX }}
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
          {extras.highLowStreaks != null && !hasStreakData && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              no streak data for this code — run{" "}
              <code>python -m analyze.mov_ave_spread</code> to build
              analysis.mov_ave_high_low_pct_streaks
            </Typography>
          )}

          {/* ---- Px-Vol States buttons (nested, single-select per row) ----
              The trading amt × price-change state family — shades come
              from the analysis.mov_ave_price_vs_amt registry (the
              analysis_forecasts.px_vol_state engine's date-level
              source of truth): layer 1 = the day's price SPEED
              (sharp/slow/flat rise/drop — t = ret_1d / the code's own
              trailing 255-row σ_ret), layer 2 = the day's
              trading-amount LEVEL STATE (up/flat/down vs the code's
              own trailing-year amount distribution — z-scored log
              amount). Picking one of each shades the chart over
              the dates satisfying BOTH legs; the shade hue follows the
              price direction (green rise / red drop / gray flat) and
              its DEPTH follows the combo's strength — sharp×heavy
              (e.g. rising price + high amount = strong growth)
              shades darkest, weaker speeds / amount states lighten
              toward pastel. */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            {/* Row label */}
            <SectionLabel
              id="pxVolStates"
              active={pxVolSpeed != null || pxVolVol != null}
            />
            {/* Layer 1 — price speed (columns 1-5). */}
            {PX_VOL_SPEED_OPTIONS.map((o, col) => (
              <Chip
                key={o.key}
                label={o.label}
                size="small"
                clickable
                disabled={extrasLoading}
                color={pxVolSpeed === o.key ? "primary" : "default"}
                variant={pxVolSpeed === o.key ? "filled" : "outlined"}
                onClick={() => handlePxVolSpeedClick(o.key)}
                sx={{ gridColumn: col + 1, ...PERIOD_CHIP_SX }}
              />
            ))}
            {/* Layer 2 — trading-amount state (columns 1-3), always visible:
                the two rows are independent picks of ONE state family, not
                a progressive drill-down like High/Low Streaks. */}
            {PX_VOL_VOL_OPTIONS.map((o, col) => (
              <Chip
                key={o.key}
                label={o.label}
                size="small"
                clickable
                disabled={extrasLoading}
                color={pxVolVol === o.key ? "primary" : "default"}
                variant={pxVolVol === o.key ? "filled" : "outlined"}
                onClick={() => handlePxVolVolClick(o.key)}
                sx={{ gridColumn: col + 1, ...PERIOD_CHIP_SX }}
              />
            ))}
          </Box>
          {/* ---- Market Regimes buttons (multi-select) ---- The
              stats.market_regimes daily label's contiguous spans (the
              market_regime_spans table): calm = the market's quiet
              background stretches, hot = elevated volatility WITH volume
              expansion, panic = elevated volatility without it, quiet =
              volume expansion without price movement. Each picked regime
              shades its spans in its palette color (calm grey / hot
              purple / panic red / quiet blue — the same accents the
              forecast tables color their regime cells with). Multi-select
              — regimes can overlap in time (a hot span followed by a
              quiet span). */}
          <Box sx={{ ...PERIOD_GRID_SX, mt: 1 }}>
            <SectionLabel
              id="marketRegimes"
              active={regimePicks.size > 0}
              suffix={regimesLoading ? " — loading…" : ""}
            />
            {ALL_REGIMES.map((r, col) => (
              <Chip
                key={r}
                label={REGIME_LABELS[r]}
                size="small"
                clickable
                disabled={extrasLoading || regimesLoading}
                color={regimePicks.has(r) ? "primary" : "default"}
                variant={regimePicks.has(r) ? "filled" : "outlined"}
                onClick={() => toggleRegime(r)}
                sx={{
                  gridColumn: col + 1,
                  ...PERIOD_CHIP_SX,
                  ...(regimePicks.has(r)
                    ? {
                        color: REGIME_ACCENT_COLORS[r],
                        borderColor: REGIME_ACCENT_COLORS[r],
                      }
                    : {}),
                }}
              />
            ))}
          </Box>
          {regimePicks.size > 0 && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              {regimeSpans == null
                ? "fetching the code's regime spans…"
                : ALL_REGIMES.filter((r) => regimePicks.has(r))
                    .map((r) => {
                      const spans = regimeSpans[r] ?? [];
                      const days = spans.reduce((a, sp) => a + sp.spanDays, 0);
                      return `${REGIME_LABELS[r]}: ${
                        spans.length === 0
                          ? "no spans"
                          : `${spans.length} span${spans.length === 1 ? "" : "s"} · ${days}d · last ${spans[spans.length - 1].endDate}`
                      }`;
                    })
                    .join(" · ") + " · source: stats.market_regimes (vol_z / amt_z vs own trailing 255d, bars ±1.0σ)"}
            </Typography>
          )}
          {(pxVolSpeed != null || pxVolVol != null) && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              {pxVolSpeed == null || pxVolVol == null
                ? "pick both a price speed and an amount state to shade matching dates"
                : `${PX_VOL_SPEED_OPTIONS.find((o) => o.key === pxVolSpeed)?.label} × ${
                    PX_VOL_VOL_OPTIONS.find((o) => o.key === pxVolVol)?.label
                  } — ${pxVolReading(pxVolSpeed, pxVolVol)} · ${
                    pxVolRuns.length === 0
                      ? "no matching dates"
                      : `${pxVolRuns.reduce((a, r) => a + r.days, 0)} matching ${
                          pxVolRuns.reduce((a, r) => a + r.days, 0) === 1 ? "day" : "days"
                        } in ${pxVolRuns.length} run${pxVolRuns.length === 1 ? "" : "s"}` +
                        ` · longest ${Math.max(...pxVolRuns.map((r) => r.days))}d` +
                        ` · last ${pxVolRuns[pxVolRuns.length - 1].endDate}`
                  } · thresholds t ±2.0σ (sharp) / ±1.26σ (slow) · z_amt(log-level) +2.0 / −0.92 · source: ${
                    extras.priceVsAmt != null
                      ? "analysis.mov_ave_price_vs_amt"
                      : "client-side replication (registry rows not loaded)"
                  }`}
            </Typography>
          )}
          {metricError != null && (
            <Typography
              variant="caption"
              color="error"
              sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
            >
              metric load failed: {metricError} — click the section's button
              again to retry
            </Typography>
          )}
        </Box>
      )}
      </BaseChart>

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
    </ChartCard>
  );
}
