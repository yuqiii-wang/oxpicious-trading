/**
 * Debt Baseline page — 4-panel multi-chart view mirroring plot_debt_baseline.py.
 *
 * Layout (vertical stack, each its own ChartCard):
 *   1. Outright Repo / MLF — cumulative balance (line) + injection/withdrawal (bars, twin axis)
 *   2. OMO — 7-day reverse-repo rate (%) line + repo lifecycle volume (bars, twin axis)
 *   3. SHIBOR — multi-line (O/N, 1W, 1M, 3M, 6M, 1Y)
 *   4. ChinaBond — multi-line (1Y, 5Y, 10Y, 30Y)
 *
 * All four charts share a connected group "debt-baseline" so the crosshair
 * tooltip syncs across panels (same x-axis date).
 *
 * PBoC operation dates (outright repo / MLF) are shown in the tooltip on hover
 * instead of dense vertical markLines.
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, CircularProgress, Slider, Stack, Typography } from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { fetchDebtBaseline } from "@/lib/api-client";
import { useStore } from "@/store/filters";
import type { DebtBaselineResponse, DebtBaselineRow } from "../../../shared/types";
import {
  CUMULATIVE_COLOR,
  MUTED_INLINE_COLOR,
  MUTED_PALETTE,
  OMO_RATE_COLOR,
  REPO_END_COLOR,
  REPO_START_COLOR,
  SHIBOR_SERIES,
  CHINABOND_SERIES,
  axisColors,
} from "@/theme/chart-palette";
import { computeOutrightRepoLifecycle } from "@/lib/lifecycle";
import { fmtNum, fmtPct } from "@/lib/series";
import { buildBaseOption } from "./base-option";

const CHART_GROUP = "debt-baseline";
const MLF_COLOR = MUTED_PALETTE[1]; // orange — same as markLine color in Python

/**
 * Build a date→info-strings map for PBoC operations (outright repo + MLF).
 * Shown in tooltip on hover instead of dense vertical markLines.
 */
function buildMarkerMap(rows: DebtBaselineRow[]): Map<string, string[]> {
  const map = new Map<string, string[]>();
  for (const r of rows) {
    if (r.outright_repo_marker === 1) {
      const info = `Outright repo: ${r.outright_repo_quantity ?? "?"}亿 (${r.outright_repo_tenor_label || "?"})`;
      const arr = map.get(r.date) ?? [];
      arr.push(info);
      map.set(r.date, arr);
    }
    if (r.mlf_marker === 1) {
      const info = `MLF: ${r.mlf_quantity ?? "?"}亿 (${r.mlf_tenor_label || "?"})`;
      const arr = map.get(r.date) ?? [];
      arr.push(info);
      map.set(r.date, arr);
    }
  }
  return map;
}

function OutrightRepoPanel({ data, markerMap }: { data: DebtBaselineResponse; markerMap: Map<string, string[]> }) {
  const themeMode = useStore((s) => s.themeMode);
  const option = useMemo(() => {
    const rows = data.rows;
    const dates = rows.map((r) => r.date);
    const lifecycle = computeOutrightRepoLifecycle(rows);
    const cumArr = lifecycle.map((l) => l.outright_cumulative);
    // Split into 4 series: outright injection, MLF injection, outright withdrawal, MLF withdrawal
    const outrightStart = lifecycle.map((l) => l.outright_start);
    const mlfStart = lifecycle.map((l) => l.mlf_start);
    const outrightEnd = lifecycle.map((l) => l.outright_end);
    const mlfEnd = lifecycle.map((l) => l.mlf_end);

    return buildBaseOption(dates, themeMode, {
      yAxis: [
        {
          type: "value",
          scale: true,
          name: "Cumulative (亿)",
          nameLocation: "middle",
          nameGap: 50,
          nameTextStyle: { color: CUMULATIVE_COLOR, fontSize: 10 },
          axisLine: { lineStyle: { color: CUMULATIVE_COLOR } },
          axisLabel: {
            color: axisColors(themeMode).textColor,
            fontSize: 10,
            formatter: (v: number) => fmtNum(v) + "亿",
          },
          splitLine: {
            lineStyle: {
              color: axisColors(themeMode).splitLineColor,
              type: "dashed",
              opacity: 0.5,
            },
          },
        },
        {
          type: "value",
          scale: true,
          name: "Injection / Withdrawal (亿)",
          nameLocation: "middle",
          nameGap: 50,
          nameTextStyle: { color: MUTED_INLINE_COLOR, fontSize: 10 },
          axisLine: { lineStyle: { color: MUTED_INLINE_COLOR } },
          axisLabel: { color: MUTED_INLINE_COLOR, fontSize: 10, formatter: (v: number) => fmtNum(v) + "亿" },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          type: "line",
          name: "Cumulative balance",
          yAxisIndex: 0,
          data: cumArr,
          smooth: false,
          symbol: "none",
          lineStyle: { color: CUMULATIVE_COLOR, width: 2 },
          z: 3,
        },
        {
          type: "bar",
          name: "Outright injection",
          yAxisIndex: 1,
          stack: "injection",
          data: outrightStart,
          itemStyle: { color: REPO_START_COLOR, opacity: 0.7 },
          barWidth: "90%",
          z: 1,
        },
        {
          type: "bar",
          name: "MLF injection",
          yAxisIndex: 1,
          stack: "injection",
          data: mlfStart,
          itemStyle: { color: MLF_COLOR, opacity: 0.7 },
          barWidth: "90%",
          z: 1,
        },
        {
          type: "bar",
          name: "Outright withdrawal",
          yAxisIndex: 1,
          stack: "withdrawal",
          data: outrightEnd,
          itemStyle: { color: REPO_END_COLOR, opacity: 0.7 },
          barWidth: "90%",
          z: 1,
        },
        {
          type: "bar",
          name: "MLF withdrawal",
          yAxisIndex: 1,
          stack: "withdrawal",
          data: mlfEnd,
          itemStyle: { color: MLF_COLOR, opacity: 0.7 },
          barWidth: "90%",
          z: 1,
        },
      ],
    }, markerMap);
  }, [data, themeMode, markerMap]);

  return (
    <ChartCard
      title="PBoC Outright Repo / MLF — Capital Injection (Auction)"
      subtitle="Cumulative balance (line) · Outright injection/withdrawal (green/red bars) · MLF injection/withdrawal (orange bars)"
      height={340}
    >
      <EChart option={option} height={320} group={CHART_GROUP} />
    </ChartCard>
  );
}

function OmoPanel({ data, markerMap }: { data: DebtBaselineResponse; markerMap: Map<string, string[]> }) {
  const themeMode = useStore((s) => s.themeMode);
  const option = useMemo(() => {
    const rows = data.rows;
    const dates = rows.map((r) => r.date);
    const rate = rows.map((r) => r.omo_rate);
    const repoStart = rows.map((r) => r.repo_start_quantity);
    const repoEnd = rows.map((r) => Math.abs(r.repo_end_quantity));
    const repoCum = rows.map((r) => r.repo_cumulative);

    return buildBaseOption(dates, themeMode, {
      yAxis: [
        {
          type: "value",
          scale: true,
          name: "OMO rate (%)",
          nameLocation: "middle",
          nameGap: 50,
          nameTextStyle: { color: OMO_RATE_COLOR, fontSize: 10 },
          axisLine: { lineStyle: { color: OMO_RATE_COLOR } },
          axisLabel: {
            color: axisColors(themeMode).textColor,
            fontSize: 10,
            formatter: (v: number) => fmtPct(v),
          },
          splitLine: {
            lineStyle: {
              color: axisColors(themeMode).splitLineColor,
              type: "dashed",
              opacity: 0.5,
            },
          },
        },
        {
          type: "value",
          scale: true,
          name: "Repo volume / Cumulative (亿)",
          nameLocation: "middle",
          nameGap: 50,
          nameTextStyle: { color: MUTED_INLINE_COLOR, fontSize: 10 },
          axisLine: { lineStyle: { color: MUTED_INLINE_COLOR } },
          axisLabel: { color: MUTED_INLINE_COLOR, fontSize: 10, formatter: (v: number) => fmtNum(v) + "亿" },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          type: "line",
          name: "OMO 7D rev-repo rate (%)",
          yAxisIndex: 0,
          data: rate,
          smooth: false,
          symbol: "none",
          lineStyle: { color: OMO_RATE_COLOR, width: 1.4 },
          z: 3,
        },
        {
          type: "bar",
          name: "Repo start (injection)",
          yAxisIndex: 1,
          data: repoStart,
          itemStyle: { color: REPO_START_COLOR, opacity: 0.7 },
          barWidth: "90%",
          z: 1,
        },
        {
          type: "bar",
          name: "Repo end (withdrawal)",
          yAxisIndex: 1,
          data: repoEnd,
          itemStyle: { color: REPO_END_COLOR, opacity: 0.7 },
          barWidth: "90%",
          z: 1,
        },
        {
          type: "line",
          name: "Cumulative balance",
          yAxisIndex: 1,
          data: repoCum,
          smooth: false,
          symbol: "none",
          lineStyle: { color: CUMULATIVE_COLOR, width: 2 },
          z: 4,
        },
      ],
    }, markerMap);
  }, [data, themeMode, markerMap]);

  return (
    <ChartCard
      title="PBoC Open Market Operations — 7-day Reverse Repo"
      subtitle="OMO rate (line, left axis) · Repo lifecycle volume + cumulative (bars/line, right axis)"
      height={340}
    >
      <EChart option={option} height={320} group={CHART_GROUP} />
    </ChartCard>
  );
}

function ShiborPanel({ data, markerMap }: { data: DebtBaselineResponse; markerMap: Map<string, string[]> }) {
  const themeMode = useStore((s) => s.themeMode);
  const option = useMemo(() => {
    const rows = data.rows;
    const dates = rows.map((r) => r.date);

    return buildBaseOption(dates, themeMode, {
      yAxis: [
        {
          type: "value",
          scale: true,
          name: "SHIBOR (%)",
          nameLocation: "middle",
          nameGap: 50,
          nameTextStyle: { color: MUTED_INLINE_COLOR, fontSize: 10 },
          axisLine: { lineStyle: { color: MUTED_INLINE_COLOR } },
          axisLabel: {
            color: axisColors(themeMode).textColor,
            fontSize: 10,
            formatter: (v: number) => fmtPct(v),
          },
          splitLine: {
            lineStyle: {
              color: axisColors(themeMode).splitLineColor,
              type: "dashed",
              opacity: 0.5,
            },
          },
        },
        { type: "value", scale: true, show: false },
      ],
      series: SHIBOR_SERIES.map((s) => ({
        type: "line" as const,
        name: s.label,
        yAxisIndex: 0,
        data: rows.map((r) => (r as unknown as Record<string, number | null>)[s.col]),
        smooth: false,
        symbol: "none",
        lineStyle: { color: s.color, width: 1.1 },
        z: 3,
      })),
    }, markerMap);
  }, [data, themeMode, markerMap]);

  return (
    <ChartCard
      title="SHIBOR — Interbank Offered Rate Fixings"
      subtitle="O/N · 1W · 1M · 3M · 6M · 1Y"
      height={320}
    >
      <EChart option={option} height={300} group={CHART_GROUP} />
    </ChartCard>
  );
}

function ChinaBondPanel({ data, markerMap }: { data: DebtBaselineResponse; markerMap: Map<string, string[]> }) {
  const themeMode = useStore((s) => s.themeMode);
  const option = useMemo(() => {
    const rows = data.rows;
    const dates = rows.map((r) => r.date);

    return buildBaseOption(dates, themeMode, {
      yAxis: [
        {
          type: "value",
          scale: true,
          name: "Yield (%)",
          nameLocation: "middle",
          nameGap: 50,
          nameTextStyle: { color: MUTED_INLINE_COLOR, fontSize: 10 },
          axisLine: { lineStyle: { color: MUTED_INLINE_COLOR } },
          axisLabel: {
            color: axisColors(themeMode).textColor,
            fontSize: 10,
            formatter: (v: number) => fmtPct(v),
          },
          splitLine: {
            lineStyle: {
              color: axisColors(themeMode).splitLineColor,
              type: "dashed",
              opacity: 0.5,
            },
          },
        },
        { type: "value", scale: true, show: false },
      ],
      series: CHINABOND_SERIES.map((s) => ({
        type: "line" as const,
        name: s.label,
        yAxisIndex: 0,
        data: rows.map((r) => (r as unknown as Record<string, number | null>)[s.col]),
        smooth: false,
        symbol: "none",
        lineStyle: { color: s.color, width: 1.1 },
        z: 3,
      })),
    }, markerMap);
  }, [data, themeMode, markerMap]);

  return (
    <ChartCard
      title="China Treasury Bond Yield Curve (selected tenors)"
      subtitle="1Y · 5Y · 10Y · 30Y"
      height={320}
    >
      <EChart option={option} height={300} group={CHART_GROUP} />
    </ChartCard>
  );
}

export default function DebtBaselinePage() {
  const [fullData, setFullData] = useState<DebtBaselineResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<[number, number]>([0, 0]);

  // Fetch all data (no date filter — slider handles windowing locally)
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchDebtBaseline(undefined, undefined)
      .then((d) => {
        if (cancelled) return;
        setFullData(d);
        setRange([0, d.rows.length - 1]);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Filter data to slider window
  const data = useMemo<DebtBaselineResponse | null>(() => {
    if (!fullData || fullData.rows.length === 0) return fullData;
    const [s, e] = range;
    const rows = fullData.rows.slice(s, e + 1);
    const dates = rows.map((r) => r.date);
    return { dates, rows, minDate: fullData.minDate, maxDate: fullData.maxDate };
  }, [fullData, range]);

  // Build marker map from ALL rows (so hover shows ops even if outside window)
  const markerMap = useMemo(() => {
    if (!fullData) return new Map<string, string[]>();
    return buildMarkerMap(fullData.rows);
  }, [fullData]);

  const maxIdx = fullData ? fullData.rows.length - 1 : 0;
  const allDates = fullData?.rows.map((r) => r.date) ?? [];

  return (
    <Stack spacing={2}>
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>
          Debt-Market Baseline
        </Typography>
        <Typography variant="body2" color="text.secondary">
          PBoC Outright Repo · MLF · OMO · SHIBOR · China Bond — interactive mirror of plot_debt_baseline.py
        </Typography>
      </Box>

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled">
          Failed to load debt baseline: {error}
        </Alert>
      )}
      {!loading && !error && data && (
        <>
          {data.rows.length === 0 ? (
            <Alert severity="warning">No data available.</Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {data.rows.length} trading days · {data.dates[0]} → {data.dates[data.dates.length - 1]}
              </Typography>
              {maxIdx > 0 && (
                <Box sx={{ px: 1 }}>
                  <Slider
                    value={range}
                    onChange={(_, v) => setRange(v as [number, number])}
                    min={0}
                    max={maxIdx}
                    size="small"
                    valueLabelDisplay="auto"
                    valueLabelFormat={(idx) => allDates[idx] ?? ""}
                    sx={{ "& .MuiSlider-valueLabel": { fontSize: "0.7rem" } }}
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
              <OutrightRepoPanel data={data} markerMap={markerMap} />
              <OmoPanel data={data} markerMap={markerMap} />
              <ShiborPanel data={data} markerMap={markerMap} />
              <ChinaBondPanel data={data} markerMap={markerMap} />
            </>
          )}
        </>
      )}
    </Stack>
  );
}
