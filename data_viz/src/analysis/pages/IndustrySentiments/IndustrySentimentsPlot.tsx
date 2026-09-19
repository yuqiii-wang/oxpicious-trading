/**
 * Industry Sentiments plot card — multi-line chart + pool-size toggle +
 * benchmark dropdown + mean-only toggle + date-range slider.
 */
import { useMemo, useRef, useState } from "react";
import {
  Autocomplete,
  Box,
  Chip,
  Checkbox,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import ChartCard from "@/components/ChartCard";
import EChart from "@/components/EChart";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import { useAiAskAddon } from "@/shared/ai-ask";
import type { AiAskSpec } from "@/shared/ai-ask";
import type { ECharts, EChartsOption } from "echarts";
import { buildGroupColorScheme } from "@/theme/group-colors";
import type { PoolSize, PerIndustryAggregation, PlotProps } from "./types";
import { CHART_GROUP, BENCHMARK_COLORS } from "./constants";
import { classifyPoolSize } from "./helpers";
import { buildIndustryChartOption } from "./industryChartOption";
import { buildAggregateChartOption } from "./aggregateChartOption";
import { CorrelationChart } from "./CorrelationChart";

export function IndustrySentimentsPlot({
  data,
  multiIndustry,
  numIndustries,
  chartDataList,
  selectedIndustryIds,
  selectedItemCodes,
}: PlotProps) {
  const themeMode = useChartThemeMode();
  const [poolSize, setPoolSize] = useState<PoolSize>("all");
  const [selectedBenchmarks, setSelectedBenchmarks] = useState<string[]>([]);
  const [meanOnly, setMeanOnly] = useState(false);

  // Single-industry overlay: render the merged mean + ±1σ band from
  // data.aggregation. Only in single-industry mode.
  const showAggOverlay = !multiIndustry && data.aggregation.length > 0;

  // Per-industry aggregation sets for multi-industry mean overlay. Built from
  // chartDataList — each industry's aggregation array is passed through
  // verbatim (filtered by pool_size inside buildIndustryChartOption).
  // Industries with no aggregation rows (analysis not yet run) are dropped.
  const perIndustryAggregations: PerIndustryAggregation[] = useMemo(() => {
    if (!multiIndustry) return [];
    return chartDataList
      .filter((d) => d.aggregation.length > 0)
      .map((d) => ({
        industry_id: d.industry_id,
        industry_label: d.industry_label,
        aggregation: d.aggregation,
      }));
  }, [multiIndustry, chartDataList]);

  // ---- Group color scheme (one MAJOR color per industry) ----
  // Shared across the price chart + aggregate charts so an industry keeps the
  // same major color everywhere. In multi-industry mode the group set is the
  // list of selected industries; in single-industry mode it's just the one
  // industry (all member indices render as variants of a single major color).
  // Assignment is stable (sorted distinct keys → palette), so re-running with
  // the same industry set always yields the same colors.
  const industryColorFor = useMemo(() => {
    const ids = multiIndustry
      ? chartDataList.map((d) => d.industry_id)
      : [data.industry_id];
    const scheme = buildGroupColorScheme(ids);
    return (industryId: string) => scheme.majorColor(industryId);
  }, [multiIndustry, chartDataList, data.industry_id]);

  // Map each member-index code → its source industry_id (the curve's GROUP
  // key). Only needed in multi-industry mode, where the merged `data.indices`
  // is a flat array and would otherwise lose per-index industry attribution.
  // Built from the un-merged `chartDataList`. Undefined in single-industry
  // mode → the chart falls back to `data.industry_id` for every index.
  const indexGroupKey = useMemo(() => {
    if (!multiIndustry) return undefined;
    const m = new Map<string, string>();
    for (const d of chartDataList) {
      for (const idx of d.indices) m.set(idx.code, d.industry_id);
    }
    return m;
  }, [multiIndustry, chartDataList]);

  // Whether the "Mean only" toggle should be enabled. In single-industry
  // mode it needs data.aggregation; in multi-industry mode it needs at least
  // one industry with aggregation rows.
  const meanToggleEnabled = multiIndustry
    ? perIndustryAggregations.length > 0
    : showAggOverlay;

  // Check whether any aggregation rows carry non-null PE / trading-amount
  // data — controls whether the PE and Trading Amount sub-plots are shown.
  const hasPeData = useMemo(() => {
    if (multiIndustry) {
      return perIndustryAggregations.some((a) =>
        a.aggregation.some((r) => r.mean_pe != null),
      );
    }
    return data.aggregation.some((r) => r.mean_pe != null);
  }, [multiIndustry, perIndustryAggregations, data.aggregation]);

  const hasAmtData = useMemo(() => {
    if (multiIndustry) {
      return perIndustryAggregations.some((a) =>
        a.aggregation.some((r) => r.total_trading_amount != null),
      );
    }
    return data.aggregation.some((r) => r.total_trading_amount != null);
  }, [multiIndustry, perIndustryAggregations, data.aggregation]);

  // Build the unified date axis — sorted union of all member indices' dates
  // PLUS benchmark dates (so benchmark lines span the full chart width even
  // when member indices have fewer dates).
  const allDates = useMemo(() => {
    const set = new Set<string>();
    for (const idx of data.indices) for (const r of idx.rows) set.add(r.date);
    for (const bm of data.benchmarks) for (const r of bm.rows) set.add(r.date);
    return Array.from(set).sort();
  }, [data]);

  const lastIdx = Math.max(0, allDates.length - 1);
  // Count indices in each pool bucket (for toggle labels). The API only
  // returns compositioned indices, so every member has a stock_num — there is
  // no "null composition" bucket anymore.
  const poolCounts = useMemo(() => {
    const counts = { all: data.indices.length, small: 0, mid: 0, large: 0 };
    for (const idx of data.indices) {
      const ps = idx.rows.reduce<PoolSize | null>(
        (acc, r) => (r.stock_num != null ? classifyPoolSize(r.stock_num) : acc),
        null,
      );
      if (ps) counts[ps]++;
    }
    return counts;
  }, [data.indices]);

  const visibleCount = useMemo(() => {
    if (poolSize === "all") return data.indices.length;
    return data.indices.filter((idx) =>
      idx.rows.some((r) => classifyPoolSize(r.stock_num) === poolSize),
    ).length;
  }, [data.indices, poolSize]);

  // ---- Chart options (hoisted into memos so the AI Ask plot info derives
  // from the same objects the charts render) ----
  const mainOption = useMemo<EChartsOption | null>(
    () =>
      data.indices.length > 0 && allDates.length > 0
        ? buildIndustryChartOption(
            data,
            allDates,
            0,
            lastIdx,
            poolSize,
            themeMode,
            selectedBenchmarks,
            meanOnly,
            showAggOverlay,
            perIndustryAggregations,
            industryColorFor,
            indexGroupKey,
          )
        : null,
    [
      data,
      allDates,
      lastIdx,
      poolSize,
      themeMode,
      selectedBenchmarks,
      meanOnly,
      showAggOverlay,
      perIndustryAggregations,
      industryColorFor,
      indexGroupKey,
    ],
  );
  const peOption = useMemo<EChartsOption | null>(
    () =>
      hasPeData
        ? buildAggregateChartOption(
            data,
            perIndustryAggregations,
            allDates,
            0,
            lastIdx,
            poolSize,
            themeMode,
            multiIndustry,
            "mean_pe",
            "PE",
            industryColorFor,
          )
        : null,
    [
      hasPeData,
      data,
      perIndustryAggregations,
      allDates,
      lastIdx,
      poolSize,
      themeMode,
      multiIndustry,
      industryColorFor,
    ],
  );
  const amtOption = useMemo<EChartsOption | null>(
    () =>
      hasAmtData
        ? buildAggregateChartOption(
            data,
            perIndustryAggregations,
            allDates,
            0,
            lastIdx,
            poolSize,
            themeMode,
            multiIndustry,
            "total_trading_amount",
            "成交额 (亿元)",
            industryColorFor,
          )
        : null,
    [
      hasAmtData,
      data,
      perIndustryAggregations,
      allDates,
      lastIdx,
      poolSize,
      themeMode,
      multiIndustry,
      industryColorFor,
    ],
  );

  // Chart instances for the AI Ask screenshots (one per stacked chart).
  const mainRef = useRef<ECharts | null>(null);
  const peRef = useRef<ECharts | null>(null);
  const amtRef = useRef<ECharts | null>(null);

  const chartTitle = data.industry_label || data.industry_id;
  const chartSubtitle = multiIndustry
    ? `${data.indices.length} member indices across ${numIndustries} industries — ${visibleCount} highlighted (${poolSize} pool)`
    : `${data.industry_label || data.industry_id} — ${visibleCount} of ${data.indices.length} member indices highlighted (${poolSize} pool)`;

  // AI Ask — state carries the CURRENT pool/benchmark/mean toggles so the
  // modal and the LLM describe the view on screen.
  const aiAskSpec = useMemo<AiAskSpec>(
    () => ({
      intro:
        `Industry sentiment view for ${chartTitle}: member index closes rebased to 100 ` +
        "(start = 100) on a shared date axis, with the pool-size toggle highlighting " +
        "small/mid/large members and the selected broad-market benchmarks overlaid. " +
        "The Mean only toggle replaces member lines with the precomputed mean ±1σ band " +
        "(single industry) or each industry's mean (multi-industry). Sub-plots below: " +
        "the industry mean PE and total trading amount (亿元).",
      series: [
        { name: `mean (${poolSize})`, description: "precomputed cross-member mean close (rebased), dashed" },
        { name: "+1σ", description: "upper +1σ band edge of the member dispersion" },
        { name: "-1σ", description: "lower −1σ band edge of the member dispersion" },
      ],
      state: {
        mode: multiIndustry ? `multi-industry (${numIndustries})` : "single-industry",
        pool: poolSize,
        benchmarks: selectedBenchmarks.length > 0 ? selectedBenchmarks.join(", ") : "none",
        mean_only: meanOnly,
        visible_members: visibleCount,
      },
      notes: [
        "Member lines and benchmark lines are dynamic series names (index/benchmark names); one color per industry in multi-industry mode.",
        "The PE sub-plot is unitless mean PE per pool; the amount sub-plot is total trading amount in 亿元 (yuan / 1e8).",
        "Crosshair syncs across the main chart and both sub-plots via the shared chart group.",
      ],
    }),
    [chartTitle, multiIndustry, numIndustries, poolSize, selectedBenchmarks, meanOnly, visibleCount],
  );
  const extraOptions = useMemo(
    () => [peOption, amtOption] as const,
    [peOption, amtOption],
  );
  const aiAskAddon = useAiAskAddon({
    title: chartTitle,
    subtitle: chartSubtitle,
    option: mainOption,
    extraOptions,
    spec: aiAskSpec,
    getInstance: () => mainRef.current,
    getExtraInstances: () => [peRef.current, amtRef.current],
  });

  return (
    <ChartCard
      title={chartTitle}
      subtitle={chartSubtitle}
      titleAddon={aiAskAddon}
    >
      <Box sx={{ display: "flex", justifyContent: "space-between", gap: 2, mb: 1, flexWrap: "wrap" }}>
        <ToggleButtonGroup
          value={poolSize}
          exclusive
          size="small"
          onChange={(_, v: PoolSize | null) => v && setPoolSize(v)}
        >
          <ToggleButton value="all">All ({poolCounts.all})</ToggleButton>
          <ToggleButton value="small">Small &lt;51 ({poolCounts.small})</ToggleButton>
          <ToggleButton value="mid">Mid 51-180 ({poolCounts.mid})</ToggleButton>
          <ToggleButton value="large">Large &gt;180 ({poolCounts.large})</ToggleButton>
        </ToggleButtonGroup>
        {/* Benchmark dropdown (multi-select with checkboxes) + standalone
            ToggleButtons (NOT inside ToggleButtonGroup) to avoid the group
            intercepting clicks and getting stuck on double-click. Each
            button independently toggles its own boolean state. */}
        <Box sx={{ display: "flex", gap: 1, alignItems: "center", flexWrap: "wrap" }}>
          {data.benchmarks.length > 0 && (
            <Autocomplete
              multiple
              size="small"
              disableCloseOnSelect
              limitTags={3}
              options={data.benchmarks}
              getOptionLabel={(b) => b.name}
              isOptionEqualToValue={(a, b) => a.code === b.code}
              value={data.benchmarks.filter((b) =>
                selectedBenchmarks.includes(b.code),
              )}
              onChange={(_, newValue) =>
                setSelectedBenchmarks(newValue.map((b) => b.code))
              }
              renderOption={(props, option, { selected }) => (
                <li {...props} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <Checkbox size="small" checked={selected} sx={{ p: 0.5 }} />
                  <span style={{ color: BENCHMARK_COLORS[option.code] ?? "#ff6b35", fontWeight: 700 }}>━</span>
                  <span>{option.name}</span>
                </li>
              )}
              renderTags={(value, getTagProps) =>
                value.map((option, index) => {
                  const { key, ...tagProps } = getTagProps({ index });
                  return (
                    <Chip
                      key={key}
                      size="small"
                      label={option.name.replace(" (benchmark)", "")}
                      {...tagProps}
                      sx={{
                        height: 22,
                        borderColor: BENCHMARK_COLORS[option.code] ?? "#ff6b35",
                        "& .MuiChip-label": { fontSize: "0.7rem", px: 0.5 },
                      }}
                    />
                  );
                })
              }
              renderInput={(params) => (
                <TextField
                  {...params}
                  size="small"
                  label="Benchmarks"
                  placeholder={selectedBenchmarks.length === 0 ? "Tick to show" : ""}
                  sx={{ minWidth: 170, "& .MuiOutlinedInput-root": { py: 0.25 } }}
                />
              )}
              sx={{ minWidth: 170, maxWidth: 280 }}
            />
          )}
          <ToggleButton
            size="small"
            value="meanOnly"
            selected={meanOnly}
            onClick={() => setMeanOnly((v) => !v)}
            disabled={!meanToggleEnabled}
            sx={!meanToggleEnabled ? { opacity: 0.4 } : {}}
          >
            Mean only{multiIndustry && meanToggleEnabled ? ` (${perIndustryAggregations.length})` : ""}
          </ToggleButton>
        </Box>
      </Box>
      {/* Main plot — the shared base owns the empty-state placeholder; the
          card itself stays a hand-rolled composite (it hosts the PE / trading
          amount sub-plots + the correlation section below the chart, which a
          single-body BaseChart cannot express). */}
      <BaseChart
        variant="bare"
        option={mainOption}
        height={460}
        group={CHART_GROUP}
        emptyText={`No member indices with close data for ${data.industry_id}.`}
        onReady={(c) => {
          mainRef.current = c;
        }}
      />
      {hasPeData && (
        <Box sx={{ mt: 2, pt: 1, borderTop: 1, borderColor: "divider" }}>
          <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
            Industry PE {multiIndustry ? `(pool: ${poolSize})` : "(by pool size)"}
          </Typography>
          <EChart
            option={peOption ?? {}}
            height={200}
            group={CHART_GROUP}
            onReady={(c) => {
              peRef.current = c;
            }}
          />
        </Box>
      )}
      {hasAmtData && (
        <Box sx={{ mt: 2, pt: 1, borderTop: 1, borderColor: "divider" }}>
          <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
            Industry Total Trading Amount {multiIndustry ? `(pool: ${poolSize})` : "(by pool size)"}
          </Typography>
          <EChart
            option={amtOption ?? {}}
            height={200}
            group={CHART_GROUP}
            onReady={(c) => {
              amtRef.current = c;
            }}
          />
        </Box>
      )}
      {/* Pairwise correlation — always visible beneath the main plot.
          The chart itself renders a hint when fewer than 2 industries are
          selected, and auto-triggers the on-demand corr recompute when the
          selected pair has no materialized rows yet. */}
      <Box sx={{ mt: 2, pt: 1, borderTop: 1, borderColor: "divider" }}>
        <CorrelationChart
          industryIds={selectedIndustryIds}
          codes={selectedItemCodes}
          poolSize={poolSize}
        />
      </Box>
    </ChartCard>
  );
}
