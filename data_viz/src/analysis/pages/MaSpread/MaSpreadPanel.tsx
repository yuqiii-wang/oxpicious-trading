/**
 * MaSpreadPanel — one card per code: pair chips + two-curve chart with
 * green/red fill between them + Bollinger envelope + the section rows
 * beneath the chart. Slim orchestrator since the 2026-10-20 split: it
 * owns the interaction state (the picks, the single shade slot's
 * exclusivity, the fetch-through click handlers) and composes the
 * extracted sub-modules —
 *
 *   hooks/useMaSpreadData    the chart blocks + on-demand metric groups
 *   hooks/useTrendSpans      the trend-phase spans fetch
 *   hooks/useRegimeSpans     the market-regime spans fetch-once
 *   shadeBuilders            pure overlay/line assembly for the option
 *   aiAskSpec                the AI Ask view description
 *   sections/                one component per button row + the header
 *                            actions; each carries its own docs
 *   chartOption/             the ECharts option builders (pre-existing)
 *
 * Cross-cutting semantics the composition preserves:
 *   · The shade families share ONE slot (trend / RSI pct / streaks /
 *     px-vol / OHLC days / regimes) — clearShadeGroupsExcept.
 *   · Enabling a Roof/Floor window is EXCLUSIVE: every other section's
 *     picks clear, the header overlay returns to Amt, an Amt/MA envelope
 *     view falls back to Price/MA5, and the window's change-since-anchor
 *     plot takes the right axis.
 *   · Right-axis precedence: Slopes/Curvatures > RSI Trends lines >
 *     Momentum lines > amt bars.
 *   · While any extras group is in flight every control is disabled and
 *     the plot freezes under a translucent spinner (BaseChart
 *     freezeOnLoading); a pick on an unloaded section fetches its group
 *     first and applies when it lands.
 */
import { useCallback, useMemo, useRef, useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import ChartCard from "@/shared/components/ChartCard";
import AnalysisRunButton from "@/components/AnalysisRunButton";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import { useAiAskAddon } from "@/shared/ai-ask";
import type { AiAskSpec } from "@/shared/ai-ask";
import type { ECharts } from "echarts";
import { UP_COLOR } from "@/theme/chart-palette";
import {
  type MovAveSpreadChartResponse,
  type MovAveSpreadPairSeries,
  type RoofFloorEstimateMethod,
} from "@shared/types";
import { fmtNum, fmtPct } from "@/lib/series";
import type { OhlcMode } from "@/lib/ohlc";
import {
  computeBreakStreaks,
  computeStreakBandWindow,
} from "@/shared/charts/streakBands";
import type { PanelProps, ChartOverlay } from "./types";
import { RF_ESTIMATE_DEFAULT_METHOD } from "./constants";
import {
  shortLabel,
  type MomentumTerm,
} from "./chartOption";
import { useMaSpreadData } from "./hooks/useMaSpreadData";
import { useTrendSpans } from "./hooks/useTrendSpans";
import { useRegimeSpans } from "./hooks/useRegimeSpans";
import { useShadeFamilies } from "./hooks/useShadeFamilies";
import {
  buildRegimeShades,
  buildPxVolStates,
  buildPxVolRuns,
  buildPxVolShade,
  buildDerivTrend,
  buildMomentumTrend,
  buildOhlcGapShades,
  buildRsiRankShades,
  buildTrendShades,
  composeChartOption,
} from "./shadeBuilders";
import { buildMaSpreadAiAskSpec } from "./aiAskSpec";
import { useDocs } from "@/shared/docs";
import { PanelHeaderActions } from "./sections/PanelHeaderActions";
import { PairChipGrid } from "./sections/PairChipGrid";
import { RsiTrendsSection } from "./sections/RsiTrendsSection";
import { MomentumSection } from "./sections/MomentumSection";
import { RoofFloorSection } from "./sections/RoofFloorSection";
import { HighLowStreaksSection } from "./sections/HighLowStreaksSection";
import { PxVolSection, PxVolCaption } from "./sections/PxVolSection";
import { OhlcGapsSection } from "./sections/OhlcGapsSection";
import { MarketRegimesSection } from "./sections/MarketRegimesSection";
import { TrendSection } from "./sections/TrendSection";

export function MaSpreadPanel({ code, name, secType }: PanelProps) {
  // Reactive light/dark theme — single source of truth for every option
  // builder call below (no prop drilling).
  const themeMode = useChartThemeMode();

  // Which of the pairs is shown in the single plot (default 0 = Price/MA5),
  // and the clicked chart date index (into the full rows — shared date
  // axis; the roof/floor trendlines and the streak window re-anchor to
  // it). Declared before the data hook — its pairs-landed callback
  // resets both.
  const [selectedPairIdx, setSelectedPairIdx] = useState(0);
  const [anchorIdx, setAnchorIdx] = useState<number | null>(null);

  // ---- Data layer (hooks/useMaSpreadData) --------------------------------
  // The pairs block landing resets the pair/anchor selections (the
  // callback is invoked at the same point the inline effect did).
  const handlePairsLanded = useCallback(() => {
    setSelectedPairIdx(0);
    setAnchorIdx(null);
  }, []);
  const {
    chartData,
    loading,
    error,
    extras,
    loadingMetrics,
    extrasLoading,
    metricError,
    ensureMetric,
    ensureMetrics,
    handleAnalysisRunCompleted,
  } = useMaSpreadData(code, secType, handlePairsLanded);
  const amtLoaded = extras.amtPairs != null;

  // ---- Picks + section state ----------------------------------------------
  // Bollinger multiplier k in MA ± k×σ. Default 2 (standard Bollinger).
  // 0 hides the envelope. Affects Price/MA and Price/EMA pairs (ma_short === 0);
  // MA5/MA and EMA6/EMA pairs don't get the envelope and the dropdown is hidden.
  const [bollingerK, setBollingerK] = useState(2);

  // The chart's right-axis overlay switch (the Amt | Slopes | Curvatures
  // group in the header) — see the ChartOverlay doc in ./types.
  const [chartOverlay, setChartOverlay] = useState<ChartOverlay>("amt");

  // OHLC display mode — "percentage" (default) rebases OHLC + MAs to % change
  // from the first valid close; "absolute" shows raw prices.
  const [ohlcMode, setOhlcMode] = useState<OhlcMode>("percentage");

  // Enabled roof/floor window (trading days) — null = off; the trendlines
  // converge on anchorIdx (declared with the pair pick above).
  const [roofFloorWindow, setRoofFloorWindow] = useState<number | null>(null);

  // Roof/floor Est-levels overlay (needs a roof/floor window enabled):
  // rfEstOn toggles the two horizontal estimated roof/floor lines at the
  // CHOSEN date from the "rfest" extras group; rfEstMethod picks the
  // quantitative method (median = cross-method consensus default).
  const [rfEstOn, setRfEstOn] = useState(false);
  const [rfEstMethod, setRfEstMethod] = useState<RoofFloorEstimateMethod>(
    RF_ESTIMATE_DEFAULT_METHOD,
  );
  // The toggle click — fetch-through pattern (first pick loads the
  // "rfest" group, the toggle applies when it lands).
  const handleRfEstToggle = useCallback(() => {
    const apply = () => setRfEstOn((v) => !v);
    if (extras.rfEstimates == null) {
      void ensureMetric("rfest").then(apply);
    } else {
      apply();
    }
  }, [ensureMetric, extras.rfEstimates]);

  // RSI Trends section state (the button row beneath the chart): rsiDays
  // multi-picks the Wilder-RSI window lines (they replace the amt bars
  // while the header toggle sits at "amt") AND scopes the pct row's
  // ranks; rsiPopulation narrows to the rsi layer's is_typical
  // populations once a day is picked. The pct SHADE pick lives in
  // useShadeFamilies below.
  const [rsiDays, setRsiDays] = useState<number[]>([]);
  const [rsiPopulation, setRsiPopulation] = useState<
    "typical" | "re-entry" | null
  >(null);

  // Market Regimes spans fetch (once per code, on the first regime pick).
  const { regimeSpans, regimesLoading, ensureSpans } = useRegimeSpans(code, secType);

  // The six shade families — state + toggles + fetch-through handlers and
  // the single-slot exclusivity (hooks/useShadeFamilies; the destructured
  // names mirror the identifiers the memos/JSX below already use).
  const {
    trendPicks,
    toggleTrendPick,
    regimePicks,
    toggleRegime,
    gapMarks,
    handleGapMarkClick,
    streakPeriod,
    streakPct,
    handleStreakPeriodClick,
    toggleStreakPct,
    pxVolSpeed,
    pxVolVol,
    handlePxVolSpeedClick,
    handlePxVolVolClick,
    rsiTopPct,
    handleRsiTopPctSelect,
    clearAllShades,
  } = useShadeFamilies({
    code,
    secType,
    ensureMetric,
    ensureSpans,
    rsiDays,
    rsiRanksLoaded: extras.rsiRanks != null,
  });

  // Trend-phase spans (analysis.trend_days) — the picked phases' shade
  // source and the tooltip's per-date trend readout.
  const trendSpans = useTrendSpans(code, secType);

  // Momentum section state (the row beneath RSI Trends): multi-pick of
  // the fixed anchors + volatility-adaptive terms; the group auto-loads
  // with the chart blocks (the chips' lookback labels need it).
  const [momentumTerms, setMomentumTerms] = useState<MomentumTerm[]>([]);

  // Pure toggle (functional update — rapid clicks can't race the closure);
  // the pct arm/clear lifecycle lives in useShadeFamilies.
  const toggleRsiDay = useCallback((w: number) => {
    setRsiDays((prev) =>
      prev.includes(w) ? prev.filter((d) => d !== w) : [...prev, w],
    );
  }, []);
  const handleRsiPopulationToggle = useCallback(
    (p: "typical" | "re-entry") => {
      setRsiPopulation((prev) => (prev === p ? null : p));
    },
    [],
  );

  const toggleMomentumTerm = useCallback(
    (term: MomentumTerm) => {
      const apply = () =>
        setMomentumTerms((prev) =>
          prev.includes(term)
            ? prev.filter((t) => t !== term)
            : [...prev, term],
        );
      if (extras.momentum == null) {
        void ensureMetric("momentum").then(apply);
      } else {
        apply();
      }
    },
    [ensureMetric, extras.momentum],
  );

  // ---- Derived rows / lookups ----------------------------------------------

  // The first pair's rows — the card subtitle (date range + bar count)
  // and the x-axis dates for the chart (all pairs share one date axis).
  const firstPairRows = chartData?.pairs[0]?.rows ?? [];

  // Whether this security has analysis rows — drives the bold highlight of
  // the per-security build button (AnalysisRunButton). Loading counts as
  // "present" so the button doesn't bold-flicker on every fetch.
  const hasAnalysisData = loading || firstPairRows.length > 0;

  // The full pairs list — the 18 default series plus the 5 Amt/MA series
  // once the "amt" extras group has loaded. Used for the pair chips, the
  // pair index lookup, and the chart option builder.
  const pairs = useMemo(
    () => [...(chartData?.pairs ?? []), ...(extras.amtPairs ?? [])],
    [chartData, extras.amtPairs],
  );

  // Lookup from `${kind}-${ma_short}-${ma_long}` → index in pairs, used to
  // place each pair chip in its long-MA column of the 2-row pair grid.
  const pairIndexMap = useMemo(() => {
    const m = new Map<string, number>();
    pairs.forEach((p, i) => {
      const kind = p.kind ?? "price";
      m.set(`${kind}-${p.ma_short}-${p.ma_long}`, i);
    });
    return m;
  }, [pairs]);

  // Clamp selectedPairIdx to valid range.
  const safePairIdx = Math.min(selectedPairIdx, Math.max(0, pairs.length - 1));
  const selectedPair = pairs[safePairIdx];
  // True when an Amt/MA pair is selected — price chips are frozen (disabled).
  const amtPairSelected = selectedPair?.kind === "amt";

  // ---- High/Low Streaks derived window + streaks ---------------------------
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
      anchorIdx,
    );
  }, [firstPairRows, streakPeriod, streakPct, anchorIdx]);

  // The BREAK STREAKS for the selected (period, pct) combo — detected
  // CLIENT-SIDE against the anchor window's own static band edges (the
  // same edges the light zones draw); see shadeBuilders' module docs and
  // the streakBands kit.
  const longStreaks = useMemo<{
    high: ReturnType<typeof computeBreakStreaks>["high"];
    low: ReturnType<typeof computeBreakStreaks>["low"];
  } | null>(() => {
    if (streakPeriod == null || streakPct == null || streakWin == null) return null;
    return computeBreakStreaks(firstPairRows, streakWin);
  }, [firstPairRows, streakWin]);

  // ---- Overlay assembly (shadeBuilders) -------------------------------------
  const pxVolStates = useMemo(
    () =>
      buildPxVolStates(
        firstPairRows,
        extras.priceVsAmt,
        pxVolSpeed != null || pxVolVol != null,
      ),
    [chartData, extras.priceVsAmt, firstPairRows, pxVolSpeed, pxVolVol],
  );
  const pxVolRuns = useMemo(
    () => buildPxVolRuns(firstPairRows, pxVolStates, pxVolSpeed, pxVolVol),
    [firstPairRows, pxVolStates, pxVolSpeed, pxVolVol],
  );
  const pxVolShade = useMemo(
    () => buildPxVolShade(pxVolSpeed, pxVolVol, pxVolRuns),
    [pxVolSpeed, pxVolVol, pxVolRuns],
  );

  const regimeShades = useMemo(
    () => buildRegimeShades(regimePicks, regimeSpans),
    [regimePicks, regimeSpans],
  );

  const derivTrend = useMemo(
    () => buildDerivTrend(chartOverlay, selectedPair, pairs, pairIndexMap),
    [chartOverlay, selectedPair, pairs, pairIndexMap],
  );

  // The RSI lines ride the toggle's "amt" slot (Slopes/Curvatures keep
  // precedence): active while the days row holds a pick and no derivative
  // overlay is toggled on. The pct shades are independent of the toggle.
  const rsiLinesActive = chartOverlay === "amt" && rsiDays.length > 0;

  const momentumLinesActive =
    chartOverlay === "amt" &&
    rsiDays.length === 0 &&
    momentumTerms.length > 0 &&
    (extras.momentum?.length ?? 0) > 0;

  const momentumTrend = useMemo(
    () =>
      buildMomentumTrend(momentumLinesActive, momentumTerms, extras.momentum, selectedPair),
    [momentumLinesActive, extras.momentum, momentumTerms, selectedPair],
  );

  const { shades: ohlcGapShades, counts: ohlcGapCounts } = useMemo(
    () => buildOhlcGapShades(gapMarks, extras.ohlcGaps, selectedPair),
    [gapMarks, extras.ohlcGaps, selectedPair],
  );

  const rsiRankShades = useMemo(
    () => buildRsiRankShades(rsiTopPct, rsiDays, extras.rsiRanks, selectedPair),
    [rsiTopPct, rsiDays, extras.rsiRanks, selectedPair],
  );

  const trendShades = useMemo(
    () => buildTrendShades(trendPicks, trendSpans),
    [trendPicks, trendSpans],
  );

  // ---- Chart option ----------------------------------------------------------
  // Null while loading / on error / when the pair has no rows; BaseChart
  // then renders the shared spinner / error Alert / empty placeholder.
  // Memoized so the AI Ask plot info derives from the same object the
  // chart renders (composeChartOption in shadeBuilders assembles it).
  const chartOption = useMemo(
    () =>
      composeChartOption({
        loading,
        error,
        selectedPair,
        amtPairSelected,
        chartData,
        themeMode,
        bollingerK,
        ohlcMode,
        chartOverlay,
        rsiDays,
        rsiPopulation,
        rsiLinesActive,
        derivTrend,
        momentumTrend,
        momentumLinesActive,
        roofFloorWindow,
        anchorIdx,
        rfEstOn,
        rfEstMethod,
        longStreaks,
        streakPeriod,
        streakPct,
        pxVolShade,
        regimeShades,
        ohlcGapShades,
        rsiRankShades,
        trendShades,
        ohlcGapRows: extras.ohlcGaps ?? null,
        ohlcGapActive: gapMarks.spike || gapMarks.flush,
        trendSpans,
        ohlcRows: extras.ohlc ?? null,
        rfEstimates: extras.rfEstimates ?? null,
        rfRebounds: extras.rfRebounds ?? null,
      }),
    [
      loading,
      error,
      selectedPair,
      amtPairSelected,
      chartData,
      extras.ohlc,
      themeMode,
      bollingerK,
      trendSpans,
      chartOverlay,
      rsiDays,
      rsiPopulation,
      rsiLinesActive,
      derivTrend,
      momentumTrend,
      momentumLinesActive,
      ohlcMode,
      roofFloorWindow,
      anchorIdx,
      extras.ohlc,
      extras.rfEstimates,
      extras.rfRebounds,
      rfEstOn,
      rfEstMethod,
      longStreaks,
      streakPeriod,
      streakPct,
      pxVolShade,
      regimeShades,
      ohlcGapShades,
      rsiRankShades,
      trendShades,
      extras.ohlcGaps,
      gapMarks.spike,
      gapMarks.flush,
    ],
  );

  // ---- AI Ask — wire the OUTER card (the chart body is a bare BaseChart).
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
    anchorIdx != null && firstPairRows[anchorIdx]
      ? firstPairRows[anchorIdx].date
      : undefined;
  const docs = useDocs();
  const aiAskSpec = useMemo<AiAskSpec>(
    () =>
      buildMaSpreadAiAskSpec(docs, {
        code,
        name,
        secType,
        selectedPair,
        amtPairSelected,
        shortName,
        longName,
        bollingerK,
        roofFloorWindow,
        rfEstOn,
        rfEstMethod,
        streakLabel,
        pxVolShade,
        ohlcMode,
        rsiDays,
        rsiLinesActive,
        derivTrend,
        anchorDate,
        rsiTopPct,
        trendPicks,
      }),
    [
      docs,
      code,
      name,
      secType,
      selectedPair,
      amtPairSelected,
      shortName,
      longName,
      bollingerK,
      roofFloorWindow,
      rfEstOn,
      rfEstMethod,
      streakLabel,
      pxVolShade,
      ohlcMode,
      rsiDays,
      rsiLinesActive,
      derivTrend,
      anchorDate,
      rsiTopPct,
      trendPicks,
    ],
  );
  const aiAskAddon = useAiAskAddon({
    title: selectedPair ? selectedPair.pair_label : "MA-Spread",
    subtitle: subtitleOf(chartData, code, name, firstPairRows),
    option: chartOption,
    spec: aiAskSpec,
    getInstance: () => maAiAskRef.current,
  });

  // ---- Sub-header + canvas interaction ---------------------------------------

  // Optional secondary stat row from the latest snapshot of all 9 pairs —
  // surfaced as a small caption so the user can scan the page quickly.
  const latestSummary = chartData?.pairs[safePairIdx]?.rows.slice(-1)[0] ?? null;

  // Canvas-level chart click: sets the anchor date. Armed while an OHLC
  // window or the High/Low Streaks row is enabled; the roof/floor
  // trendlines and the streak window re-anchor to the clicked date.
  // Clicking the already-selected date clears the anchor (toggle, back to
  // the latest date's window), clicking another date moves it.
  const handleCanvasClick = useCallback(
    (dataIdx: number) => {
      if (roofFloorWindow == null && streakPeriod == null) return;
      setAnchorIdx((prev) => (prev === dataIdx ? null : dataIdx));
    },
    [roofFloorWindow, streakPeriod],
  );

  // Enabling a Roof/Floor Trendlines window is EXCLUSIVE: every other
  // section's picks clear (RSI Trends, High/Low Streaks, Px-Vol States,
  // Top-10% OHLC Days, Market Regimes, Trend), the header overlay returns
  // to Amt, and an Amt/MA envelope view falls back to Price/MA5 — so the
  // window's roof/floor overlays own the chart and its change-since-
  // last-anchor plot owns the right axis. Toggling the same window off
  // just clears it (the other sections stay cleared until re-picked);
  // the section's own "shade" chip and the clicked anchor survive.
  const handleRoofFloorWindowClick = useCallback(
    (w: number) => {
      const turningOn = roofFloorWindow !== w;
      const apply = () => {
        if (!turningOn) {
          setRoofFloorWindow(null);
          return;
        }
        setChartOverlay("amt");
        setRsiDays([]);
        setMomentumTerms([]);
        clearAllShades();
        if (amtPairSelected) setSelectedPairIdx(0);
        setRoofFloorWindow(w);
      };
      if (extras.ohlc == null) {
        void ensureMetrics(["ohlc", "rfaudit"]).then(apply);
      } else if (extras.rfRebounds == null) {
        void ensureMetric("rfaudit").then(apply);
      } else {
        apply();
      }
    },
    [ensureMetric, ensureMetrics, extras.ohlc, extras.rfRebounds, roofFloorWindow, amtPairSelected, clearAllShades],
  );

  // ---- Pair chip clicks (fetch-through for the Amt/MA chips) -----------------

  const handlePairChipClick = useCallback(
    (pair: MovAveSpreadPairSeries, idx: number, active: boolean) => {
      const isAmt = pair.kind === "amt";
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
    },
    [amtLoaded, ensureMetric],
  );

  const handleAmtPlaceholderClick = useCallback(
    (col: number) => {
      void ensureMetric("amt").then(() => setSelectedPairIdx(18 + col));
    },
    [ensureMetric],
  );

  // Bollinger dropdown + Amt | Slopes | Curvatures overlay switch + OHLC
  // mode toggle shown in the card header's top-right corner.
  const showHeaderActions = !loading && !error && selectedPair != null;

  return (
    <ChartCard
      title={selectedPair ? selectedPair.pair_label : "MA-Spread"}
      subtitle={subtitleOf(chartData, code, name, firstPairRows)}
      titleAddon={aiAskAddon}
      action={
        showHeaderActions ? (
          <Stack direction="row" alignItems="center">
            <PanelHeaderActions
              showBollinger={selectedPair.ma_short === 0 || selectedPair.kind === "amt"}
              bollingerK={bollingerK}
              onBollingerKChange={setBollingerK}
              chartOverlay={chartOverlay}
              onChartOverlayChange={setChartOverlay}
              overlayDisabled={amtPairSelected}
              ohlcMode={ohlcMode}
              onOhlcModeChange={setOhlcMode}
            />
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
          section rows go in its children slot (rendered above the chart
          body in every state). */}
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
      {/* Section rows — one component per row (see sections/); order is
          the layout: pair chips → RSI Trends → Momentum → Roof/Floor →
          High/Low Streaks → Px-Vol → OHLC Days → Market Regimes → Trend. */}
      {!loading && !error && pairs.length > 0 && (
        <Box sx={{ mt: 1, mb: 0.5 }}>
          <PairChipGrid
            pairs={pairs}
            pairIndexMap={pairIndexMap}
            selectedIdx={safePairIdx}
            amtPairSelected={amtPairSelected}
            disabled={extrasLoading}
            amtLoaded={amtLoaded}
            onChipClick={handlePairChipClick}
            onAmtPlaceholderClick={handleAmtPlaceholderClick}
          />

          <RsiTrendsSection
            rsiDays={rsiDays}
            rsiPopulation={rsiPopulation}
            onPopulationToggle={handleRsiPopulationToggle}
            rsiTopPct={rsiTopPct}
            onToggleDay={toggleRsiDay}
            onSelectPct={handleRsiTopPctSelect}
          />

          <MomentumSection
            momentumTerms={momentumTerms}
            onToggleTerm={toggleMomentumTerm}
            disabled={extrasLoading}
            loadingMomentum={loadingMetrics.includes("momentum")}
            momentumRows={extras.momentum}
          />

          <RoofFloorSection
            roofFloorWindow={roofFloorWindow}
            onWindowClick={handleRoofFloorWindowClick}
            rfEstOn={rfEstOn}
            onEstToggle={handleRfEstToggle}
            rfEstMethod={rfEstMethod}
            onEstMethodChange={setRfEstMethod}
            disabled={extrasLoading}
            anchorIdx={anchorIdx}
            firstPairRows={firstPairRows}
            rfEstimates={extras.rfEstimates}
            rfRebounds={extras.rfRebounds}
          />

          <HighLowStreaksSection
            streakPeriod={streakPeriod}
            streakPct={streakPct}
            onPeriodClick={handleStreakPeriodClick}
            onPctClick={toggleStreakPct}
            disabled={extrasLoading}
            streakWin={streakWin}
            longStreaks={longStreaks}
            anchorIdx={anchorIdx}
            streakRowsLoaded={extras.highLowStreaks != null}
            hasStreakData={hasStreakData}
          />

          <PxVolSection
            pxVolSpeed={pxVolSpeed}
            pxVolVol={pxVolVol}
            onSpeedClick={handlePxVolSpeedClick}
            onVolClick={handlePxVolVolClick}
            disabled={extrasLoading}
          />

          <OhlcGapsSection
            gapMarks={gapMarks}
            onToggle={handleGapMarkClick}
            disabled={extrasLoading}
            counts={ohlcGapCounts}
          />

          <MarketRegimesSection
            regimePicks={regimePicks}
            onToggle={toggleRegime}
            disabled={extrasLoading || regimesLoading}
            loading={regimesLoading}
            regimeSpans={regimeSpans}
          />

          <TrendSection trendPicks={trendPicks} onToggle={toggleTrendPick} />

          {(pxVolSpeed != null || pxVolVol != null) && (
            <PxVolCaption
              pxVolSpeed={pxVolSpeed}
              pxVolVol={pxVolVol}
              pxVolRuns={pxVolRuns}
              priceVsAmtLoaded={extras.priceVsAmt != null}
            />
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

/** The card subtitle — code · name · bar count · date span. */
function subtitleOf(
  chartData: MovAveSpreadChartResponse | null,
  code: string,
  name: string,
  firstPairRows: MovAveSpreadChartResponse["pairs"][number]["rows"],
): string {
  return chartData
    ? `${chartData.code} · ${chartData.name || name || "—"} · ${firstPairRows.length} bars` +
        (firstPairRows.length > 0
          ? ` · ${firstPairRows[0].date} → ${firstPairRows[firstPairRows.length - 1].date}`
          : "")
    : `${code} · ${name || "—"}`;
}
