/**
 * CoverageAuditTable — the shared ExpandedTable for the News Audit page's
 * COVERAGE view: one row per TRADING DAY per industry
 * (text.news_coverage_audit — the daily grain analysis.news_text_daily
 * reads as its flow basis; '000001' = the whole-market rollup row,
 * "" = untagged). The 2026-11-22 coverage migration's third view.
 */
import ExpandedTable, { type ExpandedTableColumn } from "@/shared/components/ExpandedTable";
import type { NewsCoverageAuditRow } from "@shared/types";

export default function CoverageAuditTable({
  rows,
  enableFilters,
  filterScopeDeps = [],
}: {
  rows: NewsCoverageAuditRow[];
  enableFilters: boolean;
  /** The page's toolbar filters (slider range) — a change resets the
   *  per-column header filters so a narrowed row set never dead-ends
   *  against a stale tick selection. */
  filterScopeDeps?: unknown[];
}) {
  const columns: ExpandedTableColumn<NewsCoverageAuditRow>[] = [
    {
      key: "date",
      label: "Trading day",
      width: 130,
      render: (r) => r.date,
      filter: { type: "date", value: (r) => r.date },
    },
    {
      key: "industry",
      label: "Industry",
      width: 200,
      render: (r) =>
        r.industry_id === "000001" ? (
          <span style={{ fontWeight: 600 }}>{r.industry_label}</span>
        ) : r.industry_id.length > 0 ? (
          <>{r.industry_label}</>
        ) : (
          <span style={{ opacity: 0.55 }}>· untagged ·</span>
        ),
      filter: {
        type: "ticks",
        value: (r) => r.industry_label,
      },
    },
    {
      key: "n_articles",
      label: "Articles",
      align: "right",
      width: 95,
      render: (r) => <>{r.n_articles.toLocaleString()}</>,
      filter: { type: "range", value: (r) => r.n_articles },
      info: "Every text.news row in scope (digested or not) — the raw coverage basis the daily panel's abnormal-flow flag reads.",
    },
    {
      key: "n_digested",
      label: "Digested",
      align: "right",
      width: 95,
      render: (r) => <>{r.n_digested.toLocaleString()}</>,
      filter: { type: "range", value: (r) => r.n_digested },
    },
    {
      key: "n_fake",
      label: "Fake",
      align: "right",
      width: 80,
      render: (r) => <>{r.n_fake.toLocaleString()}</>,
      filter: { type: "range", value: (r) => r.n_fake },
    },
    {
      key: "n_industries",
      label: "Breadth",
      align: "right",
      width: 90,
      render: (r) =>
        r.n_industries == null ? <>—</> : <>{r.n_industries.toLocaleString()}</>,
      filter: { type: "range", value: (r) => r.n_industries },
      info: "Rollup row only: distinct industry buckets (incl. untagged) that carried at least one article that day.",
    },
  ];
  return (
    <ExpandedTable
      columns={columns}
      rows={rows}
      rowKey={(r) => `${r.date}|${r.industry_id}`}
      maxHeight={560}
      enableFilters={enableFilters}
      filterScopeDeps={filterScopeDeps}
      paginated
      rowsPerPageOptions={[20, 50, 100]}
      defaultRowsPerPage={20}
    />
  );
}
