/**
 * NewsList — article list for the News page.
 *
 * One row per article: date + source chips, industry chip (canonical
 * industry_id), title (linked to the source URL when crawled) and a snippet.
 * Paginated (server-side via offset); count line reflects the same
 * co-filters as the date bar (industry scope ∧ keyword ∧ optional date).
 */
import { Alert, Box, Chip, CircularProgress, Pagination, Typography } from "@mui/material";
import { OpenInNew } from "@mui/icons-material";
import type { NewsItem } from "@shared/types";

/** Server-side page size — also imported by NewsPage for the offset math. */
export const PAGE_SIZE = 50;

interface Props {
  items: NewsItem[];
  total: number;
  page: number; // 1-based
  onPageChange: (page: number) => void;
  loading: boolean;
  error: string | null;
  /** Reflects the active co-filters in the count line when set. */
  scopeLabel: string;
}

export default function NewsList({
  items,
  total,
  page,
  onPageChange,
  loading,
  error,
  scopeLabel,
}: Props) {
  if (error) {
    return <Alert severity="error" variant="filled" sx={{ mb: 2 }}>Failed to load news: {error}</Alert>;
  }
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <Box>
      <Box sx={{ display: "flex", alignItems: "center", mb: 1 }}>
        <Typography variant="subtitle2" color="text.secondary">
          {scopeLabel} · {total.toLocaleString()} 篇
          {loading && (
            <CircularProgress size={12} sx={{ ml: 1, verticalAlign: "middle" }} />
          )}
        </Typography>
        {totalPages > 1 && (
          <Pagination
            count={totalPages}
            page={page}
            onChange={(_, v) => onPageChange(v)}
            size="small"
            siblingCount={1}
            boundaryCount={1}
            sx={{ ml: "auto" }}
          />
        )}
      </Box>

      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
        {items.map((it) => (
          <Box
            key={it.news_id}
            sx={{
              p: 1,
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 1,
              bgcolor: "background.paper",
              "&:hover": { bgcolor: "action.hover" },
            }}
          >
            <Box sx={{ display: "flex", alignItems: "center", gap: 0.75, flexWrap: "wrap" }}>
              <Chip label={it.date} size="small" variant="outlined" sx={{ fontSize: "0.7rem" }} />
              {it.source && (
                <Chip label={it.source} size="small" color="primary" variant="outlined"
                  sx={{ fontSize: "0.7rem" }} />
              )}
              {it.industry_id && (
                <Chip label={it.industry_id} size="small" color="secondary" variant="outlined"
                  sx={{ fontSize: "0.7rem" }} />
              )}
            </Box>
            <Typography
              component="a"
              href={it.url ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
              sx={{
                display: "block",
                mt: 0.5,
                fontWeight: 600,
                fontSize: "0.9rem",
                color: "text.primary",
                textDecoration: "none",
                cursor: it.url ? "pointer" : "default",
                "&:hover": it.url ? { textDecoration: "underline" } : {},
              }}
            >
              {it.title}
              {it.url && (
                <OpenInNew sx={{ fontSize: "0.85rem", ml: 0.5, verticalAlign: "text-bottom",
                  color: "text.secondary" }} />
              )}
            </Typography>
            {it.snippet && (
              <Typography variant="body2" color="text.secondary"
                sx={{
                  mt: 0.25,
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                  overflow: "hidden",
                }}>
                {it.snippet}
              </Typography>
            )}
          </Box>
        ))}
        {!loading && items.length === 0 && (
          <Typography variant="body2" color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
            没有匹配的新闻 — 调整行业 / 日期 / 关键词后再试。
          </Typography>
        )}
      </Box>
    </Box>
  );
}
