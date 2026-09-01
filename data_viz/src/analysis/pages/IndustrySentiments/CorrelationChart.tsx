/**
 * Correlation chart — expandable section below the main multi-line chart.
 *
 * Renders one line per industry pair, showing the windowed Pearson
 * correlation of the two industries' MA curves of mean_close over the
 * user-selected window (20d / 60d / 255d): corr_ma{W}_{W}d correlates the
 * two industries' MA-W curves over the W trading days starting on each
 * grid start_date (window starts every `interval` — default 20 — trading
 * days). Hover shows the correlation value(s) at the hovered start date.
 *
 * Auto-expanded by the parent plot when 2+ industries are selected — there
 * are no pairs to correlate below that threshold. This component is only
 * rendered inside the Collapse when open.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  IconButton,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import RefreshIcon from "@mui/icons-material/Refresh";
import EChart from "@/components/EChart";
import {
  fetchIndustryCorrelations,
  runIndustryCorrelationsRefresh,
  fetchAnalysisRunStatus,
  INDUSTRY_CORR_RUN_TAG,
  invalidateCacheForPrefix,
} from "@/lib/api-client";
import type { IndustryCorrelationsResponse } from "@shared/types";
import type { EChartsOption } from "echarts";
import {
  MUTED_PALETTE,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { renderReactElement, tooltipComponents } from "@/lib/react-tooltip-renderer";
import type { CorrelationChartProps, CorrWindow } from "./types";
import { CORR_WINDOWS } from "./constants";

export function CorrelationChart({
  industryIds,
  codes,
  poolSize,
  themeMode,
}: CorrelationChartProps) {
  const [data, setData] = useState<IndustryCorrelationsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [window, setWindow] = useState<CorrWindow>("60d");

  // ---- Refresh (filtered corr recompute for the chosen data) ----
  // POST /industry-correlations/run spawns
  // `python -m analyze.industry_sentiments.corr --industry ... --code ...`
  // and WAITS for it; while waiting the button spins (and polls the tag
  // status so a page refresh restores the spinner). On success the data
  // is refetched (refreshTick bumps the fetch effect's deps).
  const [refreshing, setRefreshing] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  // Last (selection, pool) key the auto-recompute ran for — guards the
  // auto-trigger below so a pair that legitimately has no rows after a
  // recompute doesn't loop (the key only changes when the user changes the
  // selection or pool, which allows one fresh attempt).
  const autoRunKeyRef = useRef<string | null>(null);
  const industryIdsRef = useRef(industryIds);
  const codesRef = useRef(codes);
  industryIdsRef.current = industryIds;
  codesRef.current = codes;

  // True between "the run tag was seen running" and "seen finished" — gates
  // the poll's finish-transition side effect (cache invalidate + refetch) so
  // it fires once per completed run, including runs deduped (already_running)
  // or started before a page refresh (spinner restore path).
  const wasRunningRef = useRef(false);

  // Poll the run tag while refreshing — also on mount once, so a run
  // started elsewhere (or a page refresh mid-run) restores the spinner.
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const st = await fetchAnalysisRunStatus([INDUSTRY_CORR_RUN_TAG]);
        if (cancelled) return;
        const running = Boolean(st[INDUSTRY_CORR_RUN_TAG]);
        if (wasRunningRef.current && !running) {
          // A tracked run just finished — drop cached GET rows for this
          // endpoint (TTL-cached, no version check) and refetch fresh.
          invalidateCacheForPrefix("/api/analysis/industry-correlations");
          setRefreshTick((n) => n + 1);
        }
        wasRunningRef.current = running;
        setRefreshing(running);
      } catch {
        /* status is best-effort */
      }
    };
    void poll();
    if (!refreshing) return;
    const t = setInterval(() => { void poll(); }, 3000);
    return () => { cancelled = true; clearInterval(t); };
  }, [refreshing]);

  const handleRefresh = useCallback(async () => {
    setRefreshError(null);
    setRefreshing(true);
    wasRunningRef.current = true;
    const result = await runIndustryCorrelationsRefresh(
      industryIdsRef.current,
      codesRef.current,
    );
    if (result.already_running) {
      // A run with the same tag is already in flight — keep the spinner
      // polling; the poll effect invalidates + refetches when it finishes.
      return;
    }
    setRefreshing(false);
    wasRunningRef.current = false;
    if (!result.success) {
      setRefreshError(
        `Corr recompute failed${result.stderr_tail ? `: ${result.stderr_tail.slice(-300)}` : ""}`,
      );
      return;
    }
    // Recompute done — drop the cached GET response (this endpoint is
    // TTL-cached with no version check, so without invalidation the
    // refetch would serve the pre-run rows for up to 10 min).
    invalidateCacheForPrefix("/api/analysis/industry-correlations");
    setRefreshTick((n) => n + 1);
  }, []);

  // Stable key for the fetch effect — refetch when industry set or pool changes.
  const idsKey = industryIds.slice().sort().join(",");
  useEffect(() => {
    if (industryIds.length < 2) {
      setData(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchIndustryCorrelations(industryIds, poolSize)
      .then((resp) => {
        if (cancelled) return;
        setData(resp);
        setLoading(false);
        // Auto-trigger the on-demand recompute when the selected pair has no
        // materialized rows yet — same flow as the manual refresh button
        // (filtered corr recompute + upsert, then invalidate + refetch via
        // refreshTick). Once per (selection, pool) key: if the recompute
        // legitimately yields no rows, the guard prevents a loop.
        if (resp.correlations.length === 0) {
          const key = `${idsKey}|${poolSize}`;
          if (autoRunKeyRef.current !== key) {
            autoRunKeyRef.current = key;
            void handleRefresh();
          }
        }
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, poolSize, refreshTick]);

  // Build the chart option — one line per industry pair, plotting the
  // windowed correlation for the user-selected window over time. Tooltip
  // shows all 3 windows' values at the hovered start date (richer than
  // just the selected window — lets the user compare short vs long-term
  // co-movement at a glance without toggling windows).
  const option = useMemo<EChartsOption | null>(() => {
    if (!data || data.correlations.length === 0) return null;
    const c = axisColors(themeMode);

    // Group rows by pair key (industry_id, benchmark_industry_id). Each
    // pair becomes one series. Pairs are sorted lexicographically for
    // stable color assignment.
    const pairKeys = new Set<string>();
    const byPair = new Map<string, typeof data.correlations>();
    for (const row of data.correlations) {
      const key = `${row.industry_id}\u0000${row.benchmark_industry_id}`;
      pairKeys.add(key);
      let arr = byPair.get(key);
      if (!arr) {
        arr = [];
        byPair.set(key, arr);
      }
      arr.push(row);
    }
    const sortedPairs = Array.from(pairKeys).sort();
    // Sorted union of all pair window start dates — X axis.
    const allDatesSet = new Set<string>();
    for (const row of data.correlations) allDatesSet.add(row.start_date);
    const allDates = Array.from(allDatesSet).sort();

    // Selected window column → numeric value.
    const windowCol: Record<CorrWindow, "corr_ma20_20d" | "corr_ma60_60d" | "corr_ma255_255d"> = {
      "20d": "corr_ma20_20d",
      "60d": "corr_ma60_60d",
      "255d": "corr_ma255_255d",
    };

    const series: Array<Record<string, unknown>> = sortedPairs.map((key, i) => {
      const rows = byPair.get(key)!;
      const byDate = new Map<string, typeof rows[number]>();
      for (const r of rows) byDate.set(r.start_date, r);
      const pair = rows[0];
      const labelA = pair.industry_label || pair.industry_id;
      const labelB = pair.benchmark_industry_label || pair.benchmark_industry_id;
      const shortA = labelA.split("  ")[0] || pair.industry_id;
      const shortB = labelB.split("  ")[0] || pair.benchmark_industry_id;
      const name = `${shortA} ↔ ${shortB}`;
      const color = MUTED_PALETTE[i % MUTED_PALETTE.length];
      const aligned = allDates.map(
        (d) => byDate.get(d)?.[windowCol[window]] ?? null,
      );
      return {
        name,
        type: "line",
        smooth: false,
        showSymbol: false,
        connectNulls: false,
        data: aligned,
        lineStyle: { width: 1.6, color },
        itemStyle: { color },
        z: 3,
      };
    });

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: commonGrid({ left: 56, right: 24, bottom: 32 }),
      legend: commonLegend(themeMode, {
        data: series.map((s) => s.name as string),
      }),
      tooltip: {
        trigger: "axis",
        backgroundColor: c.tooltipBg,
        borderColor: c.splitLineColor,
        textStyle: { color: c.textColor, fontSize: 11 },
        formatter: (params: unknown) => {
          const arr = (Array.isArray(params) ? params : [params]) as Array<{
            dataIndex?: number;
            seriesName?: string;
            value?: number | null;
          }>;
          if (arr.length === 0) return "";
          const idx0 = arr[0].dataIndex ?? 0;
          const dateStr = allDates[idx0] ?? "";
          if (!dateStr) return "";
          const fmtV = (v: number | null | undefined) => {
            if (v == null || !Number.isFinite(v)) return "—";
            return (v >= 0 ? "+" : "") + fmtNum(v, 3);
          };
          const children: React.ReactNode[] = [];
          children.push(React.createElement(tooltipComponents.Header, null, dateStr));
          children.push(React.createElement("div", {
            style: { marginTop: 2, opacity: 0.7 },
          }, "Pairwise Pearson correlation of MA curves (window starts every 20 trading days)"));
          children.push(React.createElement("div", { style: { marginTop: 4 } },
            React.createElement("div", {
              style: { display: "flex", justifyContent: "space-between", gap: 8, opacity: 0.55, fontSize: "0.85em" },
            },
              React.createElement("span", null, "window:"),
              React.createElement("span", null,
                React.createElement(tooltipComponents.Bold, null, window),
                " highlighted · others shown for context",
              ),
            ),
          ));
          const rowChildren: React.ReactNode[] = [];
          for (const p of arr) {
            const key = sortedPairs.find((k) => {
              const rows = byPair.get(k);
              if (!rows || rows.length === 0) return false;
              const r0 = rows[0];
              const shortA = (r0.industry_label || r0.industry_id).split("  ")[0] || r0.industry_id;
              const shortB = (r0.benchmark_industry_label || r0.benchmark_industry_id).split("  ")[0] || r0.benchmark_industry_id;
              return `${shortA} ↔ ${shortB}` === p.seriesName;
            });
            if (!key) continue;
            const rows = byPair.get(key)!;
            const r = rows.find((x) => x.start_date === dateStr);
            if (!r) continue;
            const pairIdx = sortedPairs.indexOf(key);
            const color = MUTED_PALETTE[pairIdx % MUTED_PALETTE.length];
            const makeChip = (w: CorrWindow, v: number | null) => {
              const isSel = w === window;
              const style: React.CSSProperties = isSel
                ? { fontWeight: 700 }
                : { opacity: 0.55, fontSize: "0.85em" };
              return React.createElement("span", { style },
                `${w}:${v == null || !Number.isFinite(v) ? "—" : fmtV(v)}`,
              );
            };
            rowChildren.push(React.createElement("div", {
              style: { display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" },
            },
              React.createElement("span", { style: { color } }, "●"),
              React.createElement("span", { style: { flex: 1 } }, p.seriesName ?? ""),
              React.createElement("span", {
                style: { display: "flex", gap: 6, alignItems: "baseline" },
              },
                makeChip("20d", r.corr_ma20_20d),
                makeChip("60d", r.corr_ma60_60d),
                makeChip("255d", r.corr_ma255_255d),
              ),
            ));
          }
          children.push(React.createElement("div", { style: { marginTop: 4 } }, rowChildren));
          return renderReactElement(React.createElement(React.Fragment, null, children));
        },
      },
      xAxis: {
        type: "category",
        data: allDates,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: string) => v.slice(0, 7),
        },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        min: -1,
        max: 1,
        name: "Correlation",
        nameTextStyle: { color: c.textColor, fontSize: 9 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 9,
          formatter: (v: number) => fmtNum(v, 2),
        },
        splitLine: { lineStyle: { color: c.splitLineColor, type: "dashed", opacity: 0.4 } },
      },
      series,
    };
  }, [data, themeMode, window]);

  const numPairs = data
    ? new Set(data.correlations.map((r) => `${r.industry_id}|${r.benchmark_industry_id}`)).size
    : 0;
  // Fewer than 2 effective industries (industries + strategy themes +
  // L3-derived) — no pairs exist to correlate; show a hint instead of
  // fetching (the API would reject the request).
  const insufficient = industryIds.length < 2;

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 1, flexWrap: "wrap", gap: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          Pairwise Correlation of Industry Mean Sentiments
          {data ? ` — ${numPairs} pair${numPairs === 1 ? "" : "s"} · ${data.correlations.length.toLocaleString()} rows · pool=${poolSize} · stride=${data.correlations[0]?.interval ?? 20}d` : ""}
        </Typography>
        {!insufficient && (
          <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
            <ToggleButtonGroup
              value={window}
              exclusive
              size="small"
              onChange={(_, v: CorrWindow | null) => v && setWindow(v)}
            >
              {CORR_WINDOWS.map((w) => (
                <ToggleButton key={w} value={w}>{w}</ToggleButton>
              ))}
            </ToggleButtonGroup>
            <Tooltip
              title={
                refreshing
                  ? "Recomputing correlations for the chosen industries…"
                  : "Recompute + upsert correlations for the chosen industries / indices"
              }
            >
              {/* span wrapper: Tooltip needs a DOM child when the button is disabled */}
              <span>
                <IconButton
                  size="small"
                  onClick={() => { void handleRefresh(); }}
                  disabled={refreshing}
                  aria-label="Recompute correlations for chosen data"
                >
                  {refreshing
                    ? <CircularProgress size={18} />
                    : <RefreshIcon fontSize="small" />}
                </IconButton>
              </span>
            </Tooltip>
          </Box>
        )}
      </Box>
      {insufficient ? (
        <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
          <Typography variant="body2" color="text.secondary">
            Select 2+ industries (or tick index chips from 2+ industries) to see
            the windowed pairwise correlation of their mean-sentiment curves.
          </Typography>
        </Box>
      ) : (
        <>
          {refreshError && (
            <Alert severity="error" sx={{ py: 0.5 }}>{refreshError}</Alert>
          )}
          {loading && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
              <CircularProgress size={24} />
            </Box>
          )}
          {error && (
            <Alert severity="error" sx={{ py: 0.5 }}>Failed to load correlations: {error}</Alert>
          )}
          {!loading && !error && option && (
            <EChart option={option} height={360} />
          )}
          {!loading && !error && !option && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 3 }}>
              <Typography variant="body2" color="text.secondary">
                No correlation data available for the selected industries. Click the{" "}
                <RefreshIcon sx={{ fontSize: 13, verticalAlign: "-2px" }} /> button to
                recompute them, or run{" "}
                <code>python -m analyze.industry_sentiments.corr</code>.
              </Typography>
            </Box>
          )}
        </>
      )}
    </Box>
  );
}
