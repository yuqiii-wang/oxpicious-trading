/**
 * DbTrendChart — ONE partition family's aggregated write/read workload
 * trend (GET /api/monitor/db/trend): write-rate bars per active pass
 * (colored by the pass's write_flag — amber/red passes stand out) + a
 * read-rate line on a second axis. Mounted INSIDE the expanded family
 * row of the workload table — the page-level "workload trend" drill-down
 * it once fed is retired, so this Σ-partitions aggregate is the tab's
 * only trend surface (a partitioned parent's own audit rows are idle
 * zeros, and the endpoint sums the whole family per pass for it).
 * SOURCE-AWARE like the page: live mode spans a trailing window (hours=0
 * = the audit's full retained history); ARCHIVE mode (source=archive)
 * swaps to the daily rollup table's per-GMT-day series over the full
 * archive history — the x labels are the GMT operational days, not pass
 * times.
 */
import { useMemo } from "react";
import {
  BaseChart,
  useChartData,
  useChartThemeMode,
  type BaseChartProps,
} from "@/shared/charts/base-chart";
import {
  fetchDbTableTrend,
  type DbAuditSource,
} from "@/lib/api-client/monitor";
import { buildDbTrendOption } from "./dbTrendOption";

const NO_POINTS = [];

interface Props extends BaseChartProps {
  schema: string;
  table: string;
  hours: number;
  source: DbAuditSource;
}

export default function DbTrendChart({
  schema,
  table,
  hours,
  source,
  ...base
}: Props) {
  const themeMode = useChartThemeMode();
  const { data, loading, error } = useChartData(
    () => fetchDbTableTrend(schema, table, hours, source),
    [schema, table, hours, source],
  );
  const points = data?.points ?? NO_POINTS;
  const option = useMemo(
    () =>
      points.length > 0 ? buildDbTrendOption(points, themeMode, source) : null,
    [points, themeMode, source],
  );
  return (
    <BaseChart
      title={`${schema}.${table} — ${
        source === "archive"
          ? "lifetime archive trend (per GMT day, Σ family)"
          : "family workload trend (Σ partitions)"
      }`}
      subtitle={
        source === "archive"
          ? `${points.length} archived GMT days (lifetime rollup, family-summed) · write bars colored by flag (amber/red) · read line on right axis`
          : `${points.length} active passes ${hours === 0 ? "in the full retained history" : `over ${hours}h`} · write bars colored by flag (amber/red) · read line on right axis`
      }
      loading={loading}
      error={error != null ? `Failed to load the table trend: ${error}` : null}
      option={option}
      emptyText={
        source === "archive"
          ? "No archive rollups for this table (never active in any retained day)"
          : "No audited activity for this table in the window"
      }
      height={300}
      {...base}
    />
  );
}
