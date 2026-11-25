/**
 * ProviderAuditItems — the News Audit provider row's EXPANSION content:
 * the origin's articles inside the audited 5-day window, 5 PER PAGE.
 *
 * Pure data glue — the display is the SHARED news feed kit
 * (NewsFeedPage → NewsPostCard, the News page's own cards) driven through
 * the existing GET /api/news/items endpoint (source + author + date range
 * + limit/offset). author rides the query VERBATIM: "" is the
 * provider-level sentinel (author= → exactly the source's own rows).
 */
import { useEffect, useState } from "react";
import { Box } from "@mui/material";
import NewsFeedPage from "@/shared/components/news/NewsFeedPage";
import { fmtDate } from "@/shared/datetime";
import { fetchNewsItems } from "@/lib/api-client";
import type { NewsItem, NewsProviderAuditRow } from "@shared/types";

/** The expansion's page size — five articles per page (user-specified). */
const PAGE_SIZE = 5;

export default function ProviderAuditItems({
  row,
}: {
  row: NewsProviderAuditRow;
}) {
  // 1-based feed page — every change refetches that single article
  // (offset paging over the origin-window, ordering fixed server-side).
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<NewsItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const resp = await fetchNewsItems({
          source: row.source,
          author: row.author,
          date_from: row.window_start,
          date_to: row.window_end,
          limit: PAGE_SIZE,
          offset: (page - 1) * PAGE_SIZE,
          // The audit expansion must see what it audits — opt out of the
          // news API's default fake/blocked-origin exclusions.
          include_fake: true,
          include_blocked: true,
        });
        if (cancelled) return;
        setItems(resp.items);
        setTotal(resp.total);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [row.source, row.author, row.window_start, row.window_end, page]);

  return (
    <Box sx={{ px: 2, py: 1.5, bgcolor: "background.paper" }}>
      <NewsFeedPage
        items={items}
        total={total}
        page={page}
        onPageChange={setPage}
        loading={loading}
        error={error}
        pageSize={PAGE_SIZE}
        scopeLabel={`${row.source}${row.author ? ` · ${row.author}` : " · provider-level"} · ${fmtDate(row.window_start)} → ${fmtDate(row.window_end)}`}
        emptyText="该来源在此窗口没有文章。"
      />
    </Box>
  );
}
