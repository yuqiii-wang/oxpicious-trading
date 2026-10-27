/**
 * Debt page — 5-panel multi-chart view mirroring the builds/bond pipeline.
 *
 * Layout (vertical stack, each its own ChartCard):
 *   0. PBoC OMA — narrow date-based news-marker strip (公开市场业务公告)
 *   1. Outright Repo / MLF — cumulative balance (line) + injection/withdrawal (bars, twin axis)
 *   2. OMO — repo rate line (toggle: 7d = OMO policy rate / 1d = overnight
 *      reverse-repo policy rate 隔夜逆回购) + repo lifecycle volume (bars, twin axis)
 *   3. SHIBOR — multi-line (O/N, 1W, 1M, 3M, 6M, 1Y)
 *   4. ChinaBond — multi-line (1Y, 5Y, 10Y, 30Y)
 *   5. LPR — step-line (1Y, 5Y+) — PBoC monthly Loan Prime Rate announcement
 *
 * All five charts share a connected group "debt" so the crosshair
 * tooltip syncs across panels (same x-axis date).
 *
 * PBoC operation dates (outright repo / MLF) are shown in the tooltip on hover
 * instead of dense vertical markLines.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Accordion, AccordionDetails, AccordionSummary, Box, Chip, CircularProgress, Link, Stack, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ChartCard from "@/shared/components/ChartCard";
import RefreshButton from "@/components/RefreshButton";
import { fetchDebtDaily, fetchPbocOmaAnnouncements, invalidateCacheForUrl } from "@/lib/api-client";
import { BaseChart, useChartData, useChartThemeMode } from "@/shared/charts/base-chart";
import type { AiAskSpec } from "@/shared/ai-ask";
import { useDocs } from "@/shared/docs";
import type {
  DebtDailyResponse,
  DebtDailyRow,
  PbocOmaResponse,
} from "@shared/types";
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
} from "@/theme/chart-palette";
import { computeOutrightRepoLifecycle } from "@/lib/lifecycle";
import { fmtNum, fmtPct } from "@/lib/series";
import DateEventStrip, { type DateEvent } from "@/shared/components/date-events/DateEventStrip";
import { buildBaseOption } from "./base-option";

const CHART_GROUP = "debt";
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
  /** Min/max dates from the debt data — used to align the OMA strip's
   *  x-axis range with the other panels. */
  minDate: string;
  maxDate: string;
}

function OmaNewsPanel({ minDate, maxDate }: OmaNewsPanelProps) {
  const themeMode = useChartThemeMode();
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
    // /api/debt/oma has no date in response → version check is
    // skipped, so just removing the cache entry forces a fresh fetch.
    invalidateCacheForUrl("/api/debt/oma");
    setRefreshKey((k) => k + 1);
  }, []);

  // Strip events — one marker per announcement, colored/grouped by type.
  // id = row index, resolved back through onEventClick to setSelectedIdx.
  const omaEvents = useMemo<DateEvent[]>(() => {
    return (omaData?.rows ?? []).map((row, idx) => ({
      date: row.date,
      id: idx,
      type: row.type,
      title: row.title,
      detail: row.keywords ?? undefined,
    }));
  }, [omaData]);

  const handleMarkerClick = useCallback((e: DateEvent) => {
    setSelectedIdx(Number(e.id));
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
          <DateEventStrip
            events={omaEvents}
            typeMeta={OMA_TYPE_META}
            minDate={minDate}
            maxDate={maxDate}
            selectedId={selectedIdx}
            onEventClick={handleMarkerClick}
            height={100}
          />
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
function buildMarkerMap(rows: DebtDailyRow[]): Map<string, string[]> {
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
    if (r.omo_quantity_1d != null && r.omo_quantity_1d > 0) {
      const info = `Overnight rev-repo: ${r.omo_quantity_1d}亿 (1D)`;
      const arr = map.get(r.date) ?? [];
      arr.push(info);
      map.set(r.date, arr);
    }
  }
  return map;
}

function OutrightRepoPanel({ data, markerMap }: { data: DebtDailyResponse; markerMap: Map<string, string[]> }) {
  const themeMode = useChartThemeMode();
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

  const docs = useDocs();
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro: docs.text("ui.debt.oml"),
      series: docs.series("ui.debt.oml"),
    }),
    [docs],
  );

  return (
    <BaseChart
      title="PBoC Outright Repo / MLF — Capital Injection (Auction)"
      subtitle="Cumulative balance (line) · Outright injection/withdrawal (green/red bars) · MLF injection/withdrawal (orange bars)"
      height={320}
      option={option}
      group={CHART_GROUP}
      aiAsk={aiAskSpec}
    />
  );
}

/**
 * Repo-rate mode of the OMO panel — both are PBoC POLICY rates from the
 * same daily bulletins (公开市场业务交易公告):
 *   "7d" — the OMO 7-day reverse-repo auction rate (default)
 *   "1d" — the overnight reverse-repo variety (隔夜逆回购, tool since
 *          2026-06-29; fixed rate = 7d rate + 25bp, quantity-only bulletins)
 * The bars + cumulative balance (repo lifecycle) are mode-independent.
 */
type OmoRateMode = "1d" | "7d";

function OmoPanel({ data, markerMap }: { data: DebtDailyResponse; markerMap: Map<string, string[]> }) {
  const themeMode = useChartThemeMode();
  const [rateMode, setRateMode] = useState<OmoRateMode>("7d");
  const option = useMemo(() => {
    const rows = data.rows;
    const dates = rows.map((r) => r.date);
    const isOvernight = rateMode === "1d";
    const rate = rows.map((r) => (isOvernight ? r.omo_rate_1d : r.omo_rate));
    const repoStart = rows.map((r) => r.repo_start_quantity);
    const repoEnd = rows.map((r) => Math.abs(r.repo_end_quantity));
    const repoCum = rows.map((r) => r.repo_cumulative);

    // Freeze the rate axis across the 1d/7d toggle: one scale computed from
    // the UNION of both rate series, pinned as explicit min/max in either
    // mode (the sparse flat 1.65 overnight line would otherwise rescale the
    // axis on every switch).
    const rateVals = rows
      .flatMap((r) => [r.omo_rate, r.omo_rate_1d])
      .filter((v): v is number => v != null && !Number.isNaN(v));
    const rateMin = rateVals.length ? Math.min(...rateVals) : null;
    const rateMax = rateVals.length ? Math.max(...rateVals) : null;
    const ratePad = rateVals.length ? Math.max((rateMax - rateMin) * 0.05, 0.05) : 0;

    return buildBaseOption(dates, themeMode, {
      yAxis: [
        {
          type: "value",
          scale: true,
          ...(rateMin != null && rateMax != null
            ? { min: rateMin - ratePad, max: rateMax + ratePad }
            : {}),
          name: isOvernight ? "Overnight OMO rate (%)" : "OMO rate (%)",
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
        isOvernight
          ? {
              // Overnight ops are sparse (tool since 2026-06-29) and the rate
              // is fixed between ops — LPR-panel treatment: step line that
              // bridges non-op days, symbols only on op days.
              type: "line",
              name: "Overnight OMO rev-repo rate (%)",
              yAxisIndex: 0,
              data: rate,
              step: "end" as const,
              connectNulls: true,
              showSymbol: false,
              symbol: "circle",
              symbolSize: (val: number | Array<number | string>) => {
                const v = Array.isArray(val) ? val[val.length - 1] : val;
                return v == null || Number.isNaN(v as number) ? 0 : 6;
              },
              lineStyle: { color: OMO_RATE_COLOR, width: 1.4 },
              z: 3,
            }
          : {
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
  }, [data, themeMode, markerMap, rateMode]);

  const docs = useDocs();
  const aiAskSpec = useMemo<AiAskSpec>(() => {
    const docId = rateMode === "1d" ? "ui.debt.omo-1d" : "ui.debt.omo-7d";
    return {
      intro: docs.text(docId),
      series: docs.series(docId),
      notes: docs.notes(docId),
    };
  }, [docs, rateMode]);

  return (
    <BaseChart
      title={rateMode === "1d"
        ? "PBoC Open Market Operations — Overnight Reverse Repo"
        : "PBoC Open Market Operations — 7-day Reverse Repo"}
      subtitle={rateMode === "1d"
        ? "Overnight OMO policy rate (step line, left axis; markers on op days) · 7d repo lifecycle volume + cumulative (bars/line, right axis)"
        : "OMO rate (line, left axis) · Repo lifecycle volume + cumulative (bars/line, right axis)"}
      headerAction={
        <ToggleButtonGroup
          size="small"
          exclusive
          value={rateMode}
          onChange={(_, v: OmoRateMode | null) => v && setRateMode(v)}
          title="Switch the rate line: 1d = overnight reverse-repo policy rate (PBoC, since 2026-06-29), 7d = OMO 7-day reverse repo"
        >
          <ToggleButton value="1d" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>1d</ToggleButton>
          <ToggleButton value="7d" sx={{ px: 1, py: 0.25, fontSize: "0.7rem" }}>7d</ToggleButton>
        </ToggleButtonGroup>
      }
      height={320}
      option={option}
      group={CHART_GROUP}
      aiAsk={aiAskSpec}
    />
  );
}

function ShiborPanel({ data, markerMap }: { data: DebtDailyResponse; markerMap: Map<string, string[]> }) {
  const themeMode = useChartThemeMode();
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

  const docs = useDocs();
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro: docs.text("ui.debt.shibor"),
      instruments: [{ code: "SHIBOR" }],
      series: docs.series("ui.debt.shibor"),
      notes: docs.notes("ui.debt.shibor"),
    }),
    [docs],
  );

  return (
    <BaseChart
      title="SHIBOR — Interbank Offered Rate Fixings"
      subtitle="O/N · 1W · 1M · 3M · 6M · 1Y"
      height={300}
      option={option}
      group={CHART_GROUP}
      aiAsk={aiAskSpec}
    />
  );
}

function ChinaBondPanel({ data, markerMap }: { data: DebtDailyResponse; markerMap: Map<string, string[]> }) {
  const themeMode = useChartThemeMode();
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

  const docs = useDocs();
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro: docs.text("ui.debt.cgb"),
      instruments: [{ code: "CGB", name: "China Treasury Bond yields" }],
      series: docs.series("ui.debt.cgb"),
      notes: docs.notes("ui.debt.cgb"),
    }),
    [docs],
  );

  return (
    <BaseChart
      title="China Treasury Bond Yield Curve (selected tenors)"
      subtitle="1Y · 5Y · 10Y · 30Y"
      height={300}
      option={option}
      group={CHART_GROUP}
      aiAsk={aiAskSpec}
    />
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
function LprPanel({ data, markerMap }: { data: DebtDailyResponse; markerMap: Map<string, string[]> }) {
  const themeMode = useChartThemeMode();
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

  const docs = useDocs();
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro: docs.text("ui.debt.lpr"),
      instruments: [{ code: "LPR", name: "Loan Prime Rate" }],
      series: docs.series("ui.debt.lpr"),
      notes: docs.notes("ui.debt.lpr"),
    }),
    [docs],
  );

  return (
    <BaseChart
      title="PBoC LPR — Loan Prime Rate (monthly announcement)"
      subtitle="1Y · 5Y+ (step line; markers on announcement dates)"
      height={280}
      option={option}
      group={CHART_GROUP}
      aiAsk={aiAskSpec}
    />
  );
}

export default function DebtPage() {
  // Fetch all data (no date filter — slider handles windowing locally).
  // The shared kit hook owns the loading / error / stale-response lifecycle;
  // the fetcher has no deps, so it re-runs only on refresh().
  const { data: fullData, loading, error, refresh } = useChartData(
    () => fetchDebtDaily(undefined, undefined),
    [],
  );

  const handleRefresh = useCallback(() => {
    // The 5 main panels all derive from one fetch: /api/debt (no
    // query string when called with no date filter). Removing that single
    // cache entry + refresh() forces a fresh DB read.
    // /api/debt/oma is a separate cache key and is left untouched —
    // OmaNewsPanel has its own plot-level refresh button.
    invalidateCacheForUrl("/api/debt");
    refresh();
  }, [refresh]);

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
            Debt Market
          </Typography>
          <Typography variant="body2" color="text.secondary">
            PBoC Outright Repo · MLF · OMO · SHIBOR · China Bond · LPR — interactive mirror of the builds/bond pipeline
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          label="Refresh"
          tooltip="Refresh all 5 debt panels (bypass cache)"
        />
      </Box>

      {/* PBoC OMA news-marker strip — fetches its own data, independent of the
          debt slider. x-axis range aligns with the full debt range. */}
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
