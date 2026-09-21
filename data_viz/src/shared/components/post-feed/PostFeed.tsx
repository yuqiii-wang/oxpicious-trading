/**
 * PostFeed — the shared feed chrome for every post-card feed (News, AI
 * Q&A, Ask history, Market Sentiments): the scope/count line with pager,
 * the load-error alert, the card column, and the empty state. Cards come
 * in as children so each feed maps its own item type (and can mix types).
 *
 * The consumers keep their own page size (news=50, AI feeds=20) and pass
 * it in — the pager math and the count line styling live here.
 */
import type { ReactNode } from "react";
import { Alert, Box, CircularProgress, Pagination, Typography } from "@mui/material";

interface Props {
  total: number;
  page: number; // 1-based
  pageSize: number;
  onPageChange: (page: number) => void;
  loading: boolean;
  error: string | null;
  /** Error-line prefix ("Failed to load news" / "Failed to load Q&A"). */
  errorLabel: string;
  /** Left side of the count line — scope, day pick, keyword, composed by
   *  the caller; the " · {total} {countNoun}" suffix is added here. */
  scopeLabel: ReactNode;
  /** Count unit — 篇 (news) / 条问答 / 条提问. */
  countNoun: string;
  /** No items on the page (caller: items.length === 0) — shows emptyText. */
  empty?: boolean;
  emptyText: string;
  children: ReactNode;
}

export default function PostFeed({
  total,
  page,
  pageSize,
  onPageChange,
  loading,
  error,
  errorLabel,
  scopeLabel,
  countNoun,
  empty = false,
  emptyText,
  children,
}: Props) {
  if (error) {
    return (
      <Alert severity="error" variant="filled" sx={{ mb: 2 }}>
        {errorLabel}: {error}
      </Alert>
    );
  }
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <Box>
      <Box sx={{ display: "flex", alignItems: "center", mb: 1 }}>
        <Typography variant="subtitle2" color="text.secondary">
          {scopeLabel} · {total.toLocaleString()} {countNoun}
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

      <Box sx={{ display: "flex", flexDirection: "column", gap: 1 }}>
        {children}
        {!loading && empty && (
          <Typography variant="body2" color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
            {emptyText}
          </Typography>
        )}
      </Box>
    </Box>
  );
}
