/**
 * DbTrendChart — ONE table's write/read workload trend on the primary
 * (GET /api/monitor/db/trend): write-rate bars per active pass (colored
 * by the pass's write_flag — amber/red passes stand out) + a read-rate
 * line on a second axis. Fetched by the page when a workload row is
 * selected; renders placeholders while nothing is selected. FAMILY-AWARE:
 * a partitioned parent's trend is its family aggregate (response.family —
 * the caption says so); also mounted inside the expanded family row for
 * the in-context aggregate. SOURCE-AWARE like the page: live mode spans
 * a trailing window (hours=0 = the audit's full retained history);
 * ARCHIVE mode (source=archive) swaps to the daily rollup table's
 * per-GMT-day series over the full archive history — the x labels are
 * the GMT operational days, not pass times.
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
  type DbTrendResponse,
} from "@/lib/api-client/monitor";
import { buildDbTrendOption } from "./dbTrendOption";

const NO_POINTS = [];

/** Identity-stable empty response — useChartData needs a Promise even
 *  while no table is selected (the placeholders then show "select a
 *  table above"). */
const EMPTY: DbTrendResponse = {
  success: true,
  generatedAt: "",
  schema: "",
  table: "",
  hours: 0,
  source: "live",
  family: false,
  points: [],
};

interface Props extends BaseChartProps {
  schema: string | null;
  table: string | null;
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
  const enabled = schema != null && table != null;
  const { data, loading, error } = useChartData(
    () => (enabled ? fetchDbTableTrend(schema, table, hours, source) : Promise.resolve(EMPTY)),
    [schema, table, hours, source],
  );
  const points = data?.points ?? NO_POINTS;
  const family = data?.family ?? false;
  const option = useMemo(
    () =>
      enabled && points.length > 0
        ? buildDbTrendOption(points, themeMode, source)
        : null,
    [enabled, points, themeMode, source],
  );
  return (
    <BaseChart
      title={
        enabled
          ? `${schema}.${table} — ${
              source === "archive"
                ? "lifetime archive trend (per GMT day" +
                  (family ? ", Σ family)" : ")")
                : family
                  ? "family workload trend (Σ partitions)"
                  : "workload trend"
            }`
          : "Workload trend"
      }
      subtitle={
        enabled
          ? source === "archive"
            ? `${points.length} archived GMT days (lifetime rollup${family ? ", family-summed" : ""}) · write bars colored by flag (amber/red) · read line on right axis`
            : `${points.length} active passes ${hours === 0 ? "in the full retained history" : `over ${hours}h`} · write bars colored by flag (amber/red) · read line on right axis`
          : "select a table above"
      }
      loading={enabled && loading}
      error={
        error != null ? `Failed to load the table trend: ${error}` : null
      }
      option={option}
      emptyText={
        enabled
          ? source === "archive"
            ? "No archive rollups for this table (never active in any retained day)"
            : "No audited activity for this table in the window"
          : null
      }
      height={300}
      {...base}
    />
  );
}
