/**
 * PairDetailTable — the Signal Rules Audit page's pair ranking: one row
 * per ORDERED family pair (A = base, B = other; overlap_pct is
 * asymmetric by design). Default order = lift desc — which pairs genuinely
 * co-move beyond chance — with the same/opposite-action conflict splits.
 */
import ExpandedTable, { type ExpandedTableColumn } from "@/shared/components/ExpandedTable";
import type { SignalOverlapAuditRow } from "@shared/types";
import { familyLabel, fmtLift, fmtPct } from "./format";

export default function PairDetailTable({
  rows,
  enableFilters,
  filterScopeDeps = [],
}: {
  rows: SignalOverlapAuditRow[];
  enableFilters: boolean;
  /** The page's period toggle — a change resets the per-column header
   *  filters so a narrowed row set never dead-ends against stale ticks. */
  filterScopeDeps?: unknown[];
}) {
  const columns: ExpandedTableColumn<SignalOverlapAuditRow>[] = [
    {
      key: "base",
      label: "Base family (A)",
      width: 150,
      render: (r) => familyLabel(r.signal_type_label, r.signal_type),
      filter: { type: "ticks", value: (r) => familyLabel(r.signal_type_label, r.signal_type) },
    },
    {
      key: "other",
      label: "Observed in (B)",
      width: 150,
      render: (r) => familyLabel(r.other_signal_type_label, r.other_signal_type),
      filter: { type: "ticks", value: (r) => familyLabel(r.other_signal_type_label, r.other_signal_type) },
    },
    {
      key: "overlap",
      label: "Overlap",
      align: "right",
      width: 100,
      info: "Of A's signal days, the fraction B also fired on (n_overlap / n_base) — asymmetric: the mirror pair (B→A) carries its own row.",
      render: (r) => fmtPct(r.overlap_pct),
    },
    {
      key: "shared",
      label: "Shared days",
      align: "right",
      width: 110,
      render: (r) => `${r.n_overlap.toLocaleString()} / ${r.n_base.toLocaleString()}`,
    },
    {
      key: "expected",
      label: "Expected",
      align: "right",
      width: 100,
      info: "Chance co-occurrence: B's base rate over the period's signal universe (n_other / n_universe) — what the overlap would read if A's days were random.",
      render: (r) => fmtPct(r.expected_pct),
    },
    {
      key: "lift",
      label: "Lift",
      align: "right",
      width: 90,
      info: "overlap / expected — above 1: A's days are genuinely enriched in B (structural co-firing); at or below 1: chance-level.",
      render: (r) => fmtLift(r.lift),
    },
    {
      key: "jaccard",
      label: "Jaccard",
      align: "right",
      width: 90,
      info: "Symmetric set similarity: shared days / union days.",
      render: (r) => fmtPct(r.jaccard),
    },
    {
      key: "same",
      label: "Same action",
      align: "right",
      width: 110,
      info: "Shared days where A and B fired the same side (buy↔buy / sell↔sell).",
      render: (r) => r.n_same_action.toLocaleString(),
    },
    {
      key: "opposite",
      label: "Opposite action",
      align: "right",
      width: 120,
      info: "Shared days where A and B fired opposite sides (buy↔sell) — one family's buy landing on another's sell: the conflict audit.",
      render: (r) => r.n_opposite_action.toLocaleString(),
    },
  ];

  return (
    <ExpandedTable<SignalOverlapAuditRow>
      columns={columns}
      rows={rows}
      rowKey={(r) => `${r.signal_type}-${r.other_signal_type}`}
      enableFilters={enableFilters}
      filterScopeDeps={filterScopeDeps}
      paginated
      rowsPerPageOptions={[10, 25, 50]}
      defaultRowsPerPage={25}
      maxHeight={420}
    />
  );
}
