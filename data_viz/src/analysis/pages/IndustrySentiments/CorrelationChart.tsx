/**
 * Correlation chart — expandable section below the main multi-line chart.
 *
 * Renders one line per industry pair, showing the rolling Pearson
 * correlation of the two industries' mean_price series over the
 * user-selected window (5d / 20d / 60d / 255d). Hover shows the
 * correlation value(s) at the hovered date.
 *
 * Auto-expanded by the parent plot when 2+ industries are selected — there
 * are no pairs to correlate below that threshold. This component is only
 * rendered inside the Collapse when open.
 */
import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Box,
  CircularProgress,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import EChart from "@/components/EChart";
import { fetchIndustryCorrelations } from "@/lib/api-client";
import type { IndustryCorrelationsResponse } from "@shared/types";
import type { EChartsOption } from "echarts";
import {
  MUTED_PALETTE,
  axisColors,
  commonLegend,
  commonGrid,
} from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { CorrelationChartProps, CorrWindow } from "./types";
import { CORR_WINDOWS } from "./constants";

export function CorrelationChart({
  industryIds,
  poolSize,
  themeMode,
}: CorrelationChartProps) {
  const [data, setData] = useState<IndustryCorrelationsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [window, setWindow] = useState<CorrWindow>("60d");

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
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
        setLoading(false);
      });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idsKey, poolSize]);

  // Build the chart option — one line per industry pair, plotting the
  // rolling correlation for the user-selected window over time. Tooltip
  // shows all 4 windows' values at the hovered date (richer than just the
  // selected window — lets the user compare short vs long-term co-movement
  // at a glance without toggling windows).
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
    // Sorted union of all pair dates — X axis.
    const allDatesSet = new Set<string>();
    for (const row of data.correlations) allDatesSet.add(row.date);
    const allDates = Array.from(allDatesSet).sort();

    // Selected window column → numeric value.
    const windowCol: Record<CorrWindow, "corr_5d" | "corr_20d" | "corr_60d" | "corr_255d"> = {
      "5d": "corr_5d",
      "20d": "corr_20d",
      "60d": "corr_60d",
      "255d": "corr_255d",
    };

    const series: Array<Record<string, unknown>> = sortedPairs.map((key, i) => {
      const rows = byPair.get(key)!;
      const byDate = new Map<string, typeof rows[number]>();
      for (const r of rows) byDate.set(r.date, r);
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
          // For each pair (in series order), look up all 4 window values
          // at this date. Display the selected window's value as the main
          // number; the other 3 as small muted chips for context.
          const rowsHtml = arr
            .map((p) => {
              const key = sortedPairs.find((k) => {
                const rows = byPair.get(k);
                if (!rows || rows.length === 0) return false;
                const r0 = rows[0];
                const shortA = (r0.industry_label || r0.industry_id).split("  ")[0] || r0.industry_id;
                const shortB = (r0.benchmark_industry_label || r0.benchmark_industry_id).split("  ")[0] || r0.benchmark_industry_id;
                return `${shortA} ↔ ${shortB}` === p.seriesName;
              });
              if (!key) return "";
              const rows = byPair.get(key)!;
              const r = rows.find((x) => x.date === dateStr);
              if (!r) return "";
              const pairIdx = sortedPairs.indexOf(key);
              const color = MUTED_PALETTE[pairIdx % MUTED_PALETTE.length];
              const fmtV = (v: number | null | undefined) => {
                if (v == null || !Number.isFinite(v)) return "—";
                return (v >= 0 ? "+" : "") + fmtNum(v, 3);
              };
              const chip = (w: CorrWindow, v: number | null) => {
                const isSel = w === window;
                const cls = isSel ? "font-weight:700" : "opacity:0.55;font-size:0.85em";
                return `<span style="${cls}">${w}:${v == null || !Number.isFinite(v) ? "—" : fmtV(v)}</span>`;
              };
              return `<div style="display:flex;justify-content:space-between;gap:8px;align-items:baseline">
                <span style="color:${color}">●</span>
                <span style="flex:1">${p.seriesName ?? ""}</span>
                <span style="display:flex;gap:6px;align-items:baseline">
                  ${chip("5d", r.corr_5d)} ${chip("20d", r.corr_20d)} ${chip("60d", r.corr_60d)} ${chip("255d", r.corr_255d)}
                </span>
              </div>`;
            })
            .join("");
          return `<div style="font-weight:600">${dateStr}</div>
                  <div style="margin-top:2px;opacity:0.7">Pairwise rolling Pearson correlation of mean_price series</div>
                  <div style="margin-top:4px">
                    <div style="display:flex;justify-content:space-between;gap:8px;opacity:0.55;font-size:0.85em">
                      <span>window:</span>
                      <span><b>${window}</b> highlighted · others shown for context</span>
                    </div>
                  </div>
                  <div style="margin-top:4px">${rowsHtml}</div>`;
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

  return (
    <Box>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center", mb: 1, flexWrap: "wrap", gap: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 600 }}>
          Pairwise Correlation of Industry Mean Sentiments
          {data ? ` — ${numPairs} pair${numPairs === 1 ? "" : "s"} · ${data.correlations.length.toLocaleString()} rows · pool=${poolSize}` : ""}
        </Typography>
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
      </Box>
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
            No correlation data available for the selected industries. Run{" "}
            <code>analyze_industry_correlations.py</code> to populate.
          </Typography>
        </Box>
      )}
    </Box>
  );
}
