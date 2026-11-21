/**
 * ContentAuditTable — the shared ExpandedTable for the News Audit page's
 * CONTENT view: one row per 5-day window per industry ("" = untagged),
 * with the window metrics + the keyword rollup columns.
 */
import ExpandedTable, { type ExpandedTableColumn } from "@/shared/components/ExpandedTable";
import type { NewsContentAuditRow } from "@shared/types";
import { fmtPct, sentimentSx } from "./format";

function windowLabel(r: NewsContentAuditRow): string {
  return r.is_complete
    ? `${r.window_start} → ${r.window_end}`
    : `${r.window_start} → ${r.window_end} (partial)`;
}

export default function ContentAuditTable({
  rows,
  enableFilters,
  filterScopeDeps = [],
}: {
  rows: NewsContentAuditRow[];
  enableFilters: boolean;
  /** The page's toolbar filters (slider range) — a change resets the
   *  per-column header filters so a narrowed row set never dead-ends
   *  against a stale tick selection. */
  filterScopeDeps?: unknown[];
}) {
  const columns: ExpandedTableColumn<NewsContentAuditRow>[] = [
    {
      key: "window",
      label: "Window",
      width: 210,
      render: (r) => windowLabel(r),
      filter: { type: "date", value: (r) => r.window_start },
    },
    {
      key: "industry",
      label: "Industry",
      width: 170,
      render: (r) =>
        r.industry_id.length > 0 ? (
          <>{r.industry_label}</>
        ) : (
          <span style={{ opacity: 0.55 }}>· untagged ·</span>
        ),
      filter: {
        type: "ticks",
        value: (r) => (r.industry_id.length > 0 ? r.industry_label : "(untagged)"),
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
      key: "distinct_keywords",
      label: "KW Distinct",
      align: "right",
      width: 100,
      render: (r) =>
        r.distinct_keywords == null ? <>—</> : <>{r.distinct_keywords.toLocaleString()}</>,
      filter: { type: "range", value: (r) => r.distinct_keywords },
      group: "Keywords",
      info: "How many DIFFERENT market/industry keywords the window's content hit (one keyword hit by many articles counts once).",
    },
    {
      key: "keyword_mentions",
      label: "Mentions",
      align: "right",
      width: 100,
      render: (r) =>
        r.keyword_mentions == null ? <>—</> : <>{r.keyword_mentions.toLocaleString()}</>,
      filter: { type: "range", value: (r) => r.keyword_mentions },
      group: "Keywords",
      info: "Total affirmative keyword mentions (negated readings — 并未下跌 — never count).",
    },
    {
      key: "articles_with_keywords",
      label: "KW Articles",
      align: "right",
      width: 105,
      render: (r) =>
        r.articles_with_keywords == null ? (
          <>—</>
        ) : (
          <>{r.articles_with_keywords.toLocaleString()}</>
        ),
      filter: { type: "range", value: (r) => r.articles_with_keywords },
      group: "Keywords",
      info: "Articles that hit at least one keyword — the coverage denominator to read against Items.",
    },
  ];
  return (
    <ExpandedTable
      columns={columns}
      rows={rows}
      rowKey={(r) => `${r.window_start}|${r.industry_id}`}
      maxHeight={560}
      enableFilters={enableFilters}
      filterScopeDeps={filterScopeDeps}
      paginated
      rowsPerPageOptions={[20, 50, 100]}
      defaultRowsPerPage={20}
    />
  );
}
