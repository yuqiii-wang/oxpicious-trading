/**
 * IndustryDrilldown — per-industry expansion shown BENEATH a clicked row of
 * the ETF Holdings page's Industry changes table (QuarterlyChangesTable).
 *
 * Mounted lazily when a row is expanded; fetches two feeds in parallel:
 *   1. /api/analysis/industry-sentiments/chart?industry_id=<id>
 *      → member indices' raw daily closes + the precomputed mean/var
 *        aggregation rows from stats.industry_basic_stats.
 *   2. /api/sec-composition/industry-weight-series?code=<etf>&industry_id=<id>
 *      → this industry's weight in the ETF's composition across ALL snapshot
 *        dates (roughly monthly — denser than the quarterly table view).
 *
 * Renders two stacked plots:
 *   • TOP — dual y-axis overlay on a shared time x-axis:
 *       LEFT  axis: industry mean_close (composite rebased-to-100 close,
 *                   stats.industry_basic_stats mean_close, pool_size='all');
 *       RIGHT axis: this industry's % of the ETF's total composition
 *                   (weight_pct / total_weight_pct × 100 per snapshot — the
 *                   same normalization the quarterly table uses).
 *   • BOTTOM — the member INDEX curves that belong to this industry, each
 *     rebased to 100 at its own first non-null close (same convention as
 *     stats.industry_basic_stats and the Industry Sentiments page).
 *
 * The industry taxonomy is SHARED between stock and index classifications in
 * stats.sec_classification (same industry_id space), so the industry_id the
 * composition join resolved for the table row drives both feeds directly.
 */
import { useEffect, useMemo, useState } from "react";
import { Alert, Box, CircularProgress, Stack, Typography } from "@mui/material";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import {
  fetchIndustrySentimentsChart,
  fetchIndustryWeightSeries,
} from "@/lib/api-client";
import { PALETTE_HI, MA20_COLOR, MUTED_PALETTE, axisColors } from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import { rebaseTo100 } from "@/analysis/pages/IndustrySentiments/helpers";
import type {
  IndustrySentimentsChartResponse,
  IndustryWeightSeriesResponse,
} from "@shared/types";
import type { EChartsOption, LineSeriesOption } from "echarts";

interface Props {
  /** Bare ETF code the composition table is about (e.g. "159673"). */
  etfCode: string;
  /** Industry id from the composition's sec_classification join ('' = 未分类). */
  industryId: string;
  /** Display label (the table row's industry name). */
  industryLabel: string;
  /** The row's color dot from the shared colorByIndustry map. */
  color?: string;
}

/** Row drilldown for the Industry changes table. */
export default function IndustryDrilldown({
  etfCode,
  industryId,
  industryLabel,
  color,
}: Props) {
  const themeMode = useStore((s) => s.themeMode);
  const [chart, setChart] = useState<IndustrySentimentsChartResponse | null>(null);
  const [weights, setWeights] = useState<IndustryWeightSeriesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!industryId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      fetchIndustrySentimentsChart(industryId),
      fetchIndustryWeightSeries(etfCode, industryId),
    ])
      .then(([c, w]) => {
        if (cancelled) return;
        setChart(c);
        setWeights(w);
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
  }, [etfCode, industryId]);

  // mean_close daily series (pool_size='all') for the LEFT axis.
  const meanClose = useMemo(
    () =>
      (chart?.aggregation ?? [])
        .filter((r) => r.pool_size === "all" && r.mean_close != null)
        .map((r) => [r.date, r.mean_close as number] as [string, number]),
    [chart],
  );

  // Industry % of ETF total composition per snapshot for the RIGHT axis
  // (same normalization as the quarterly table).
  const etfPct = useMemo(
    () =>
      (weights?.points ?? [])
        .filter((p) => p.total_weight_pct > 0)
        .map(
          (p) =>
            [p.date, Number(((p.weight_pct / p.total_weight_pct) * 100).toFixed(2))] as [
              string,
              number,
            ],
        ),
    [weights],
  );

  // Member index curves rebased to 100 at each index's first non-null close.
  const memberSeries = useMemo(() => {
    const indices = chart?.indices ?? [];
    return indices.map((idx, i) => {
      const closes = idx.rows.map((r) => r.close);
      const rebased = rebaseTo100(closes, 0, closes.length - 1);
      const data: Array<[string, number]> = [];
      idx.rows.forEach((r, j) => {
        const v = rebased[j];
        if (v != null) data.push([r.date, Number(v.toFixed(2))]);
      });
      return {
        name: idx.name || idx.code,
        data,
        color: MUTED_PALETTE[i % MUTED_PALETTE.length],
      };
    });
  }, [chart]);

  const dualOption = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const lineColor = color ?? PALETTE_HI;
    // Last data point with date <= t (ISO strings compare lexicographically).
    // Returns null when t precedes the series' first point.
    const lastAvail = (
      data: Array<[string, number]>,
      t: string,
    ): [string, number] | null => {
      let out: [string, number] | null = null;
      for (const d of data) {
        if (d[0] <= t) out = d;
        else break;
      }
      return out;
    };
    const dot = (col: string) =>
      `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${col};margin-right:4px"></span>`;
    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line" },
        // Custom formatter: the ETF pct series is SPARSE (snapshot dates
        // only) — hovering BETWEEN points must still show it. Both series
        // carry their LAST AVAILABLE value (date <= hovered date); a carried
        // value is tagged with its as-of date. NOTE: on a time axis
        // axisValue arrives as a ms NUMBER — coerced to a local YYYY-MM-DD.
        formatter: (params: unknown) => {
          const list = params as Array<{ axisValue?: string | number }>;
          const raw = list[0]?.axisValue;
          const hovered =
            typeof raw === "number"
              ? (() => {
                  const d = new Date(raw);
                  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
                })()
              : (raw ?? "").slice(0, 10);
          if (!hovered) return "";
          const mc = lastAvail(meanClose, hovered);
          const pct = lastAvail(etfPct, hovered);
          const asOf = (d: string) =>
            d < hovered ? `<span style="opacity:0.55"> (${d})</span>` : "";
          const mcRow = mc
            ? `${dot(lineColor)}${industryLabel} mean_close ${fmtNum(mc[1], 2)}${asOf(mc[0])}`
            : "";
          const pctRow = pct
            ? `${dot(MA20_COLOR)}in ${etfCode} ${fmtNum(pct[1], 2)}%${asOf(pct[0])}`
            : "";
          return (
            `<div style="font-weight:600;margin-bottom:4px">${hovered}</div>` +
            [mcRow, pctRow].filter(Boolean).join("<br/>")
          );
        },
        backgroundColor: c.tooltipBg,
        borderColor: c.splitLineColor,
        textStyle: { color: c.textColor, fontSize: 11 },
        confine: true,
      },
      legend: {
        top: 0,
        left: "center",
        textStyle: { color: c.textColor, fontSize: 9 },
        itemWidth: 14,
        itemHeight: 8,
        itemGap: 12,
      },
      grid: { left: 52, right: 52, top: 28, bottom: 28 },
      xAxis: {
        type: "time",
        axisLabel: { color: c.textColor, fontSize: 10, hideOverlap: true },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        splitLine: { show: false },
      },
      yAxis: [
        {
          type: "value",
          name: "mean close",
          nameTextStyle: { color: lineColor, fontSize: 9 },
          scale: true,
          axisLabel: { color: c.textColor, fontSize: 10 },
          splitLine: { lineStyle: { color: c.splitLineColor } },
        },
        {
          type: "value",
          name: "ETF pct",
          nameTextStyle: { color: MA20_COLOR, fontSize: 9 },
          axisLabel: { color: c.textColor, fontSize: 10, formatter: "{value}%" },
          splitLine: { show: false },
        },
      ],
      dataZoom: [{ type: "inside" }],
      series: [
        {
          name: `${industryLabel} mean_close (all pools)`,
          type: "line",
          yAxisIndex: 0,
          data: meanClose,
          showSymbol: false,
          lineStyle: { width: 1.6, color: lineColor },
          itemStyle: { color: lineColor },
          emphasis: { focus: "series" },
        },
        {
          name: `${industryLabel} in ${etfCode} (pct of total)`,
          type: "line",
          yAxisIndex: 1,
          data: etfPct,
          symbol: "circle",
          symbolSize: 5,
          // step 'end': the line HOLDS the previous value right up to the
          // next available snapshot, where it turns — the turning angle sits
          // ON the next point (carry-forward semantics made visual).
          step: "end",
          lineStyle: { width: 1.6, color: MA20_COLOR },
          itemStyle: { color: MA20_COLOR },
          emphasis: { focus: "series" },
        },
      ],
    };
  }, [themeMode, color, industryLabel, etfCode, meanClose, etfPct]);

  const curvesOption = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const series = memberSeries.map(
      (s) =>
        ({
          name: s.name,
          type: "line",
          data: s.data,
          showSymbol: false,
          lineStyle: { width: 1.2, color: s.color },
          itemStyle: { color: s.color },
          emphasis: { focus: "series" },
        }) as LineSeriesOption,
    );
    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line" },
        backgroundColor: c.tooltipBg,
        borderColor: c.splitLineColor,
        textStyle: { color: c.textColor, fontSize: 11 },
        confine: true,
        valueFormatter: (v: unknown) => fmtNum(v as number, 1),
      },
      legend: {
        type: "scroll",
        top: 0,
        left: "center",
        textStyle: { color: c.textColor, fontSize: 9 },
        itemWidth: 12,
        itemHeight: 6,
        itemGap: 8,
        pageIconColor: c.textColor,
        pageTextStyle: { color: c.textColor },
      },
      grid: { left: 44, right: 12, top: 30, bottom: 28 },
      xAxis: {
        type: "time",
        axisLabel: { color: c.textColor, fontSize: 10, hideOverlap: true },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { color: c.textColor, fontSize: 10 },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      dataZoom: [{ type: "inside" }],
      series,
    };
  }, [themeMode, memberSeries]);

  if (!industryId) {
    return (
      <Alert severity="info" icon={false} sx={{ py: 0.5 }}>
        Industry <b>{industryLabel}</b> has no industry classification — no
        drill-down stats available.
      </Alert>
    );
  }

  const noMeanClose = meanClose.length === 0;
  const noPct = etfPct.length === 0;
  const noCurves = memberSeries.length === 0;

  return (
    <Box sx={{ py: 1 }}>
      {loading && (
        <Stack direction="row" spacing={1} alignItems="center" sx={{ py: 2 }} justifyContent="center">
          <CircularProgress size={18} />
          <Typography variant="caption" color="text.secondary">
            Loading {industryLabel} drill-down…
          </Typography>
        </Stack>
      )}
      {error && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          Failed to load drill-down: {error}
        </Alert>
      )}
      {!loading && !error && noMeanClose && noPct && noCurves && (
        <Alert severity="warning" icon={false} sx={{ py: 0.5 }}>
          No stats data for industry <b>{industryLabel}</b> ({industryId}).
        </Alert>
      )}
      {!loading && !error && (!noMeanClose || !noPct) && (
        <Box sx={{ mb: 1.5 }}>
          <Typography
            variant="caption"
            sx={{ fontSize: "0.7rem", fontWeight: 600, display: "block", mb: 0.25 }}
            color="text.secondary"
          >
            {industryLabel} · industry mean close (left) vs % in {etfCode} (right)
            {weights?.source === "index" && weights.index_source
              ? ` · via tracking index ${weights.index_source.code}`
              : ""}
          </Typography>
          <EChart option={dualOption} height={240} minHeight={160} />
        </Box>
      )}
      {!loading && !error && !noCurves && (
        <Box>
          <Typography
            variant="caption"
            sx={{ fontSize: "0.7rem", fontWeight: 600, display: "block", mb: 0.25 }}
            color="text.secondary"
          >
            {industryLabel} · member index curves (rebased to 100 at each index&apos;s
            first close) · {memberSeries.length} indices
          </Typography>
          <EChart option={curvesOption} height={280} minHeight={180} />
        </Box>
      )}
    </Box>
  );
}
