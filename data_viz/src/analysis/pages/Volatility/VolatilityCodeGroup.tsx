/**
 * VolatilityCodeGroup — one code's merged vol box (the four vol panels
 * stacked in ONE parent card the page renders per code), with the ONE
 * shared fetchVolatilitySeries call and the echarts.connect group wiring:
 *
 *   • Data — the group owns the single useChartData(fetchVolatilitySeries)
 *     call (the LRU cache would dedupe four per-panel fetches anyway;
 *     one fetch keeps the loading/error state and the row payload
 *     shared) and passes { rows, loading, error } down.
 *
 *   • Time slider — all four charts join one echarts.connect group, but
 *     only the FIRST chart (historical-vol levels) shows the dataZoom
 *     slider; the other three render an inside-only zoom (no slider —
 *     chartOption/*.ts) and react to the top slider through the group.
 *     All four share the same date axis, which is what makes the sync
 *     correct (the CrowdednessCodeGroup pattern).
 *
 *   • The latest-snapshot card is not a plot chart — it stays its own
 *     card beneath the merged box.
 */
import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import { Divider, Stack } from "@mui/material";
import ChartCard from "@/shared/components/ChartCard";
import { useChartData } from "@/shared/charts/base-chart";
import { fetchVolatilitySeries } from "@/lib/api-client";
import type { VolatilitySecType } from "@/lib/api-client";
import type { VolPanelProps } from "./types";
import { VOL_CHART_GROUP_PREFIX } from "./constants";
import VolLevelsPanel from "./VolLevelsPanel";
import EstimatorsPanel from "./EstimatorsPanel";
import RegimePanel from "./RegimePanel";
import DecompositionPanel from "./DecompositionPanel";
import VolSnapshotCard from "./VolSnapshotCard";

interface Props extends VolPanelProps {
  secType: VolatilitySecType;
}

export default function VolatilityCodeGroup({
  code, name, secType,
}: Props) {
  const { data, loading, error } = useChartData(
    () => fetchVolatilitySeries(code, secType),
    [code, secType],
  );
  const rows = data?.rows ?? [];
  const errorMessage = error ?? null;
  const label = name || code;
  const chartGroup = `${VOL_CHART_GROUP_PREFIX}-${secType}-${code}`;

  // Wire up cross-chart tooltip + dataZoom sync via echarts.connect() —
  // connect by group name persists, so charts mounting later (option
  // rebuilds, StrictMode remounts) keep syncing; guard against
  // re-connecting a group we already wired.
  const connectedGroupsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (connectedGroupsRef.current.has(chartGroup)) return;
    echarts.connect(chartGroup);
    connectedGroupsRef.current.add(chartGroup);
  }, [chartGroup]);

  const panelProps = { code, name, rows, loading, error: errorMessage };

  return (
    <>
      <ChartCard
        title={`${label} — Volatility`}
        subtitle={
          rows.length > 0
            ? `${rows.length} daily bars · percent annualized (√252) · one synced time slider (top chart) drives all four views`
            : undefined
        }
      >
        <Stack divider={<Divider />} spacing={1}>
          <VolLevelsPanel {...panelProps} group={chartGroup} />
          <EstimatorsPanel {...panelProps} group={chartGroup} />
          <RegimePanel {...panelProps} group={chartGroup} />
          <DecompositionPanel {...panelProps} group={chartGroup} />
        </Stack>
      </ChartCard>
      <VolSnapshotCard code={code} name={name} rows={rows} />
    </>
  );
}
