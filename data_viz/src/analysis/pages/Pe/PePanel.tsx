/**
 * PePanel — one card per code: the EXACT data-viz baseline plot for the
 * security on top, the Valuation Streaks (band-break) analysis card
 * beneath. (The former Dividends mode — the "Dividend Stats (10y rolling)"
 * table card backed by analysis.pe_stats / analysis.dividends_stats — was
 * removed 2026-09-28 with its tables.)
 *
 * The plot is NOT reimplemented here — it delegates to the shared baseline
 * panel used across the app:
 *   • sec_type=index → IndexPanel  (OHLC + MAs + Trading Amt + PE twin axis)
 *   • sec_type=etf   → EtfMarginPanel (rebased OHLC + MAs + RZ/RQ + Amt +
 *                     corp-action markers; dividends already shown as gold
 *                     diamond markPoints on ex-dividend dates)
 *   • sec_type=stock → StockPanel (OHLC + MAs + PE; dividends already shown
 *                     as gold diamond markPoints on ex-dividend dates)
 *
 * The baseline bundle is fetched by the shared useSecBaselineBundle hook
 * (extracted 2026-09-29 from the effect this panel and DividendsPanel
 * duplicated).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import ChartCard from "@/shared/components/ChartCard";
import AnalysisRunButton from "@/components/AnalysisRunButton";
import EChart from "@/shared/charts/EChart";
import { useChartThemeMode } from "@/shared/charts/base-chart";
import IndexPanel from "@/dataviz/features/index/IndexPanel";
import EtfMarginPanel from "@/dataviz/features/etf-margin/EtfMarginPanel";
import StockPanel from "@/dataviz/features/stock/StockPanel";
import { useSecBaselineBundle } from "@/analysis/pages/shared/useSecBaselineBundle";
import { fmtNum } from "@/lib/series";
import {
  fetchPeStreaks,
  fetchPeChart,
  invalidateCacheForUrl,
} from "@/lib/api-client";
import type {
  IndexBundle,
  EtfBundle,
  StockBundle,
  PeStreaksResponse,
  PeStreak,
  PeStreakMetric,
  PeChartResponse,
} from "@shared/types";
import { buildStreakChartOption, type MetricObsRow } from "./chartOption/streakChartOption";
import { useAiAskAddon } from "@/shared/ai-ask";
import type { AiAskSpec } from "@/shared/ai-ask";
import type { ECharts } from "echarts";
import type { PanelProps } from "./types";import {
  expandedTableBodyCellSx,
  expandedTableBodyRowSx,
  expandedTableContainerSx,
  expandedTableHeadCellSx,
  expandedTableNumCellSx,
} from "@/shared/styles/expanded-table-styles";

// ---- Band-break excursion streaks (analysis.pe_pct_streaks, the
// mov_ave_high_low_pct_streaks pattern applied to pe) — nested
// period → pct selection mirrors the MaSpread High/Low Streaks buttons.
// (2026-09-28: PE-only — the dividend_yield streak half was removed with
// its tables.) ----

/** Band lookback windows (observations) — mirrors PD_PCT_PERIODS. */
const STREAK_PERIODS = [255, 500, 750, 1275] as const;
/** Band tightness levels (percent) — mirrors PD_PCT_TYPES. */
const STREAK_PCTS = [1, 5, 10] as const;
/** In-band gap tolerance bridged inside ONE streak (trading days). */
const STREAK_GAP_TOLERANCE = 5;
/** Max streak rows rendered in the table (most recent first). */
const STREAK_TABLE_CAP = 500;
/** Accent for high-side streaks (stretched metric — the MaSpread high
 *  streak accent). */
const STREAK_HIGH_ACCENT = "#AB47BC";
/** Accent for low-side streaks (compressed metric — the MaSpread low
 *  streak accent). */
const STREAK_LOW_ACCENT = "#F9A825";

/** The streaks card's fixed metric — PE (the dividend_yield streak half
 *  was removed 2026-09-28, so there is no metric filter row — the card
 *  just shows PE content). */
const STREAK_METRIC: PeStreakMetric = "pe";

/** Format one streak's pe value (a plain multiple). */
function fmtStreakValue(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return fmtNum(v, 2);
}

export function PePanel({
  code,
  secType,
}: PanelProps) {
  // Reactive light/dark theme — feeds the streak-chart builder and the
  // delegated baseline panels (no prop drilling from the page).
  const themeMode = useChartThemeMode();

  // ---- Security baseline bundle (IndexBundle | EtfBundle | StockBundle) ---
  const { bundle, loading: bundleLoading, error: bundleError } =
    useSecBaselineBundle(code, secType);

  // ---- Band-break excursion streaks data ----------------------------------
  const [streakData, setStreakData] = useState<PeStreaksResponse | null>(null);
  const [streaksLoading, setStreaksLoading] = useState(false);
  const [streaksError, setStreaksError] = useState<string | null>(null);

  // ---- Daily chart rows (observation series for the streak shading) -------
  const [chartData, setChartData] = useState<PeChartResponse | null>(null);

  // Streak combo selection: band lookback period + band tightness pct. The
  // metric is FIXED to PE (STREAK_METRIC — no metric filter row).
  const [streakPeriod, setStreakPeriod] = useState<number | null>(null);
  const [streakPct, setStreakPct] = useState<number | null>(null);

  // Streak shading anchor (index into the selected metric's observation
  // rows; null = the latest row) + the clicked archive streak (drives the
  // table highlight and the bordered band on the chart).
  const [streakAnchorIdx, setStreakAnchorIdx] = useState<number | null>(null);
  const [selectedStreak, setSelectedStreak] = useState<PeStreak | null>(null);

  // Bumped by the per-security AnalysisRunButton after a rebuild run —
  // retriggers the streaks fetch (the cache entry is invalidated first in
  // the completion handler).
  const [refreshKey, setRefreshKey] = useState(0);

  // Fetch the band-break excursion streaks + daily chart rows on mount and
  // whenever code/sec_type changes (the streaks card + its shading chart ARE
  // the analysis card). A per-security rebuild refreshes them.
  useEffect(() => {
    let cancelled = false;
    setStreaksLoading(true);
    setStreaksError(null);
    fetchPeStreaks(code, secType)
      .then((data) => {
        if (cancelled) return;
        setStreakData(data);
        setStreaksLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setStreaksError(e.message);
        setStreaksLoading(false);
      });
    fetchPeChart(code, secType)
      .then((data) => {
        if (cancelled) return;
        setChartData(data);
      })
      .catch(() => {
        /* the baseline plot above still renders; the streak chart just
           shows the "no data" hint */
        if (cancelled) return;
        setChartData(null);
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType, refreshKey]);

  // Reset the streak combo/anchor when the code changes.
  useEffect(() => {
    setStreakPeriod(null);
    setStreakPct(null);
    setStreakAnchorIdx(null);
    setSelectedStreak(null);
  }, [code, secType]);

  // ---- Streak section: availability + selected-combo subset --------------
  const allStreaks = useMemo(() => streakData?.streaks ?? [], [streakData]);
  const hasStreakData = allStreaks.length > 0;

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

  // The selected combo's streaks, most recent first (capped for render).
  const selectedStreaks = useMemo(() => {
    if (streakPeriod == null || streakPct == null) return [];
    return allStreaks
      .filter((s) => s.period === streakPeriod && s.pctType === streakPct)
      .sort((a, b) => (a.startDate < b.startDate ? 1 : -1));
  }, [allStreaks, streakPeriod, streakPct]);

  // Caption summary for the selected combo: per-side streak count, total
  // days, extreme value and the first couple of spans (MaSpread caption
  // style).
  const streakSummary = useMemo(() => {
    if (streakPeriod == null || streakPct == null) return null;
    const high = selectedStreaks.filter((s) => s.side === "high");
    const low = selectedStreaks.filter((s) => s.side === "low");
    const spanList = (arr: PeStreak[]) =>
      arr.length === 0
        ? "none"
        : `${arr.length} streak${arr.length === 1 ? "" : "s"} · ${arr.reduce((a, s) => a + s.dayCount, 0)}d · peak ${fmtStreakValue(Math.max(...arr.map((s) => s.maxValue)))} · ${arr.slice(0, 2).map((s) => `${s.startDate.slice(2)}→${s.endDate.slice(2)}`).join(", ")}${arr.length > 2 ? `, +${arr.length - 2} more` : ""}`;
    return { high: spanList(high), low: spanList(low) };
  }, [selectedStreaks, streakPeriod, streakPct]);

  // ---- Streak shading chart (MaSpread-style window zones + break bands) --
  // Non-NULL observations of the selected metric — the series the shading
  // is detected and drawn against.
  const obsRows = useMemo<MetricObsRow[]>(() => {
    if (chartData == null) return [];
    return chartData.rows
      .filter((r) => r[STREAK_METRIC] != null && Number.isFinite(r[STREAK_METRIC]!))
      .map((r) => ({ date: r.date, value: r[STREAK_METRIC]! }));
  }, [chartData]);

  /** Stable identity for an archive streak (table-row highlight state). */
  const streakKey = (s: PeStreak): string =>
    `${s.metric}|${s.period}|${s.pctType}|${s.startDate}|${s.endDate}|${s.side}`;

  // Clicking a streak row: anchor the shading window at the streak's end
  // date, border its detected counterpart on the chart. Clicking the
  // selected row again clears the anchor (back to the latest window).
  const handleStreakRowClick = useCallback(
    (s: PeStreak) => {
      if (selectedStreak != null && streakKey(selectedStreak) === streakKey(s)) {
        setSelectedStreak(null);
        setStreakAnchorIdx(null);
        return;
      }
      setSelectedStreak(s);
      const idx = obsRows.findIndex((r) => r.date === s.endDate);
      setStreakAnchorIdx(idx >= 0 ? idx : null);
    },
    [selectedStreak, obsRows],
  );

  // Clicking the chart re-anchors the window (like MaSpread); clicking the
  // anchored date again clears it. Re-anchoring deselects the archive
  // streak (its static-edge counterpart may no longer be in the window).
  const handleStreakChartClick = useCallback((dataIdx: number) => {
    setStreakAnchorIdx((prev) => {
      if (prev === dataIdx) return null;
      return dataIdx;
    });
    setSelectedStreak(null);
  }, []);

  const chartBuild = useMemo(() => {
    if (streakPeriod == null || streakPct == null || obsRows.length === 0) return null;
    return buildStreakChartOption({
      obs: obsRows,
      period: streakPeriod,
      pct: streakPct,
      anchorIdx: streakAnchorIdx,
      selectedStreak,
      themeMode,
    });
  }, [obsRows, streakPeriod, streakPct, streakAnchorIdx, selectedStreak, themeMode]);

  // ---- Streak chart AI Ask — state carries the CURRENT metric → window →
  // tightness combo + anchor so the modal / LLM describe the shaded view. --
  const streakChartRef = useRef<ECharts | null>(null);
  const streakAiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        "Valuation band-break streaks: PE plotted " +
        "daily with the trailing window's static tightness-% zones shaded (light purple " +
        "above, light yellow below); client-side break streaks drawn darker. Streak = " +
        "maximal run of days above (high) / below (low) the anchor window's static band " +
        "edge, bridging in-band gaps of ≤5 trading days. Click a table row or chart date " +
        "to re-anchor. Indication: long excursions = valuation stretched past its recent " +
        "envelope — historically snaps back once the band re-anchors.",
      instruments: [{ code, assetClass: secType === "etf" ? "etf" : secType === "index" ? "index" : "stock" }],
      series: [
        { name: "PE", unit: "×", description: "daily price/earnings multiple" },
        { name: "High streaks", description: "merged spans of days above the window's static top band edge" },
        { name: "Low streaks", description: "merged spans of days below the window's static bottom band edge" },
        { name: "Selected streak", description: "the archive streak clicked in the table (bordered when it breaks this static edge)" },
      ],
      state: {
        metric: STREAK_METRIC,
        window: streakPeriod ?? "none",
        tightness: streakPct != null ? `${streakPct}%` : "none",
        anchor: streakAnchorIdx != null ? "clicked chart date" : "latest",
        selected_streak:
          selectedStreak != null
            ? `${selectedStreak.side} ${selectedStreak.startDate}→${selectedStreak.endDate}`
            : "none",
      },
      notes: [
        "Break bands are detected client-side against the anchor window's STATIC edge; the DB archive streaks (tested against each month's own moving band) only populate the table.",
        "Shading only covers values inside the drawn zone — break streaks are clipped to the window's vertical extent.",
      ],
    }),
    [code, secType, streakPeriod, streakPct, streakAnchorIdx, selectedStreak],
  );
  const streakAiAskAddon = useAiAskAddon({
    title: "Valuation Streaks (band-break)",
    subtitle: hasStreakData
      ? `${allStreaks.length} streaks across all metric × window × tightness combos`
      : undefined,
    option: chartBuild?.option ?? null,
    spec: chartBuild != null && chartBuild.win != null ? streakAiAskSpec : null,
    getInstance: () => streakChartRef.current,
  });

  // Whether this security has PE & dividend analysis rows — drives the bold
  // highlight of the per-security build button (AnalysisRunButton). Loading
  // counts as "present" so the button doesn't bold-flicker.
  const hasAnalysisData = streaksLoading || allStreaks.length > 0;

  // Refetch after a per-security analysis rebuild (AnalysisRunButton):
  // drop the cached streaks response, then bump the refresh key.
  const handleAnalysisRunCompleted = useCallback(() => {
    invalidateCacheForUrl(
      `/api/analysis/pe/streaks?code=${code}&sec_type=${secType}`,
    );
    setRefreshKey((k) => k + 1);
  }, [code, secType]);

  // The per-security rebuild button — shown on whichever analysis card the
  // active mode renders (it runs the whole pe_and_dividends module either
  // way, refreshing both cards' data sources).
  const rebuildButton = (
    <AnalysisRunButton
      module="pe_and_dividends"
      secType={secType}
      code={code}
      hasData={hasAnalysisData}
      onCompleted={handleAnalysisRunCompleted}
    />
  );
  // ---- Render: baseline plot ---------------------------------------------
  const plotContent = (() => {
    if (bundleLoading) {
      return (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={28} />
        </Box>
      );
    }
    if (bundleError) {
      return (
        <Alert severity="error" variant="filled">
          Failed to load {secType} baseline: {bundleError}
        </Alert>
      );
    }
    if (!bundle) {
      return (
        <Alert severity="warning">
          No {secType.toUpperCase()} baseline data for {code}.
        </Alert>
      );
    }
    // Delegate to the exact same plot component used in /dataviz/*.
    if (secType === "index") {
      return <IndexPanel index={bundle as IndexBundle} />;
    }
    if (secType === "etf") {
      return <EtfMarginPanel etf={bundle as EtfBundle} />;
    }
    return <StockPanel stock={bundle as StockBundle} />;
  })();

  return (
    <Stack spacing={1.5}>
      {/*
        The baseline panels (IndexPanel / EtfMarginPanel / StockPanel) render
        their own ChartCard with title + subtitle + controls, so we don't wrap
        them in another ChartCard here — just render them directly.
      */}
      {plotContent}

      {/* ---- Valuation Streaks (band-break) ----
          The high/low streaks pattern applied to the pe / dividend_yield
          series: a day breaks out when its value is above/below its own
          month's trailing percentile band (analysis.pe_pct / analysis.dividends_pct), and
          a streak is a maximal run of same-side break days with ≤5-day
          in-band gaps bridged. Nested metric → period → pct selection lists
          the DB archive rows for that combo. */}
      <ChartCard
        title="Valuation Streaks (band-break)"
        subtitle={
          hasStreakData
            ? `${allStreaks.length} streaks across all metric × window × tightness combos — pick one to list them`
            : undefined
        }
        titleAddon={streakAiAskAddon}
        action={rebuildButton}
        height={undefined}
      >
        {streaksLoading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={28} />
          </Box>
        )}
        {streaksError && (
          <Alert severity="error" variant="filled">
            Failed to load streaks: {streaksError}
          </Alert>
        )}
        {!streaksLoading && !streaksError && !hasStreakData && (
          <Alert severity="warning">
            No streak data for {code}. (Streaks are computed by the Python
            build script against the monthly percentile bands — run{" "}
            <code>python -m analyze.pe_and_dividends</code> to build
            analysis.pe_pct_streaks / analysis.dividends_pct_streaks.)
          </Alert>
        )}
        {!streaksLoading && !streaksError && hasStreakData && (
          <>
            <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
              {/* Band lookback period (observations) — the metric is fixed
                  to PE (no metric filter row). */}
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, flexWrap: "wrap" }}>
                <Typography
                  variant="caption"
                  component="span"
                  sx={{
                    fontSize: "0.65rem",
                    minWidth: 88,
                    color: streakPeriod != null ? "primary.main" : "text.secondary",
                    fontWeight: streakPeriod != null ? 700 : 400,
                  }}
                >
                  Window
                </Typography>
                {STREAK_PERIODS.map((w) => (
                  <Chip
                    key={w}
                    label={`${w}d`}
                    size="small"
                    clickable
                    color={streakPeriod === w ? "primary" : "default"}
                    variant={streakPeriod === w ? "filled" : "outlined"}
                    onClick={() => toggleStreakPeriod(w)}
                    sx={{ fontSize: "0.7rem", height: 22 }}
                  />
                ))}
              </Box>
              {/* Band tightness pct. */}
              {streakPeriod != null && (
                <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, flexWrap: "wrap" }}>
                  <Typography
                    variant="caption"
                    component="span"
                    sx={{
                      fontSize: "0.65rem",
                      minWidth: 88,
                      color: streakPct != null ? "primary.main" : "text.secondary",
                      fontWeight: streakPct != null ? 700 : 400,
                    }}
                  >
                    Tightness
                  </Typography>
                  {STREAK_PCTS.map((p) => (
                    <Chip
                      key={p}
                      label={`${p}%`}
                      size="small"
                      clickable
                      color={streakPct === p ? "primary" : "default"}
                      variant={streakPct === p ? "filled" : "outlined"}
                      onClick={() => toggleStreakPct(p)}
                      sx={{ fontSize: "0.7rem", height: 22 }}
                    />
                  ))}
                </Box>
              )}
            </Box>
            {streakPeriod != null && streakPct == null && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
              >
                pick a band tightness (pct) to list streaks
              </Typography>
            )}
            {streakPeriod != null && streakPct != null && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
              >
                PE breaks its
                trailing {streakPeriod}-obs top/bottom {streakPct}% band (each day vs its
                own month's band · ≤{STREAK_GAP_TOLERANCE}-day in-band gaps bridged) ·{" "}
                {selectedStreaks.length === 0
                  ? "no streaks for this combo"
                  : `high ${streakSummary?.high} · low ${streakSummary?.low}`}
              </Typography>
            )}
            {/* ---- Streak shading chart (MaSpread style) ----
                Light purple / yellow = the anchor window's static top/bottom
                pct% zones; darker bands = break streaks detected CLIENT-SIDE
                against that same edge (the guide's "shading ⊆ values inside
                the zone" guarantee). Clicking a streak row above or a chart
                date re-anchors the trailing window. */}
            {chartBuild != null && chartBuild.win != null && (
              <>
                <EChart
                  option={chartBuild.option}
                  height={300}
                  onCanvasClick={handleStreakChartClick}
                  onReady={(c) => {
                    streakChartRef.current = c;
                  }}
                />
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
                >
                  light purple/yellow = trailing {streakPeriod}-obs top/bottom{" "}
                  {streakPct}% zones of PE (
                  {chartBuild.win.startDate} → {chartBuild.win.endDate}), darker = break
                  streaks vs that static edge (≤{STREAK_GAP_TOLERANCE}-day bridge ·
                  high {chartBuild.streaks?.high.length ?? 0} / low{" "}
                  {chartBuild.streaks?.low.length ?? 0}) ·{" "}
                  {selectedStreak != null
                    ? `selected ${selectedStreak.side} ${selectedStreak.startDate}→${selectedStreak.endDate}${
                        chartBuild.emphasized
                          ? " (bordered)"
                          : " — not a break vs this static edge"
                      }`
                    : streakAnchorIdx != null
                      ? `anchored to ${chartBuild.win.endDate} (click again to clear)`
                      : "click a streak row or a chart date to anchor the window"}
                </Typography>
              </>
            )}
            {streakPeriod != null && streakPct != null && selectedStreaks.length > 0 && (
              <TableContainer component={Box} sx={expandedTableContainerSx(360)}>
                <Table size="small" stickyHeader>
                  <TableHead>
                    <TableRow>
                      <TableCell sx={expandedTableHeadCellSx}>Side</TableCell>
                      <TableCell sx={expandedTableHeadCellSx}>Start</TableCell>
                      <TableCell sx={expandedTableHeadCellSx}>End</TableCell>
                      <TableCell sx={expandedTableHeadCellSx} align="right">Days</TableCell>
                      <TableCell sx={expandedTableHeadCellSx} align="right">Start Val</TableCell>
                      <TableCell sx={expandedTableHeadCellSx} align="right">End Val</TableCell>
                      <TableCell sx={expandedTableHeadCellSx} align="right">Max</TableCell>
                      <TableCell sx={expandedTableHeadCellSx} align="right">Min</TableCell>
                      <TableCell sx={expandedTableHeadCellSx} align="right">Std</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {selectedStreaks.slice(0, STREAK_TABLE_CAP).map((s, idx) => {
                      const isSel = selectedStreak != null && streakKey(selectedStreak) === streakKey(s);
                      return (
                        <TableRow
                          key={`${s.startDate}-${s.endDate}`}
                          onClick={() => handleStreakRowClick(s)}
                          sx={{
                            ...expandedTableBodyRowSx(idx),
                            cursor: "pointer",
                            ...(isSel ? { bgcolor: "action.selected" } : {}),
                          }}
                        >
                          <TableCell sx={expandedTableBodyCellSx}>
                            <Typography
                              variant="caption"
                              component="span"
                              sx={{
                                fontSize: "0.7rem",
                                fontWeight: 700,
                                color: s.side === "high" ? STREAK_HIGH_ACCENT : STREAK_LOW_ACCENT,
                              }}
                            >
                              {s.side}
                            </Typography>
                          </TableCell>
                          <TableCell sx={expandedTableBodyCellSx}>{s.startDate}</TableCell>
                          <TableCell sx={expandedTableBodyCellSx}>{s.endDate}</TableCell>
                          <TableCell align="right" sx={expandedTableNumCellSx}>{s.dayCount}</TableCell>
                          <TableCell align="right" sx={expandedTableNumCellSx}>
                            {fmtStreakValue(s.startValue)}
                          </TableCell>
                          <TableCell align="right" sx={expandedTableNumCellSx}>
                            {fmtStreakValue(s.endValue)}
                          </TableCell>
                          <TableCell align="right" sx={expandedTableNumCellSx}>
                            {fmtStreakValue(s.maxValue)}
                          </TableCell>
                          <TableCell align="right" sx={expandedTableNumCellSx}>
                            {fmtStreakValue(s.minValue)}
                          </TableCell>
                          <TableCell align="right" sx={expandedTableNumCellSx}>
                            {fmtNum(s.stdDev, 3)}
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
            {streakPeriod != null && streakPct != null && selectedStreaks.length > STREAK_TABLE_CAP && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}>
                showing the {STREAK_TABLE_CAP} most recent of {selectedStreaks.length} streaks
              </Typography>
            )}
          </>
        )}
      </ChartCard>
    </Stack>
  );
}
