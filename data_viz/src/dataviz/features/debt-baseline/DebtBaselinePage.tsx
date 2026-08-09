/**
 * Debt Baseline page — 5-panel multi-chart view mirroring plot_debt_baseline.py.
 *
 * Layout (vertical stack, each its own ChartCard):
 *   0. PBoC OMA — narrow date-based news-marker strip (公开市场业务公告)
 *   1. Outright Repo / MLF — cumulative balance (line) + injection/withdrawal (bars, twin axis)
 *   2. OMO — 7-day reverse-repo rate (%) line + repo lifecycle volume (bars, twin axis)
 *   3. SHIBOR — multi-line (O/N, 1W, 1M, 3M, 6M, 1Y)
 *   4. ChinaBond — multi-line (1Y, 5Y, 10Y, 30Y)
 *   5. LPR — step-line (1Y, 5Y+) — PBoC monthly Loan Prime Rate announcement
 *
 * All five charts share a connected group "debt-baseline" so the crosshair
 * tooltip syncs across panels (same x-axis date).
 *
 * PBoC operation dates (outright repo / MLF) are shown in the tooltip on hover
 * instead of dense vertical markLines.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Accordion, AccordionDetails, AccordionSummary, Box, Chip, CircularProgress, Link, Stack, Typography } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import RefreshButton from "@/components/RefreshButton";
import { fetchDebtBaseline, fetchPbocOmaAnnouncements, invalidateCacheForUrl } from "@/lib/api-client";
import { useStore } from "@/store/filters";
import type {
  DebtBaselineResponse,
  DebtBaselineRow,
  PbocOmaRow,
  PbocOmaResponse,
} from "../../../../shared/types";
import {
  CUMULATIVE_COLOR,
  MUTED_INLINE_COLOR,
  MUTED_PALETTE,
  OMO_RATE_COLOR,
  REPO_END_COLOR,
  REPO_START_COLOR,
  SHIBOR_SERIES,
  CHINABOND_SERIES,
  LPR_SERIES,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { computeOutrightRepoLifecycle } from "@/lib/lifecycle";
import { fmtNum, fmtPct } from "@/lib/series";
import { buildBaseOption } from "./base-option";

const CHART_GROUP = "debt-baseline";
const MLF_COLOR = MUTED_PALETTE[1]; // orange — same as markLine color in Python

// ----------------------------------------------------------------------------
// PBoC OMA news-marker strip
//   Narrow 1-dim horizontal scatter: one marker per announcement date, coloured
//   by type. Hover shows title tooltip; click expands content below; clicking
//   another marker switches the expanded content.
// ----------------------------------------------------------------------------
const OMA_TYPE_META: Record<string, { label: string; color: string }> = {
  central_bank_bill:     { label: "Central bank bill",    color: MUTED_PALETTE[2] },
  overnight_reverse_repo:{ label: "Overnight rev-repo",   color: MUTED_PALETTE[1] },
  outright_repo:         { label: "Outright repo",        color: MUTED_PALETTE[3] },
  interest_rate:         { label: "Interest rate",        color: MUTED_PALETTE[6] },
  mlf:                   { label: "MLF",                  color: MUTED_PALETTE[5] },
  tool_introduction:     { label: "Tool introduction",    color: MUTED_PALETTE[4] },
  other:                 { label: "Other",                color: MUTED_PALETTE[7] },
};

function omaTypeLabel(t: string): string {
  return OMA_TYPE_META[t]?.label ?? t;
}
function omaTypeColor(t: string): string {
  return OMA_TYPE_META[t]?.color ?? MUTED_PALETTE[7];
}

interface OmaNewsPanelProps {
  /** Min/max dates from the debt-baseline data — used to align the OMA strip's
   *  x-axis range with the other panels. */
  minDate: string;
  maxDate: string;
}

function OmaNewsPanel({ minDate, maxDate }: OmaNewsPanelProps) {
  const themeMode = useStore((s) => s.themeMode);
  const [omaData, setOmaData] = useState<PbocOmaResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);
  // Plot-level refresh key — bumped by the refresh button to force a cache
  // bypass + refetch of the OMA announcements strip.
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPbocOmaAnnouncements()
      .then((d) => {
        if (cancelled) return;
        setOmaData(d);
        // Default-select the latest announcement so the content panel is
        // populated on first load.
        setSelectedIdx(d.rows.length > 0 ? d.rows.length - 1 : null);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
  }, [refreshKey]);

  const handleRefresh = useCallback(() => {
    // /api/debt-baseline/oma has no date in response → version check is
    // skipped, so just removing the cache entry forces a fresh fetch.
    invalidateCacheForUrl("/api/debt-baseline/oma");
    setRefreshKey((k) => k + 1);
  }, []);

  // Group rows by type for per-type scatter series (enables legend toggle).
  const seriesByType = useMemo(() => {
    if (!omaData) return new Map<string, Array<{ value: [string, number]; row: PbocOmaRow; idx: number }>>();
    const map = new Map<string, Array<{ value: [string, number]; row: PbocOmaRow; idx: number }>>();
    omaData.rows.forEach((row, idx) => {
      const arr = map.get(row.type) ?? [];
      arr.push({ value: [row.date, 0], row, idx });
      map.set(row.type, arr);
    });
    return map;
  }, [omaData]);

  const option = useMemo(() => {
    const c = axisColors(themeMode);
    const allRows = omaData?.rows ?? [];
    // x-axis min/max: prefer debt-baseline range (so the strip aligns with the
    // other panels), but fall back to OMA's own range if debt data isn't loaded.
    const xMin = minDate || (allRows[0]?.date ?? "");
    const xMax = maxDate || (allRows[allRows.length - 1]?.date ?? "");

    return {
      backgroundColor: "transparent",
      animation: false,
      grid: commonGrid({ left: 16, right: 16, top: 16, bottom: 28 }),
      tooltip: {
        trigger: "item" as const,
        backgroundColor: c.tooltipBg,
        borderColor: c.splitLineColor,
        textStyle: { color: c.textColor, fontSize: 11 },
        formatter: (params: unknown) => {
          const p = params as {
            data?: { value?: [string, number]; row?: PbocOmaRow };
            name?: string;
          };
          const row = p.data?.row;
          if (!row) return "";
          const kw = row.keywords ? `<div style="font-size:10px;opacity:0.8;margin-top:2px">${row.keywords}</div>` : "";
          return `<div style="font-weight:600;max-width:380px">${row.title}</div>` +
                 `<div style="font-size:10px;opacity:0.7;margin-top:2px">${row.date} · ${omaTypeLabel(row.type)}</div>` +
                 kw;
        },
      },
      xAxis: {
        type: "time" as const,
        min: xMin || undefined,
        max: xMax || undefined,
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisLabel: {
          color: c.textColor,
          fontSize: 10,
          formatter: (v: number) => {
            const d = new Date(v);
            const yyyy = d.getFullYear();
            const mm = String(d.getMonth() + 1).padStart(2, "0");
            return `${yyyy}-${mm}`;
          },
        },
        axisTick: { show: false },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value" as const,
        min: -1,
        max: 1,
        show: false,
      },
      legend: commonLegend(themeMode, { left: "right", data: Array.from(seriesByType.keys()).map((t) => omaTypeLabel(t)) }),
      series: Array.from(seriesByType.entries()).map(([type, points]) => ({
        name: omaTypeLabel(type),
        type: "scatter" as const,
        data: points.map((p) => ({
          value: p.value,
          row: p.row,
          idx: p.idx,
        })),
        symbolSize: (val: unknown, params: unknown) => {
          const p = params as { data?: { idx?: number } };
          return p.data?.idx === selectedIdx ? 16 : 10;
        },
        itemStyle: {
          color: omaTypeColor(type),
          opacity: 0.85,
          borderColor: "#fff",
          borderWidth: 1,
          shadowBlur: 2,
          shadowColor: "rgba(0,0,0,0.25)",
        },
        emphasis: {
          itemStyle: {
            borderColor: "#fff",
            borderWidth: 2,
            shadowBlur: 6,
          },
          scale: 1.3,
        },
        z: 3,
      })),
    };
  }, [omaData, themeMode, minDate, maxDate, seriesByType, selectedIdx]);

  // Click handler — stable identity via useCallback so EChart doesn't re-bind
  // on every render. Reads selectedIdx setter only.
  const handleClick = useCallback((params: unknown) => {
    const p = params as { data?: { idx?: number } };
    if (p.data?.idx != null) {
      setSelectedIdx(p.data.idx);
    }
  }, []);

  const selectedRow = selectedIdx != null && omaData ? omaData.rows[selectedIdx] ?? null : null;

  return (
    <ChartCard
      title="PBoC Open Market Announcements (公开市场业务公告)"
      subtitle="Policy notices timeline — hover for title, click marker to expand content below"
      height={undefined}
      action={
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          size="tiny"
          tooltip="Refresh PBoC OMA announcements"
        />
      }
    >
      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 1 }}>
          <CircularProgress size={24} />
        </Box>
      )}
      {error && (
        <Alert severity="error" sx={{ mb: 1 }}>
          Failed to load OMA announcements: {error}
        </Alert>
      )}
      {!loading && !error && omaData && omaData.rows.length > 0 && (
        <>
          <Box sx={{ height: 100, position: "relative" }}>
            <EChart
              option={option}
              height={100}
              minHeight={80}
              onEvents={{ click: handleClick }}
            />
          </Box>
          {/* Type legend chips */}
          <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", gap: 0.5, mt: 0.5, mb: 1 }}>
            {Array.from(seriesByType.keys()).map((t) => (
              <Chip
                key={t}
                size="small"
                label={omaTypeLabel(t)}
                sx={{
                  fontSize: "0.65rem",
                  height: 18,
                  bgcolor: omaTypeColor(t),
                  color: "#fff",
                  opacity: 0.9,
                }}
              />
            ))}
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem", alignSelf: "center" }}>
              {omaData.rows.length} announcements · {omaData.rows[0].date} → {omaData.rows[omaData.rows.length - 1].date}
            </Typography>
          </Stack>
          {/* Collapsible content panel for the selected announcement */}
          {selectedRow && (
            <Accordion
              sx={{
                mt: 0.5,
                borderRadius: 1,
                bgcolor: themeMode === "dark" ? "rgba(255,255,255,0.04)" : "rgba(0,0,0,0.03)",
                border: "1px solid",
                borderColor: themeMode === "dark" ? "rgba(255,255,255,0.08)" : "rgba(0,0,0,0.08)",
                "&:before": {
                  display: "none", // Remove default border
                },
              }}
            >
              <AccordionSummary
                expandIcon={<ExpandMoreIcon sx={{ fontSize: 16 }} />}
                sx={{
                  minHeight: 40,
                  padding: "8px 12px",
                  "&.Mui-expanded": {
                    minHeight: 40,
                  },
                }}
              >
                <Stack direction="row" spacing={1} alignItems="center" sx={{ flexWrap: "wrap", gap: 1, flex: 1 }}>
                  <Chip
                    size="small"
                    label={omaTypeLabel(selectedRow.type)}
                    sx={{
                      fontSize: "0.7rem",
                      height: 20,
                      bgcolor: omaTypeColor(selectedRow.type),
                      color: "#fff",
                    }}
                  />
                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
                    {selectedRow.date}
                  </Typography>
                  {selectedRow.keywords && (
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: "0.65rem", opacity: 0.7 }}>
                      keywords: {selectedRow.keywords}
                    </Typography>
                  )}
                  <Typography variant="subtitle2" sx={{ fontWeight: 600, fontSize: "0.85rem", flex: 1, ml: 1 }}>
                    {selectedRow.title}
                  </Typography>
                  {selectedRow.detail_url && (
                    <Link
                      href={selectedRow.detail_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      sx={{ fontSize: "0.7rem" }}
                    >
                      source ↗
                    </Link>
                  )}
                </Stack>
              </AccordionSummary>
              <AccordionDetails sx={{ padding: "0 12px 12px" }}>
                <Box
                  component="pre"
                  sx={{
                    m: 0,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    fontFamily: "inherit",
                    fontSize: "0.78rem",
                    lineHeight: 1.55,
                    color: themeMode === "dark" ? "rgba(255,255,255,0.82)" : "rgba(0,0,0,0.78)",
                    maxHeight: 320,
                    overflowY: "auto",
                  }}
                >
                  {selectedRow.content}
                </Box>
              </AccordionDetails>
            </Accordion>
          )}
        </>
      )}
      {!loading && !error && omaData && omaData.rows.length === 0 && (
        <Typography variant="body2" color="text.secondary" sx={{ py: 1 }}>
          No OMA announcements available.
        </Typography>
      )}
    </ChartCard>
  );
}

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

/**
 * LPR panel — PBoC Loan Prime Rate monthly announcement.
 *
 * LPR is published once a month (on the 20th, or next business day if holiday).
 * The rate stays constant between announcements, so we render it as a STEP line
 * (visualising the policy hold/cut pattern) plus a symbol marker on each
 * announcement date so the user can see exactly when the rate changed.
 */
function LprPanel({ data, markerMap }: { data: DebtBaselineResponse; markerMap: Map<string, string[]> }) {
  const themeMode = useStore((s) => s.themeMode);
  const option = useMemo(() => {
    const rows = data.rows;
    const dates = rows.map((r) => r.date);

    return buildBaseOption(dates, themeMode, {
      yAxis: [
        {
          type: "value",
          scale: true,
          name: "LPR (%)",
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
      series: LPR_SERIES.map((s) => ({
        type: "line" as const,
        name: s.label,
        yAxisIndex: 0,
        // Step line: rate holds flat between monthly announcements, then jumps
        // at the next announcement. 'end' means the step happens at the start
        // of the next day (visually matches "rate effective from announcement").
        step: "end" as const,
        connectNulls: true,
        data: rows.map((r) => (r as unknown as Record<string, number | null>)[s.col]),
        smooth: false,
        // Show a small circle marker ONLY on dates where the rate value is
        // non-null (i.e. the announcement date). ECharts 'showSymbol: false'
        // hides the default per-point symbol; we then use a separate
        // symbol-size function to render only announcement-day markers.
        showSymbol: false,
        lineStyle: { color: s.color, width: 1.6 },
        // Render symbols only on announcement days (where value != null)
        symbol: "circle",
        symbolSize: (val: number | Array<number | string>) => {
          const v = Array.isArray(val) ? val[val.length - 1] : val;
          return v == null || Number.isNaN(v as number) ? 0 : 6;
        },
        z: 3,
      })),
    }, markerMap);
  }, [data, themeMode, markerMap]);

  return (
    <ChartCard
      title="PBoC LPR — Loan Prime Rate (monthly announcement)"
      subtitle="1Y · 5Y+ (step line; markers on announcement dates)"
      height={300}
    >
      <EChart option={option} height={280} group={CHART_GROUP} />
    </ChartCard>
  );
}

export default function DebtBaselinePage() {
  const [fullData, setFullData] = useState<DebtBaselineResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Page-level refresh key — bumped by the page-header refresh button to
  // force a cache bypass + refetch of fetchDebtBaseline (drives the 5 main
  // panels: OutrightRepo / OMO / SHIBOR / ChinaBond / LPR). OmaNewsPanel
  // has its own plot-level refresh button.
  const [refreshKey, setRefreshKey] = useState(0);

  // Fetch all data (no date filter — slider handles windowing locally)
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchDebtBaseline(undefined, undefined)
      .then((d) => {
        if (cancelled) return;
        setFullData(d);
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
  }, [refreshKey]);

  const handleRefresh = useCallback(() => {
    // The 5 main panels all derive from one fetch: /api/debt-baseline (no
    // query string when called with no date filter). Removing that single
    // cache entry + bumping refreshKey forces a fresh DB read.
    // /api/debt-baseline/oma is a separate cache key and is left untouched —
    // OmaNewsPanel has its own plot-level refresh button.
    invalidateCacheForUrl("/api/debt-baseline");
    setRefreshKey((k) => k + 1);
  }, []);

  // Build marker map from ALL rows (so hover shows ops even if outside window)
  const markerMap = useMemo(() => {
    if (!fullData) return new Map<string, string[]>();
    return buildMarkerMap(fullData.rows);
  }, [fullData]);

  return (
    <Stack spacing={2}>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 1, flexWrap: "wrap" }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Debt-Market Baseline
          </Typography>
          <Typography variant="body2" color="text.secondary">
            PBoC Outright Repo · MLF · OMO · SHIBOR · China Bond · LPR — interactive mirror of plot_debt_baseline.py
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          label="Refresh"
          tooltip="Refresh all 5 debt-baseline panels (bypass cache)"
        />
      </Box>

      {/* PBoC OMA news-marker strip — fetches its own data, independent of the
          debt-baseline slider. x-axis range aligns with the full debt range. */}
      <OmaNewsPanel
        minDate={fullData?.minDate ?? ""}
        maxDate={fullData?.maxDate ?? ""}
      />

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
      {!loading && !error && fullData && (
        <>
          {fullData.rows.length === 0 ? (
            <Alert severity="warning">No data available.</Alert>
          ) : (
            <>
              <Typography variant="caption" color="text.secondary">
                {fullData.rows.length} trading days · {fullData.dates[0]} → {fullData.dates[fullData.dates.length - 1]}
              </Typography>
              <OutrightRepoPanel data={fullData} markerMap={markerMap} />
              <OmoPanel data={fullData} markerMap={markerMap} />
              <ShiborPanel data={fullData} markerMap={markerMap} />
              <ChinaBondPanel data={fullData} markerMap={markerMap} />
              <LprPanel data={fullData} markerMap={markerMap} />
            </>
          )}
        </>
      )}
    </Stack>
  );
}
