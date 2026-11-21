/**
 * ProviderAuditTable — the shared ExpandedTable for the News Audit page's
 * PROVIDER view: one row per 5-day window per origin (provider-level rows
 * carry an empty author), with the window metrics + the registry's blocked
 * flag and seen-date span.
 */
import ExpandedTable, { type ExpandedTableColumn } from "@/shared/components/ExpandedTable";
import type { NewsProviderAuditRow } from "@shared/types";
import { authorFilterLabel, fmtPct, sentimentSx } from "./format";

function windowLabel(r: NewsProviderAuditRow): string {
  return r.is_complete
    ? `${r.window_start} → ${r.window_end}`
    : `${r.window_start} → ${r.window_end} (partial)`;
}

export default function ProviderAuditTable({
  rows,
  enableFilters,
  filterScopeDeps = [],
}: {
  rows: NewsProviderAuditRow[];
  enableFilters: boolean;
  /** The page's toolbar filters (slider range / provider / author) — a
   *  change resets the per-column header filters so a narrowed row set
   *  never dead-ends against a stale tick selection. */
  filterScopeDeps?: unknown[];
}) {
  const columns: ExpandedTableColumn<NewsProviderAuditRow>[] = [
    {
      key: "window",
      label: "Window",
      width: 210,
      render: (r) => windowLabel(r),
      filter: { type: "date", value: (r) => r.window_start },
    },
    {
      key: "source",
      label: "Provider",
      width: 110,
      render: (r) => <>{r.source}</>,
      filter: { type: "ticks", value: (r) => r.source },
    },
    {
      key: "author",
      label: "Author",
      width: 180,
      render: (r) =>
        r.author.length > 0 ? (
          <>{r.author}</>
        ) : (
          <span style={{ opacity: 0.55 }}>· provider-level ·</span>
        ),
      filter: {
        type: "ticks",
        value: (r) => authorFilterLabel(r.author),
      },
    },
    {
      key: "num_items",
      label: "Items",
      align: "right",
      width: 80,
      render: (r) => <>{r.num_items.toLocaleString()}</>,
      filter: { type: "range", value: (r) => r.num_items },
    },
    {
      key: "num_digested",
      label: "Digested",
      align: "right",
      width: 90,
      render: (r) => <>{r.num_digested.toLocaleString()}</>,
      filter: { type: "range", value: (r) => r.num_digested },
    },
    {
      key: "num_fake",
      label: "Fake",
      align: "right",
      width: 70,
      render: (r) => <>{r.num_fake.toLocaleString()}</>,
      filter: { type: "range", value: (r) => r.num_fake },
    },
    {
      key: "fake_pct",
      label: "Fake %",
      align: "right",
      width: 90,
      render: (r) => <>{fmtPct(r.fake_pct)}</>,
      filter: { type: "range", value: (r) => r.fake_pct },
      info: "Share of DIGESTED articles flagged fake (num_fake / num_digested). Verdicts exist only where the LLM digestion ran.",
    },
    {
      key: "avg_sentiment",
      label: "Sentiment",
      align: "right",
      width: 100,
      render: (r) =>
        r.avg_sentiment == null ? (
          <>—</>
        ) : (
          <span style={sentimentSx(r.avg_sentiment)}>
            {r.avg_sentiment.toFixed(2)}
          </span>
        ),
      filter: { type: "range", value: (r) => r.avg_sentiment },
      info: "Average digestion sentiment in [-5, +5] over the window's digested rows.",
    },
    {
      key: "avg_word_count",
      label: "Avg Words",
      align: "right",
      width: 95,
      render: (r) =>
        r.avg_word_count == null ? <>—</> : <>{Math.round(r.avg_word_count).toLocaleString()}</>,
      filter: { type: "range", value: (r) => r.avg_word_count },
    },
    {
      key: "num_industries",
      label: "Industries",
      align: "right",
      width: 95,
      render: (r) => (r.num_industries == null ? <>—</> : <>{r.num_industries}</>),
      filter: { type: "range", value: (r) => r.num_industries },
      info: "COUNT(DISTINCT industry_id) over the window's items — the origin's industry spread.",
    },
    {
      key: "is_blocked",
      label: "Blocked",
      align: "center",
      width: 90,
      render: (r) =>
        r.is_blocked ? (
          <span style={{ color: "error.main", fontWeight: 600 }}>blocked</span>
        ) : (
          <span style={{ opacity: 0.4 }}>—</span>
        ),
      filter: { type: "ticks", value: (r) => (r.is_blocked ? "blocked" : "—") },
      info: "The registry blocklist flag (text.news_providers.is_blocked) — manual curation.",
    },
    {
      key: "seen",
      label: "Seen",
      width: 190,
      render: (r) =>
        r.first_seen_date == null || r.last_seen_date == null ? (
          <>—</>
        ) : (
          <>{`${r.first_seen_date} → ${r.last_seen_date}`}</>
        ),
    },
  ];
  return (
    <ExpandedTable
      columns={columns}
      rows={rows}
      rowKey={(r) => `${r.window_start}|${r.source}|${r.author}`}
      maxHeight={560}
      enableFilters={enableFilters}
      filterScopeDeps={filterScopeDeps}
      paginated
      rowsPerPageOptions={[20, 50, 100]}
      defaultRowsPerPage={20}
    />
  );
}
