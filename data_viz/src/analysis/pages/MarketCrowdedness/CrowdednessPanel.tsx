/**
 * CrowdednessPanel — one card per code: the security's daily stats close
 * + trading amount, with the crowdedness_score curve from
 * analysis.market_crowdedness overlaid on its own axis (the four RAW
 * pillars — concentration / flow change / co-movement / valuation —
 * with their history percentiles, the turnover-concentration evidence
 * (top 1/5/10% shares, effective-N) and the stable hot-set names land
 * in the tooltip).
 *
 * The Hot set control pins the hot basket to one YEAR: Rolling (the
 * default — re-ranked every date) or a year from
 * analysis.market_crowdedness_hot_sets ("2025" …). A pinned year swaps
 * the metric to the analysis.market_crowdedness_year variant AND slices
 * the chart to that one calendar year (the API serves
 * hot_year-01-01..12-31) — that year's hottest names held FIXED across
 * the year (a member drops out only before its listing / after its
 * exit), so the curve reflects price / flow / co-movement / valuation
 * of a stable basket instead of daily membership churn. The selected
 * year is owned by the per-code CrowdednessCodeGroup and also drives
 * the HotTechPanel below (a display-window slice — the hot-tech table
 * has no year-pinned variant), and the two cards share an
 * echarts.connect group so the dataZoom time slider syncs across both.
 *
 * The nav/codes universe above is the full classification, deliberately
 * not data-restricted; codes outside the pipeline scope (index side =
 * the 10 study indices) plot close + amount only.
 */
import { useMemo } from "react";
import { BaseChart, useChartThemeMode, useChartData } from "@/shared/charts/base-chart";
import type { BaseChartProps } from "@/shared/charts/base-chart";
import {
  fetchMarketCrowdednessChart,
  fetchMarketCrowdednessHotYears,
} from "@/lib/api-client";
import type { MarketCrowdednessChartResponse } from "@shared/types";
import { buildCrowdednessCurveOption } from "@/shared/charts/crowdednessCurveOption";
import type { HotYearControlProps, PanelProps } from "./types";
import { Box, FormControl, MenuItem, Select, Typography } from "@mui/material";

interface Props extends BaseChartProps, PanelProps, HotYearControlProps {}

export default function CrowdednessPanel({
  code,
  name,
  secType,
  hotYear,
  onHotYearChange,
  ...base
}: Props) {
  const themeMode = useChartThemeMode();
  // 0 = the rolling hot set (re-ranked daily); a calendar year pins the
  // basket to that year's roster. The year state lives on the per-code
  // CrowdednessCodeGroup so the hot-tech card reacts to it too.
  const { data, loading, error } = useChartData<MarketCrowdednessChartResponse>(
    () => fetchMarketCrowdednessChart(code, secType, hotYear || null),
    [code, secType, hotYear],
  );
  const { data: yearsData } = useChartData(
    () => fetchMarketCrowdednessHotYears(code, secType),
    [code, secType],
  );
  const years = yearsData?.years ?? [];

  const option = useMemo(
    () =>
      data && data.rows.length > 0
        ? buildCrowdednessCurveOption(data, themeMode)
        : null,
    [data, themeMode],
  );

  const label = name || code;
  const hasMetric = (data?.rows ?? []).some((r) => r.crowdedness_score != null);

  return (
    <BaseChart
      title={`${label} — Market Crowdedness`}
      subtitle={
        data && data.rows.length > 0
          ? `${data.rows.length} daily bars · close + trading amount${hasMetric ? " · crowdedness" : " (no crowdedness rows for this code)"}${hotYear ? ` · hot set pinned to ${hotYear}` : ""}`
          : undefined
      }
      loading={loading}
      error={error ? `Failed to load market crowdedness chart: ${error}` : null}
      option={option}
      emptyText={`No close history for ${label} in stats — nothing to plot.`}
      height={360}
      {...base}
    >
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, px: 0.5, pb: 0.5 }}>
        <Typography variant="caption" sx={{ fontSize: "0.72rem", fontWeight: 600 }}>
          Hot set:
        </Typography>
        <FormControl size="small" sx={{ minWidth: 150 }}>
          <Select
            value={hotYear}
            onChange={(e) => onHotYearChange(Number(e.target.value))}
            sx={{ height: 28, fontSize: "0.75rem" }}
          >
            <MenuItem value={0} sx={{ fontSize: "0.75rem" }}>
              Rolling · daily re-pick
            </MenuItem>
            {years.map((y) => (
              <MenuItem key={y} value={y} sx={{ fontSize: "0.75rem" }}>
                {y}
              </MenuItem>
            ))}
          </Select>
        </FormControl>
      </Box>
    </BaseChart>
  );
}
