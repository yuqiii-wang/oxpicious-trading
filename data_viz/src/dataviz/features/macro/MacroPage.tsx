/**
 * Macro page — precious metals (gold / silver) daily bars, mirroring the
 * builds.macro pipeline (downloads.macro.gold_silver → stats.macro_*).
 *
 * Layout (vertical stack, each its own BaseChart):
 *   0. Gold  黄金 — SGE Au99.99 (CNY/克, left axis) + COMEX GC (USD/ozt, right axis)
 *   1. Silver 白银 — SGE Ag99.99 (CNY/kg, left axis) + COMEX SI (USD/ozt, right axis)
 *
 * Both panels share a connected group "macro" so the crosshair tooltip
 * syncs across panels (same x-axis date). The two quote units ride twin
 * y-axes — they are never placed on one scale, and units come from the
 * data (macro_identity.unit) without client-side conversion.
 */
import { useCallback, useMemo, useRef } from "react";
import { Alert, Box, CircularProgress, Stack, Typography } from "@mui/material";
import RefreshButton from "@/components/RefreshButton";
import { fetchMacroDaily, invalidateCacheForUrl } from "@/lib/api-client";
import { BaseChart, useChartData, useChartThemeMode } from "@/shared/charts/base-chart";
import { useChartWidth } from "@/shared/charts/useChartWidth";
import { useDateTickGranularity } from "@/shared/charts/useDateTickGranularity";
import type { MacroDailyResponse } from "@shared/types";
import { MACRO_SGE_COLOR, MACRO_COMEX_COLOR } from "@/theme/chart-palette";
import { buildBaseOption } from "./base-option";

const CHART_GROUP = "macro";

interface MetalSeriesSpec {
  code: string;
  label: string;
}

interface MetalPanelProps {
  title: string;
  subtitle: string;
  /** Onshore SGE series (left axis, CNY unit). */
  sge: MetalSeriesSpec;
  /** Offshore COMEX series (right axis, USD unit). */
  comex: MetalSeriesSpec;
  data: MacroDailyResponse;
  /** Measured page width — drives the quarter→year tick loosening. */
  chartWidth?: number;
}

function MetalPanel({ title, subtitle, sge, comex, data, chartWidth }: MetalPanelProps) {
  const themeMode = useChartThemeMode();
  // Zoom-aware tick granularity: re-densifies year → quarter → month as the
  // time slider zooms in (see shared/charts/useDateTickGranularity).
  const { granularity, onEvents } = useDateTickGranularity(data.dates, chartWidth);
  const option = useMemo(() => {
    const closeByDate = (code: string): Map<string, number | null> => {
      const out = new Map<string, number | null>();
      for (const r of data.rows) {
        if (r.code === code) out.set(r.date, r.close);
      }
      return out;
    };
    const sgeClose = closeByDate(sge.code);
    const comexClose = closeByDate(comex.code);
    // The union date axis bridges the two calendars (SGE follows the
    // China calendar, COMEX the US one): each series is null on the
    // other market's holidays and connectNulls bridges its own gaps.
    return buildBaseOption(data.dates, themeMode, granularity, {
      series: [
        {
          type: "line" as const,
          name: sge.label,
          yAxisIndex: 0,
          data: data.dates.map((d) => sgeClose.get(d) ?? null),
          connectNulls: true,
          smooth: false,
          symbol: "none",
          lineStyle: { color: MACRO_SGE_COLOR, width: 1.1 },
          z: 3,
        },
        {
          type: "line" as const,
          name: comex.label,
          yAxisIndex: 1,
          data: data.dates.map((d) => comexClose.get(d) ?? null),
          connectNulls: true,
          smooth: false,
          symbol: "none",
          lineStyle: { color: MACRO_COMEX_COLOR, width: 1.1 },
          z: 3,
        },
      ],
    });
  }, [data, themeMode, sge, comex, granularity]);

  return (
    <BaseChart
      title={title}
      subtitle={subtitle}
      height={320}
      option={option}
      group={CHART_GROUP}
      onEvents={onEvents}
    />
  );
}

export default function MacroPage() {
  // Fetch all data (no date filter — slider handles windowing locally).
  // The shared kit hook owns the loading / error / stale-response lifecycle;
  // the fetcher has no deps, so it re-runs only on refresh().
  const { data: fullData, loading, error, refresh } = useChartData(
    () => fetchMacroDaily(),
    [],
  );
  // Page content width → the panels' width-adaptive date ticks (quarter
  // by default, loosened to year-start on a narrower chart).
  const rootRef = useRef<HTMLDivElement>(null);
  const chartWidth = useChartWidth(rootRef);

  const handleRefresh = useCallback(() => {
    // One fetch covers both panels: removing the /api/macro cache entry +
    // refresh() forces a fresh DB read.
    invalidateCacheForUrl("/api/macro");
    refresh();
  }, [refresh]);

  return (
    <Stack spacing={2} ref={rootRef}>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 1, flexWrap: "wrap" }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            Macro
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Gold 黄金 · Silver 白银 — SGE onshore spot (CNY) vs COMEX offshore main continuous (USD), interactive mirror of the builds/macro pipeline
          </Typography>
        </Box>
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          label="Refresh"
          tooltip="Refresh both macro panels (bypass cache)"
        />
      </Box>

      {loading && (
        <Box sx={{ display: "flex", justifyContent: "center", py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      )}
      {error && (
        <Alert severity="error" variant="filled">
          Failed to load macro data: {error}
        </Alert>
      )}
      {!loading && !error && fullData && (
        fullData.rows.length === 0 ? (
          <Alert severity="warning">No data available.</Alert>
        ) : (
          <>
            <Typography variant="caption" color="text.secondary">
              {fullData.rows.length} rows · {fullData.dates[0]} → {fullData.dates[fullData.dates.length - 1]}
            </Typography>
            <MetalPanel
              title="Gold 黄金 — SGE spot vs COMEX main continuous"
              subtitle="Au99.99 (CNY/克, left) · GC (USD/ozt, right)"
              sge={{ code: "Au99.99", label: "SGE Au99.99 (CNY/克)" }}
              comex={{ code: "GC", label: "COMEX GC (USD/ozt)" }}
              data={fullData}
              chartWidth={chartWidth}
            />
            <MetalPanel
              title="Silver 白银 — SGE spot vs COMEX main continuous"
              subtitle="Ag99.99 (CNY/kg, left) · SI (USD/ozt, right)"
              sge={{ code: "Ag99.99", label: "SGE Ag99.99 (CNY/kg)" }}
              comex={{ code: "SI", label: "COMEX SI (USD/ozt)" }}
              data={fullData}
              chartWidth={chartWidth}
            />
          </>
        )
      )}
    </Stack>
  );
}
