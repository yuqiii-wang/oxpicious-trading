/**
 * TypeSummaryTable — the Signal Rules Audit page's family overview: one
 * row per family (signal_type_audit) — trigger-day counts, buy/sell
 * split, code/day spread, the share of the period's signal universe.
 */
import ExpandedTable, { type ExpandedTableColumn } from "@/shared/components/ExpandedTable";
import type { SignalTypeAuditRow } from "@shared/types";
import { familyLabel, fmtPct } from "./format";

export default function TypeSummaryTable({
  rows,
  enableFilters,
  filterScopeDeps = [],
}: {
  rows: SignalTypeAuditRow[];
  enableFilters: boolean;
  /** The page's period toggle — a change resets the per-column header
   *  filters so a narrowed row set never dead-ends against stale ticks. */
  filterScopeDeps?: unknown[];
}) {
  const columns: ExpandedTableColumn<SignalTypeAuditRow>[] = [
    {
      key: "family",
      label: "Family",
      width: 150,
      render: (r) => familyLabel(r.signal_type_label, r.signal_type),
      filter: { type: "ticks", value: (r) => familyLabel(r.signal_type_label, r.signal_type) },
    },
    {
      key: "n_events",
      label: "Signal days",
      align: "right",
      width: 110,
      render: (r) => r.n_events.toLocaleString(),
    },
    {
      key: "n_buy",
      label: "Buy days",
      align: "right",
      width: 100,
      render: (r) => r.n_buy.toLocaleString(),
    },
    {
      key: "n_sell",
      label: "Sell days",
      align: "right",
      width: 100,
      render: (r) => r.n_sell.toLocaleString(),
    },
    {
      key: "n_codes",
      label: "Codes",
      align: "right",
      width: 90,
      render: (r) => r.n_codes.toLocaleString(),
    },
    {
      key: "n_days",
      label: "Dates",
      align: "right",
      width: 90,
      render: (r) => r.n_days.toLocaleString(),
    },
    {
      key: "share",
      label: "Universe share",
      align: "right",
      width: 120,
      info: "The family's signal days over the period's DISTINCT (code, date) days with any family signal — the chance denominator of the overlap audit.",
      render: (r) => fmtPct(r.signal_universe > 0 ? r.n_events / r.signal_universe : null),
    },
    {
      key: "first_date",
      label: "First",
      width: 110,
      render: (r) => r.first_date,
    },
    {
      key: "last_date",
      label: "Last",
      width: 110,
      render: (r) => r.last_date,
    },
  ];

  return (
    <ExpandedTable<SignalTypeAuditRow>
      columns={columns}
      rows={rows}
      rowKey={(r) => `${r.signal_type}`}
      enableFilters={enableFilters}
      filterScopeDeps={filterScopeDeps}
      paginated
      rowsPerPageOptions={[10, 25, 50]}
      defaultRowsPerPage={10}
      maxHeight={420}
    />
  );
}
