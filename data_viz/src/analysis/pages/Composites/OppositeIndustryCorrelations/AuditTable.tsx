/**
 * AuditTable — the shared ExpandedTable for the Opposite Industry
 * Correlations page's latest-window audit: layered group headers Overall /
 * Offset / Score over the 20d/60d/255d sub-columns, per-column header
 * filters, zebra rows. Score cells >= 0.7 are highlighted (strongly
 * opposite once the benchmark factor is removed).
 */
import { Box } from "@mui/material";
import ExpandedTable, { type ExpandedTableColumn } from "@/shared/components/ExpandedTable";
import { UP_COLOR } from "@/theme/chart-palette";
import { fmtNum } from "@/lib/series";
import type { IndustryCorrOffsetRow } from "@shared/types";
import { METRIC_COLS, METRIC_LABELS, WINDOWS, pairLabel, rowVal } from "./constants";
import type { CorrWindow, OffsetMetric } from "./constants";

const SCORE_HIGHLIGHT = 0.7;

/** One window sub-column per metric group (shared shape). */
function windowCol(
  metric: OffsetMetric,
  w: CorrWindow,
): ExpandedTableColumn<IndustryCorrOffsetRow> {
  return {
    key: `${metric}-${w}`,
    label: w,
    align: "right",
    width: 58,
    group: METRIC_LABELS[metric],
    render: (r) => {
      const v = rowVal(r, METRIC_COLS[metric][w]);
      if (v == null || !Number.isFinite(v)) return <>—</>;
      if (metric === "score" && v >= SCORE_HIGHLIGHT) {
        return (
          <Box component="span" sx={{ color: UP_COLOR, fontWeight: 600 }}>
            {fmtNum(v, 3)}
          </Box>
        );
      }
      return <>{fmtNum(v, 3)}</>;
    },
    filter: { type: "range", value: (r) => rowVal(r, METRIC_COLS[metric][w]) },
  };
}

export default function AuditTable({
  rows,
  enableFilters,
}: {
  rows: IndustryCorrOffsetRow[];
  enableFilters: boolean;
}) {
  const columns: ExpandedTableColumn<IndustryCorrOffsetRow>[] = [
    {
      key: "pair",
      label: "Pair",
      width: 220,
      render: (r) => pairLabel(r),
      filter: { type: "ticks", value: (r) => pairLabel(r) },
    },
    ...(["overall", "sub", "score"] as OffsetMetric[]).flatMap((m) =>
      WINDOWS.map((w) => windowCol(m, w)),
    ),
  ];
  return (
    <ExpandedTable
      columns={columns}
      rows={rows}
      rowKey={(r) => `${r.industry_id}|${r.benchmark_industry_id}`}
      maxHeight={320}
      enableFilters={enableFilters}
    />
  );
}
