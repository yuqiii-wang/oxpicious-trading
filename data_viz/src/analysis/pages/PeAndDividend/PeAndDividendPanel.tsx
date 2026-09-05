/**
 * PeAndDividendPanel — one card per code: the EXACT data-viz baseline plot
 * for the security on top, monthly PE & Dividend stats table beneath.
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
 * Clicking any date on the plot fires onDateClick → the monthly stats table
 * beneath highlights the row whose month-end contains the clicked date and
 * scrolls it into view.
 *
 * The monthly PE & Dividend stats table (analysis.pe_and_dividend_stats) is
 * rendered beneath the plot: one row per month-end snapshot, most recent
 * first. is_active row is tagged with a "latest" chip.
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
import ChartCard from "@/components/ChartCard";
import AnalysisRunButton from "@/components/AnalysisRunButton";
import EChart from "@/components/EChart";
import IndexPanel from "@/dataviz/features/index-baseline/IndexPanel";
import EtfMarginPanel from "@/dataviz/features/etf-margin/EtfMarginPanel";
import StockPanel from "@/dataviz/features/stock-baseline/StockPanel";
import { DIVIDEND_COLOR, PE_COLOR } from "@/theme/chart-palette";
import { fmtNum, fmtPct } from "@/lib/series";
import {
  fetchIndicesCombined,
  fetchEtfMarginCombined,
  fetchStocksCombined,
  fetchPeAndDividendStats,
  fetchPeAndDividendStreaks,
  fetchPeAndDividendChart,
  invalidateCacheForUrl,
} from "@/lib/api-client";
import type {
  IndexBundle,
  EtfBundle,
  StockBundle,
  PeAndDividendStatsResponse,
  PeAndDividendStatsRow,
  PeAndDividendStreaksResponse,
  PeAndDividendStreak,
  PeAndDividendStreakMetric,
  PeAndDividendChartResponse,
} from "@shared/types";
import { buildStreakChartOption, type MetricObsRow } from "./chartOption/streakChartOption";
import type { PanelProps } from "./types";
import {
  expandedTableBodyCellSx,
  expandedTableBodyRowSx,
  expandedTableContainerSx,
  expandedTableHeadCellSx,
  expandedTableNumCellSx,
} from "@/shared/styles/expanded-table-styles";
import useTableHeaderFilters, { type HeaderFilterDef } from "@/hooks/table-header-filters";

/** Format a YYYY-MM-DD date as a short YYYY-MM string for month display. */
function fmtMonth(dateStr: string): string {
  return dateStr.length >= 7 ? dateStr.slice(0, 7) : dateStr;
}

/** Return the YYYY-MM key for a YYYY-MM-DD date string. */
function monthKey(dateStr: string): string {
  return dateStr.slice(0, 7);
}

// ---- Band-break excursion streaks (analysis.pe_and_dividend_pct_streaks,
// the mov_ave_high_low_pct_streaks pattern applied to pe_ma20 /
// dividend_yield) — nested metric → period → pct selection mirrors the
// MaSpread High/Low Streaks buttons. ----

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

const STREAK_METRICS: Array<{ value: PeAndDividendStreakMetric; label: string }> = [
  { value: "pe_ma20", label: "PE MA20" },
  { value: "dividend_yield", label: "Div Yield" },
];

/** Format one streak's metric value: dividend_yield is a FRACTIONAL ratio
 *  (0.035 = 3.5%) — scale to percent for display; pe_ma20 is a plain
 *  multiple. */
function fmtStreakValue(
  metric: PeAndDividendStreakMetric,
  v: number | null | undefined,
): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return metric === "dividend_yield" ? `${fmtNum(v * 100, 2)}%` : fmtNum(v, 2);
}

/** Opt-in per-column header filters — Month is a date-range selector (month
 *  granularity over the month-end snapshot dates), Active is a discrete
 *  label (ticks), the rolling 5y metrics are continuous magnitudes
 *  (numeric range). */
const FILTER_DEFS: HeaderFilterDef<PeAndDividendStatsRow>[] = [
  { key: "month", label: "Month", type: "date", granularity: "month", value: (r) => r.date.slice(0, 7) },
  { key: "active", label: "Active", type: "ticks", value: (r) => (r.is_active ? "latest" : "earlier") },
  { key: "min_pe", label: "Min PE 5y", type: "range", value: (r) => r.min_pe_5y },
  { key: "max_pe", label: "Max PE 5y", type: "range", value: (r) => r.max_pe_5y },
  { key: "div_var", label: "Div Var 5y", type: "range", value: (r) => r.dividend_var_5y },
  { key: "last_div", label: "Last Div", type: "range", value: (r) => r.last_dividend_per_share },
  { key: "div_stab", label: "Div Stability 5y", type: "range", value: (r) => r.dividend_stability_5y },
];
const DEF_BY_KEY = new Map(FILTER_DEFS.map((d) => [d.key, d]));

export function PeAndDividendPanel({
  code,
  secType,
  themeMode,
}: PanelProps) {
  // ---- Security baseline bundle (IndexBundle | EtfBundle | StockBundle) ---
  const [bundle, setBundle] = useState<IndexBundle | EtfBundle | StockBundle | null>(null);
  const [bundleLoading, setBundleLoading] = useState(false);
  const [bundleError, setBundleError] = useState<string | null>(null);

  // ---- Stats data ---------------------------------------------------------
  const [statsData, setStatsData] = useState<PeAndDividendStatsResponse | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState<string | null>(null);

  // ---- Band-break excursion streaks data ----------------------------------
  const [streakData, setStreakData] = useState<PeAndDividendStreaksResponse | null>(null);
  const [streaksLoading, setStreaksLoading] = useState(false);
  const [streaksError, setStreaksError] = useState<string | null>(null);

  // ---- Daily chart rows (observation series for the streak shading) -------
  const [chartData, setChartData] = useState<PeAndDividendChartResponse | null>(null);

  // Nested single-select streak combo: layer 1 = metric (null = off),
  // layer 2 = band lookback period, layer 3 = band tightness pct.
  const [streakMetric, setStreakMetric] = useState<PeAndDividendStreakMetric | null>(null);
  const [streakPeriod, setStreakPeriod] = useState<number | null>(null);
  const [streakPct, setStreakPct] = useState<number | null>(null);

  // Streak shading anchor (index into the selected metric's observation
  // rows; null = the latest row) + the clicked archive streak (drives the
  // table highlight and the bordered band on the chart).
  const [streakAnchorIdx, setStreakAnchorIdx] = useState<number | null>(null);
  const [selectedStreak, setSelectedStreak] = useState<PeAndDividendStreak | null>(null);

  // Bumped by the per-security AnalysisRunButton after a rebuild run —
  // retriggers the stats fetch (the cache entry is invalidated first in
  // the completion handler).
  const [refreshKey, setRefreshKey] = useState(0);

  // Clicked date from the plot — drives the table highlight + scroll-into-view.
  const [clickedDate, setClickedDate] = useState<string | null>(null);

  // Ref to the table row that should scroll into view when the highlight
  // changes (set by the chart click handler).
  const highlightedRowRef = useRef<HTMLTableRowElement | null>(null);

  // Fetch the security baseline bundle on mount and whenever code/sec_type
  // changes. Uses the SAME combined endpoints as /dataviz/index-baseline,
  // /dataviz/etf-margin, /dataviz/stock-baseline — page_size=1 + code filter
  // returns just the one security.
  useEffect(() => {
    let cancelled = false;
    setBundleLoading(true);
    setBundleError(null);
    setBundle(null);
    const p =
      secType === "index"
        ? fetchIndicesCombined(null, null, null, null, 1, 1, code, null).then((r) => r.indices[0] ?? null)
        : secType === "etf"
          ? fetchEtfMarginCombined(null, null, null, null, undefined, 1, 1, code, null).then((r) => r.etfs[0] ?? null)
          : fetchStocksCombined(null, null, null, null, 1, 1, code, null).then((r) => r.stocks[0] ?? null);
    p.then((b) => {
      if (cancelled) return;
      setBundle(b);
      setBundleLoading(false);
    }).catch((e: Error) => {
      if (cancelled) return;
      setBundleError(e.message);
      setBundleLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [code, secType]);

  // Fetch stats data on mount and whenever code/sec_type changes.
  useEffect(() => {
    let cancelled = false;
    setStatsLoading(true);
    setStatsError(null);
    fetchPeAndDividendStats(code, secType)
      .then((data) => {
        if (cancelled) return;
        setStatsData(data);
        setStatsLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setStatsError(e.message);
        setStatsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [code, secType, refreshKey]);

  // Fetch the band-break excursion streaks + daily chart rows (parallel
  // with stats) — same deps, so a per-security rebuild refreshes all.
  useEffect(() => {
    let cancelled = false;
    setStreaksLoading(true);
    setStreaksError(null);
    fetchPeAndDividendStreaks(code, secType)
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
    fetchPeAndDividendChart(code, secType)
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

  // Reset clicked date + streak combo/anchor when the code changes.
  useEffect(() => {
    setClickedDate(null);
    setStreakMetric(null);
    setStreakPeriod(null);
    setStreakPct(null);
    setStreakAnchorIdx(null);
    setSelectedStreak(null);
  }, [code, secType]);

  // ---- Streak section: availability + selected-combo subset --------------
  const allStreaks = useMemo(() => streakData?.streaks ?? [], [streakData]);
  const hasStreakData = allStreaks.length > 0;

  const toggleStreakMetric = useCallback((m: PeAndDividendStreakMetric) => {
    // The anchor indexes into the selected metric's observation rows, so a
    // metric switch invalidates it (and the selected archive streak).
    setStreakAnchorIdx(null);
    setSelectedStreak(null);
    setStreakMetric((prev) => {
      if (prev === m) {
        setStreakPeriod(null);
        setStreakPct(null);
        return null;
      }
      return m;
    });
  }, []);

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
    if (streakMetric == null || streakPeriod == null || streakPct == null) return [];
    return allStreaks
      .filter((s) => s.metric === streakMetric && s.period === streakPeriod && s.pctType === streakPct)
      .sort((a, b) => (a.startDate < b.startDate ? 1 : -1));
  }, [allStreaks, streakMetric, streakPeriod, streakPct]);

  // Caption summary for the selected combo: per-side streak count, total
  // days, extreme value and the first couple of spans (MaSpread caption
  // style).
  const streakSummary = useMemo(() => {
    if (streakMetric == null || streakPeriod == null || streakPct == null) return null;
    const high = selectedStreaks.filter((s) => s.side === "high");
    const low = selectedStreaks.filter((s) => s.side === "low");
    const spanList = (arr: PeAndDividendStreak[]) =>
      arr.length === 0
        ? "none"
        : `${arr.length} streak${arr.length === 1 ? "" : "s"} · ${arr.reduce((a, s) => a + s.dayCount, 0)}d · peak ${fmtStreakValue(streakMetric, Math.max(...arr.map((s) => s.maxValue)))} · ${arr.slice(0, 2).map((s) => `${s.startDate.slice(2)}→${s.endDate.slice(2)}`).join(", ")}${arr.length > 2 ? `, +${arr.length - 2} more` : ""}`;
    return { high: spanList(high), low: spanList(low) };
  }, [selectedStreaks, streakMetric, streakPeriod, streakPct]);

  // ---- Streak shading chart (MaSpread-style window zones + break bands) --
  // Non-NULL observations of the selected metric — the series the shading
  // is detected and drawn against.
  const obsRows = useMemo<MetricObsRow[]>(() => {
    if (streakMetric == null || chartData == null) return [];
    return chartData.rows
      .filter((r) => r[streakMetric] != null && Number.isFinite(r[streakMetric]!))
      .map((r) => ({ date: r.date, value: r[streakMetric]! }));
  }, [chartData, streakMetric]);

  /** Stable identity for an archive streak (table-row highlight state). */
  const streakKey = (s: PeAndDividendStreak): string =>
    `${s.metric}|${s.period}|${s.pctType}|${s.startDate}|${s.endDate}|${s.side}`;

  // Clicking a streak row: anchor the shading window at the streak's end
  // date, border its detected counterpart on the chart. Clicking the
  // selected row again clears the anchor (back to the latest window).
  const handleStreakRowClick = useCallback(
    (s: PeAndDividendStreak) => {
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
      metric: streakMetric!,
      period: streakPeriod,
      pct: streakPct,
      anchorIdx: streakAnchorIdx,
      selectedStreak,
      themeMode,
    });
  }, [obsRows, streakMetric, streakPeriod, streakPct, streakAnchorIdx, selectedStreak, themeMode]);

  // ---- Stats table: highlight + scroll-into-view --------------------------
  // Find the stats row whose month-end is the latest one <= clickedDate.
  const statsRows: PeAndDividendStatsRow[] = statsData?.rows ?? [];

  // Opt-in header filters over the monthly snapshots (reset on scope change).
  const { filtered: visibleStatsRows, menuFor } = useTableHeaderFilters(
    FILTER_DEFS,
    statsRows,
    [code, secType, refreshKey],
  );

  // Whether this security has PE & dividend analysis rows — drives the bold
  // highlight of the per-security build button (AnalysisRunButton). Loading
  // counts as "present" so the button doesn't bold-flicker.
  const hasAnalysisData = statsLoading || statsRows.length > 0;

  // Refetch after a per-security analysis rebuild (AnalysisRunButton):
  // drop the cached stats/streaks responses, then bump the refresh key.
  const handleAnalysisRunCompleted = useCallback(() => {
    invalidateCacheForUrl(
      `/api/analysis/pe-and-dividend/stats?code=${code}&sec_type=${secType}`,
    );
    invalidateCacheForUrl(
      `/api/analysis/pe-and-dividend/streaks?code=${code}&sec_type=${secType}`,
    );
    setRefreshKey((k) => k + 1);
  }, [code, secType]);
  const highlightedStatsRowDate = useMemo(() => {
    if (!clickedDate || statsRows.length === 0) return null;
    for (const r of statsRows) {
      if (r.date <= clickedDate) return r.date;
    }
    return null;
  }, [clickedDate, statsRows]);

  useEffect(() => {
    if (!highlightedStatsRowDate) return;
    const t = setTimeout(() => {
      highlightedRowRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 50);
    return () => clearTimeout(t);
  }, [highlightedStatsRowDate]);

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
    // onDateClick fires for any click on the chart → highlights the matching
    // month-end row in the stats table below.
    if (secType === "index") {
      return <IndexPanel index={bundle as IndexBundle} themeMode={themeMode} onDateClick={setClickedDate} />;
    }
    if (secType === "etf") {
      return <EtfMarginPanel etf={bundle as EtfBundle} onDateClick={setClickedDate} />;
    }
    return <StockPanel stock={bundle as StockBundle} onDateClick={setClickedDate} />;
  })();

  return (
    <Stack spacing={1.5}>
      {/*
        The baseline panels (IndexPanel / EtfMarginPanel / StockPanel) render
        their own ChartCard with title + subtitle + controls, so we don't wrap
        them in another ChartCard here — just render them directly.
      */}
      {plotContent}

      {/* ---- Band-break excursion streaks (analysis.pe_and_dividend_pct_streaks) ----
          The high/low streaks pattern applied to the pe_ma20 / dividend_yield
          series: a day breaks out when its value is above/below its own
          month's trailing percentile band (analysis.pe_and_dividend_pct), and
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
            analysis.pe_and_dividend_pct_streaks.)
          </Alert>
        )}
        {!streaksLoading && !streaksError && hasStreakData && (
          <>
            <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
              {/* Layer 1 — metric (pe_ma20 / dividend_yield). */}
              <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, flexWrap: "wrap" }}>
                <Typography
                  variant="caption"
                  component="span"
                  sx={{
                    fontSize: "0.65rem",
                    minWidth: 88,
                    color: streakMetric != null ? "primary.main" : "text.secondary",
                    fontWeight: streakMetric != null ? 700 : 400,
                  }}
                >
                  Streaks
                </Typography>
                {STREAK_METRICS.map((m) => (
                  <Chip
                    key={m.value}
                    label={m.label}
                    size="small"
                    clickable
                    color={streakMetric === m.value ? "primary" : "default"}
                    variant={streakMetric === m.value ? "filled" : "outlined"}
                    onClick={() => toggleStreakMetric(m.value)}
                    sx={{ fontSize: "0.7rem", height: 22 }}
                  />
                ))}
              </Box>
              {/* Layer 2 — band lookback period (observations). */}
              {streakMetric != null && (
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
              )}
              {/* Layer 3 — band tightness pct. */}
              {streakMetric != null && streakPeriod != null && (
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
            {streakMetric != null && streakPeriod != null && streakPct == null && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
              >
                pick a band tightness (pct) to list streaks
              </Typography>
            )}
            {streakMetric != null && streakPeriod != null && streakPct != null && (
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
              >
                {streakMetric === "pe_ma20" ? "PE MA20" : "dividend yield"} breaks its
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
                />
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}
                >
                  light purple/yellow = trailing {streakPeriod}-obs top/bottom{" "}
                  {streakPct}% zones of{" "}
                  {streakMetric === "pe_ma20" ? "PE MA20" : "dividend yield"} (
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
            {streakMetric != null && streakPeriod != null && streakPct != null && selectedStreaks.length > 0 && (
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
                            {fmtStreakValue(streakMetric!, s.startValue)}
                          </TableCell>
                          <TableCell align="right" sx={expandedTableNumCellSx}>
                            {fmtStreakValue(streakMetric!, s.endValue)}
                          </TableCell>
                          <TableCell align="right" sx={expandedTableNumCellSx}>
                            {fmtStreakValue(streakMetric!, s.maxValue)}
                          </TableCell>
                          <TableCell align="right" sx={expandedTableNumCellSx}>
                            {fmtStreakValue(streakMetric!, s.minValue)}
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
            {streakMetric != null && streakPeriod != null && streakPct != null && selectedStreaks.length > STREAK_TABLE_CAP && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.5, fontSize: "0.65rem" }}>
                showing the {STREAK_TABLE_CAP} most recent of {selectedStreaks.length} streaks
              </Typography>
            )}
          </>
        )}
      </ChartCard>

      {/* ---- Monthly PE & Dividend stats table ---- */}
      <ChartCard
        title="Monthly PE & Dividend Stats (5y rolling)"
        subtitle={
          statsData
            ? `${statsRows.length} month-end snapshots · click a date on the chart above to highlight the matching month`
            : undefined
        }
        action={
          <AnalysisRunButton
            module="pe_and_dividends"
            secType={secType}
            code={code}
            hasData={hasAnalysisData}
            onCompleted={handleAnalysisRunCompleted}
          />
        }
        height={undefined}
      >
        {statsLoading && (
          <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
            <CircularProgress size={28} />
          </Box>
        )}
        {statsError && (
          <Alert severity="error" variant="filled">
            Failed to load stats: {statsError}
          </Alert>
        )}
        {!statsLoading && !statsError && statsRows.length === 0 && (
          <Alert severity="warning">
            No monthly stats for {code}. (Stats are computed monthly by the
            Python build script — run it once a month after the 5y window
            updates.)
          </Alert>
        )}
        {!statsLoading && !statsError && statsRows.length > 0 && (
          <TableContainer
            component={Box}
            sx={expandedTableContainerSx(360)}
          >
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell sx={expandedTableHeadCellSx}>
                    {menuFor(DEF_BY_KEY.get("month")!)}
                  </TableCell>
                  <TableCell sx={expandedTableHeadCellSx} align="center">
                    {menuFor(DEF_BY_KEY.get("active")!)}
                  </TableCell>
                  <TableCell sx={expandedTableHeadCellSx} align="right">
                    {menuFor(DEF_BY_KEY.get("min_pe")!)}
                  </TableCell>
                  <TableCell sx={expandedTableHeadCellSx} align="right">
                    {menuFor(DEF_BY_KEY.get("max_pe")!)}
                  </TableCell>
                  <TableCell sx={expandedTableHeadCellSx} align="right">
                    {menuFor(DEF_BY_KEY.get("div_var")!)}
                  </TableCell>
                  <TableCell sx={expandedTableHeadCellSx} align="right">
                    {menuFor(DEF_BY_KEY.get("last_div")!)}
                  </TableCell>
                  <TableCell sx={expandedTableHeadCellSx} align="right">
                    {menuFor(DEF_BY_KEY.get("div_stab")!)}
                  </TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {visibleStatsRows.map((r, idx) => {
                  const isHighlighted = r.date === highlightedStatsRowDate;
                  const isActive = r.is_active;
                  // Bold the Last Div cell when a dividend was issued in
                  // this month — surfaces dividend-event months at a glance.
                  const boldDiv = r.dividend_issued_this_month === true;
                  return (
                    <TableRow
                      key={r.date}
                      ref={isHighlighted ? highlightedRowRef : undefined}
                      sx={{
                        ...expandedTableBodyRowSx(idx),
                        ...(isHighlighted
                          ? { bgcolor: "action.selected" }
                          : isActive
                            ? { bgcolor: "action.hover" }
                            : {}),
                      }}
                    >
                      <TableCell sx={{ ...expandedTableBodyCellSx, fontWeight: isHighlighted ? 700 : 500 }}>
                        {fmtMonth(r.date)}
                      </TableCell>
                      <TableCell align="center" sx={{ ...expandedTableBodyCellSx, py: 0.5 }}>
                        {isActive && (
                          <Chip
                            label="latest"
                            size="small"
                            color="primary"
                            sx={{ height: 18, fontSize: "0.65rem" }}
                          />
                        )}
                      </TableCell>
                      <TableCell align="right" sx={expandedTableNumCellSx}>{fmtNum(r.min_pe_5y)}</TableCell>
                      <TableCell align="right" sx={expandedTableNumCellSx}>{fmtNum(r.max_pe_5y)}</TableCell>
                      <TableCell align="right" sx={expandedTableNumCellSx}>{fmtPct(r.dividend_var_5y)}</TableCell>
                      <TableCell
                        align="right"
                        sx={{
                          ...expandedTableNumCellSx,
                          color: DIVIDEND_COLOR,
                          fontWeight: boldDiv ? 700 : 400,
                        }}
                      >
                        {fmtNum(r.last_dividend_per_share, 4)}
                      </TableCell>
                      <TableCell align="right" sx={{ ...expandedTableNumCellSx, color: PE_COLOR }}>
                        {r.dividend_stability_5y != null
                          ? fmtNum(r.dividend_stability_5y, 1)
                          : "—"}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        {clickedDate && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 1, display: "block" }}>
            Clicked date <b>{clickedDate}</b> → highlighted month{" "}
            <b>{highlightedStatsRowDate ? monthKey(highlightedStatsRowDate) : "(none — before earliest stats)"}</b>
          </Typography>
        )}
      </ChartCard>
    </Stack>
  );
}
