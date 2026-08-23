/**
 * QuarterlyCompositionBars — per-quarter 100% stacked bar chart of an ETF's
 * holdings composition by industry (the ETF Holdings analysis page's main
 * view).
 *
 * Data: /api/sec-composition/quarterly?code=<etf> — one entry per calendar
 * quarter that HAS a snapshot (latest snapshot within the quarter; quarters
 * without data are absent — no carry-forward). Tracking-index fallback is
 * applied server-side when the ETF itself has no snapshots.
 *
 * Rendering:
 *   • One bar per quarter; every bar is the SAME HEIGHT — each stacked
 *     segment is the industry's weight NORMALIZED to % of total composition,
 *     so the full stack always sums to exactly 100%.
 *   • One series (one color) per industry, colored via the shared
 *     CompositionPieChart color scheme (MUTED_PALETTE). Colors are assigned
 *     deterministically: industries ordered by weight in the LATEST quarter
 *     (desc; ties/missing resolved by max weight across all quarters), then
 *     MUTED_PALETTE cycles. The SAME industry→color map is handed to the
 *     CompositionPieChart / QuarterlyChangesTable below, so an industry
 *     keeps its color in the bars, the pie and the table.
 *   • Every bar carries a TICK indicator: clicking a bar toggles its tick —
 *     a ✓ badge is drawn above ticked bars and unticked bars dim while any
 *     bar is ticked. The panel below the chart switches on the tick count:
 *       - exactly 1 ticked bar → the shared CompositionPieChart renders for
 *         that quarter (seasonal mode: the quarter's snapshot date is passed
 *         as `date`, mapped back to the same quarter server-side);
 *       - 2 or more ticked bars → the pie is REPLACED by the shared
 *         QuarterlyChangesTable, which lists every industry's weight in each
 *         ticked season plus its changes (consecutive Δ + Total Δ).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Box, Chip, CircularProgress, Stack, Typography } from "@mui/material";
import { BarChart as BarChartIcon } from "@mui/icons-material";
import ChartCard from "@/components/ChartCard";
import RefreshButton from "@/components/RefreshButton";
import CompositionPieChart from "@/components/CompositionPieChart";
import QuarterlyChangesTable from "./QuarterlyChangesTable";
import EChart from "@/components/EChart";
import { useStore } from "@/store/filters";
import { fetchQuarterlyComposition, invalidateCacheForUrl } from "@/lib/api-client";
import { MUTED_PALETTE, axisColors } from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type {
  QuarterlyCompositionResponse,
  QuarterlyCompositionQuarter,
} from "@shared/types";
import type {
  BarSeriesOption,
  CustomSeriesOption,
  CustomSeriesRenderItem,
  EChartsOption,
  SeriesOption,
} from "echarts";

/** Tooltip caps the industry list to keep the card readable. */
const TOOLTIP_MAX_ROWS = 14;

/** Series name of the ✓-badge overlay (excluded from the legend, silent). */
const TICK_BADGE_SERIES = "__tick_badge__";

interface Props {
  /** Bare ETF code (e.g. "159673"). */
  code: string;
  /** Optional ETF display name (resolved by the parent from the nav tree). */
  name?: string;
}

export default function QuarterlyCompositionBars({ code, name }: Props) {
  const themeMode = useStore((s) => s.themeMode);
  const [data, setData] = useState<QuarterlyCompositionResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Indexes (into `quarters`) of the TICKED bars, ascending — a multi-select
  // that replaces the old single-bar selection. Exactly 1 tick → composition
  // pie for that season; 2+ ticks → QuarterlyChangesTable comparing seasons.
  const [tickedIdxs, setTickedIdxs] = useState<number[]>([]);
  const [refreshKey, setRefreshKey] = useState(0);

  const quarters = useMemo(
    () => data?.quarters ?? [],
    [data],
  );

  // Fetch the quarterly composition for this ETF.
  useEffect(() => {
    if (!code) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setTickedIdxs([]);
    fetchQuarterlyComposition(code)
      .then((d) => {
        if (cancelled) return;
        setData(d);
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
  }, [code, refreshKey]);

  const handleRefresh = () => {
    invalidateCacheForUrl(`/api/sec-composition/quarterly?code=${code}`);
    setRefreshKey((k) => k + 1);
  };

  // ---------------------------------------------------------------------------
  // Deterministic industry → color map (shared with the pie chart).
  //
  // Ordering: weight in the LATEST quarter desc; industries absent from the
  // latest quarter follow, ordered by their max weight across all quarters.
  // MUTED_PALETTE then cycles — mirroring how the standalone pie chart
  // assigns colors to its value-sorted slices.
  // ---------------------------------------------------------------------------
  const colorByIndustry = useMemo<Record<string, string>>(() => {
    if (quarters.length === 0) return {};
    const latest = quarters[quarters.length - 1];
    const latestW = new Map(latest.industries.map((i) => [i.industry, i.weight_pct]));
    const maxW = new Map<string, number>();
    for (const q of quarters) {
      for (const ind of q.industries) {
        maxW.set(ind.industry, Math.max(maxW.get(ind.industry) ?? 0, ind.weight_pct));
      }
    }
    const industries = Array.from(maxW.keys()).sort((a, b) => {
      const la = latestW.get(a) ?? -1;
      const lb = latestW.get(b) ?? -1;
      if (la !== lb) return lb - la;
      return (maxW.get(b) ?? 0) - (maxW.get(a) ?? 0);
    });
    const map: Record<string, string> = {};
    industries.forEach((ind, i) => {
      map[ind] = MUTED_PALETTE[i % MUTED_PALETTE.length];
    });
    return map;
  }, [quarters]);

  // ---------------------------------------------------------------------------
  // Bar chart option — 100% stacked, one series per industry + a custom
  // ✓-badge overlay marking the ticked bars.
  // ---------------------------------------------------------------------------
  const tickedSet = useMemo(() => new Set(tickedIdxs), [tickedIdxs]);
  const anyTicked = tickedIdxs.length > 0;

  const option = useMemo<EChartsOption>(() => {
    const c = axisColors(themeMode);
    const labels = quarters.map((q) => q.quarter);
    const series: SeriesOption[] = Object.entries(colorByIndustry).map(
      ([industry, color]) =>
        ({
          name: industry,
          type: "bar",
          stack: "composition",
          // Normalize each industry's raw weight to % of the quarter's TOTAL
          // composition so every bar stacks to exactly 100 (same height).
          // While at least one bar is ticked, unticked quarters dim so the
          // ticked bars stand out.
          data: quarters.map((q, idx) => {
            const ind = q.industries.find((i) => i.industry === industry);
            const value =
              !ind || q.total_weight_pct <= 0
                ? 0
                : Number(((ind.weight_pct / q.total_weight_pct) * 100).toFixed(2));
            if (anyTicked && !tickedSet.has(idx)) {
              return { value, itemStyle: { opacity: 0.25 } };
            }
            return value;
          }),
          itemStyle: { color },
          emphasis: {
            itemStyle: { shadowBlur: 6, shadowOffsetX: 0, shadowColor: "rgba(0,0,0,0.35)" },
          },
        }) as BarSeriesOption,
    );

    // ✓-badge overlay — one badge floating above every TICKED bar. The y-axis
    // max of 110 (bars still stack to 100) leaves the 100→110 band free for
    // it. silent:true so clicks pass through to the bar segments beneath;
    // the legend pins its data to the industry names so this series never
    // shows up as a toggleable legend entry.
    const tickBadgeRenderItem: CustomSeriesRenderItem = (params, api) => {
      if (!tickedSet.has(params.dataIndex)) return undefined;
      const point = api.coord([params.dataIndex, 106]);
      const x = point[0];
      const y = point[1];
      return {
        type: "group",
        children: [
          {
            type: "circle",
            shape: { cx: x, cy: y, r: 8 },
            style: {
              fill: c.textColor,
              stroke: c.tooltipBg,
              lineWidth: 1.5,
            },
          },
          {
            type: "text",
            style: {
              text: "✓",
              x,
              y,
              textAlign: "center",
              textVerticalAlign: "middle",
              fill: c.tooltipBg,
              font: "bold 11px sans-serif",
            },
          },
        ],
      };
    };
    series.push({
      type: "custom",
      name: TICK_BADGE_SERIES,
      renderItem: tickBadgeRenderItem,
      data: quarters.map((_, idx) => (tickedSet.has(idx) ? 1 : 0)),
      silent: true,
      z: 50,
      tooltip: { show: false },
    } satisfies CustomSeriesOption);

    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params: unknown) => {
          const list = params as Array<{ name?: string; marker?: string; seriesName?: string; value?: number }>;
          const idx = quarters.findIndex((q) => q.quarter === list[0]?.name);
          const q: QuarterlyCompositionQuarter | undefined = quarters[idx];
          if (!q) return "";
          const rows = q.industries
            .slice()
            .sort((a, b) => b.weight_pct - a.weight_pct)
            .slice(0, TOOLTIP_MAX_ROWS)
            .map((ind) => {
              const pct =
                q.total_weight_pct > 0
                  ? (ind.weight_pct / q.total_weight_pct) * 100
                  : 0;
              const marker = Object.entries(colorByIndustry).find(([n]) => n === ind.industry);
              const dot = marker
                ? `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${marker[1]};margin-right:4px"></span>`
                : "";
              return `${dot}${ind.industry} ${fmtNum(pct, 2)}%`;
            });
          const extra =
            q.industries.length > TOOLTIP_MAX_ROWS
              ? `<div style="opacity:0.7;margin-top:2px">… +${q.industries.length - TOOLTIP_MAX_ROWS} more</div>`
              : "";
          return (
            `<div style="font-weight:600;margin-bottom:4px">${q.quarter} · snapshot ${q.snapshot_date}</div>` +
            `<div style="opacity:0.8;margin-bottom:4px">${q.n_holdings} holdings · ${q.industries.length} industries</div>` +
            rows.join("<br/>") +
            extra +
            `<div style="opacity:0.6;margin-top:4px">Click the bar to tick/untick — 1 tick = pie · 2+ ticks = compare</div>`
          );
        },
        backgroundColor: c.tooltipBg,
        borderColor: c.splitLineColor,
        textStyle: { color: c.textColor, fontSize: 11 },
        confine: true,
      },
      legend: {
        type: "scroll",
        // Pin legend entries to the industry series — keeps the ✓-badge
        // custom overlay series out of the legend.
        data: Object.keys(colorByIndustry),
        top: 0,
        left: "center",
        textStyle: { color: c.textColor, fontSize: 9 },
        itemWidth: 10,
        itemHeight: 6,
        itemGap: 10,
        pageIconColor: c.textColor,
        pageTextStyle: { color: c.textColor },
      },
      grid: { left: 40, right: 12, top: 46, bottom: 30 },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { color: c.textColor, fontSize: 10, rotate: 45, interval: 0 },
        axisLine: { lineStyle: { color: c.axisLineColor } },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        // max 110 (bars still stack to exactly 100) leaves the 100→110 band
        // free for the ✓ tick badges; interval 25 keeps the ticks/labels at
        // 0/25/50/75/100 only.
        max: 110,
        min: 0,
        interval: 25,
        axisLabel: { color: c.textColor, fontSize: 10, formatter: "{value}%" },
        splitLine: { lineStyle: { color: c.splitLineColor } },
      },
      series,
    };
  }, [quarters, colorByIndustry, themeMode, tickedSet, anyTicked]);

  // Bar click → toggle that quarter's TICK (any segment of the stacked bar
  // works — every series shares the same quarter dataIndex). The ticks drive
  // the panel below the chart: exactly 1 tick → composition pie for that
  // season; 2+ ticks → QuarterlyChangesTable comparing the ticked seasons.
  const handleBarClick = useCallback(
    (params: unknown) => {
      const p = params as {
        componentType?: string;
        seriesName?: string;
        dataIndex?: number;
      };
      if (p.componentType !== "series" || typeof p.dataIndex !== "number") return;
      if (p.seriesName === TICK_BADGE_SERIES) return;
      if (p.dataIndex < 0 || p.dataIndex >= quarters.length) return;
      setTickedIdxs((cur) =>
        cur.includes(p.dataIndex)
          ? cur.filter((i) => i !== p.dataIndex)
          : [...cur, p.dataIndex].sort((a, b) => a - b),
      );
    },
    [quarters.length],
  );

  const selectedQuarter =
    tickedIdxs.length === 1 ? quarters[tickedIdxs[0]] : undefined;

  const subtitle = quarters.length
    ? `${quarters.length} quarters · ${quarters[0].quarter} → ${quarters[quarters.length - 1].quarter}` +
      (data?.source === "index" ? " · via tracking index" : "")
    : "No composition snapshots";

  return (
    <ChartCard
      title={`${code}${name ? ` · ${name}` : ""} — Quarterly Holdings by Industry`}
      subtitle={`${subtitle} · every bar = 100% of composition · tick bars: 1 = pie, 2+ = compare`}
      action={
        <RefreshButton
          onClick={handleRefresh}
          loading={loading}
          size="tiny"
          tooltip={`Refresh quarterly composition for ${code}`}
        />
      }
    >
      {loading && (
        <Stack direction="row" spacing={1} alignItems="center" sx={{ py: 4 }} justifyContent="center">
          <CircularProgress size={20} />
          <Typography variant="caption" color="text.secondary">
            Loading quarterly composition…
          </Typography>
        </Stack>
      )}
      {error && (
        <Alert severity="error" sx={{ py: 0.5 }}>
          Failed to load quarterly composition: {error}
        </Alert>
      )}
      {!loading && !error && quarters.length === 0 && (
        <Alert severity="warning" icon={false}>
          No composition snapshots for {code} — neither direct ETF holdings nor a
          usable tracking-index composition exists in stats.sec_composition.
        </Alert>
      )}
      {!loading && !error && quarters.length > 0 && (
        <>
          {data?.source === "index" && data.index_source && (
            <Alert severity="info" sx={{ py: 0.25, mb: 0.5 }} icon={false}>
              This ETF has no direct holdings snapshots — showing the
              composition of its tracking index{" "}
              <b>{data.index_source.code}</b> ({data.index_source.name || "—"}).
            </Alert>
          )}
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }} flexWrap="wrap" useFlexGap>
            <Chip
              icon={<BarChartIcon />}
              label="100% stacked · per quarter"
              size="small"
              variant="outlined"
              sx={{ fontSize: "0.65rem", height: 20 }}
            />
            <Chip
              label="colors = MUTED_PALETTE (same as composition pie)"
              size="small"
              variant="outlined"
              sx={{ fontSize: "0.65rem", height: 20 }}
            />
            {anyTicked && (
              <Chip
                label={`${tickedIdxs.length} ticked · ${
                  tickedIdxs.length === 1 ? "pie" : "changes table"
                }`}
                size="small"
                color="primary"
                variant="outlined"
                onDelete={() => setTickedIdxs([])}
                sx={{ fontSize: "0.65rem", height: 20 }}
              />
            )}
          </Stack>
          <EChart
            option={option}
            height={360}
            onEvents={{ click: handleBarClick }}
          />
          {selectedQuarter && (
            <Box sx={{ mt: 2 }}>
              <Typography
                variant="caption"
                sx={{ fontSize: "0.75rem", fontWeight: 600, display: "block", mb: 0.5 }}
                color="text.secondary"
              >
                {selectedQuarter.quarter} composition (snapshot {selectedQuarter.snapshot_date})
              </Typography>
              <CompositionPieChart
                code={code}
                date={selectedQuarter.snapshot_date}
                open
                onToggle={() => setTickedIdxs([])}
                colorByIndustry={colorByIndustry}
              />
            </Box>
          )}
          {tickedIdxs.length >= 2 && (
            <Box sx={{ mt: 2 }}>
              <Typography
                variant="caption"
                sx={{ fontSize: "0.75rem", fontWeight: 600, display: "block", mb: 0.5 }}
                color="text.secondary"
              >
                Industry changes ·{" "}
                {tickedIdxs
                  .map((i) => quarters[i]?.quarter)
                  .filter(Boolean)
                  .join(" → ")}{" "}
                — weights are % of each quarter&apos;s total composition
              </Typography>
              <QuarterlyChangesTable
                quarters={quarters}
                tickedIdxs={tickedIdxs}
                colorByIndustry={colorByIndustry}
              />
            </Box>
          )}
        </>
      )}
    </ChartCard>
  );
}
