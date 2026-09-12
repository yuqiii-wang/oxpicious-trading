/**
 * NewsSearchResults — live question-search results for the News page.
 *
 * Rendered in place of NewsList while a question search result is shown.
 * Rows are the RAW zhihu items returned by POST /api/news/search (not
 * text.news rows — the artifact only joins the corpus after the next
 * builds.text run, so this panel is the only immediate view of the search).
 */
import { Alert, Box, Chip, Typography } from "@mui/material";
import { OpenInNew } from "@mui/icons-material";
import type { NewsSearchItem, NewsSearchResponse } from "@shared/types";

/** EditTime (unix seconds, string) → YYYY-MM-DD in Asia/Shanghai. */
function shanghaiDate(editTime: string | null | undefined): string {
  if (!editTime) return "—";
  const ts = Number(editTime);
  if (!Number.isFinite(ts) || ts <= 0) return "—";
  // "en-CA" formats as ISO YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(
    new Date(ts * 1000),
  );
}

interface Props {
  result: NewsSearchResponse;
  /** Run/parse failure message (mutually exclusive with usable items). */
  error: string | null;
  onDismiss: () => void;
}

export default function NewsSearchResults({ result, error, onDismiss }: Props) {
  const scopeBits = [
    result.author ? `作者 ${result.author}` : null,
    `${result.total.toLocaleString()} 条`,
  ].filter(Boolean).join(" · ");

  return (
    <Box>
      <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1, flexWrap: "wrap" }}>
        <Chip
          label={`知乎搜索: ${result.question}`}
          size="small"
          color="primary"
          variant="filled"
          onDelete={onDismiss}
          sx={{ fontSize: "0.7rem", maxWidth: 560 }}
        />
        <Typography variant="subtitle2" color="text.secondary">
          {scopeBits}
        </Typography>
      </Box>

      {error && (
        <Alert severity="error" variant="filled" sx={{ mb: 1.5 }}>
          提问搜索失败：{error}
        </Alert>
      )}
      {!error && result.already_running && (
        <Alert severity="info" sx={{ mb: 1.5 }}>
          相同来源的提问搜索正在进行中 — 显示的是最近一次结果。
        </Alert>
      )}
      {!error && result.success && result.items.length === 0 && (
        <Typography variant="body2" color="text.secondary" sx={{ py: 4, textAlign: "center" }}>
          知乎没有返回匹配的提问内容 — 换个问法或清除 Author 筛选后再试。
        </Typography>
      )}

      <Box sx={{ display: "flex", flexDirection: "column", gap: 0.75 }}>
        {result.items.map((it: NewsSearchItem) => (
          <Box
            key={it.ContentID || it.Title}
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
              <Chip label={shanghaiDate(it.EditTime)} size="small" variant="outlined" sx={{ fontSize: "0.7rem" }} />
              {it.AuthorName && (
                <Chip label={it.AuthorName} size="small" color="secondary" variant="outlined"
                  sx={{ fontSize: "0.7rem", maxWidth: 220 }} />
              )}
              {it.ContentType && (
                <Chip label={it.ContentType} size="small" variant="outlined" sx={{ fontSize: "0.7rem" }} />
              )}
              {it.VoteUpCount && Number(it.VoteUpCount) > 0 && (
                <Chip label={`▲ ${Number(it.VoteUpCount).toLocaleString()}`} size="small" variant="outlined"
                  sx={{ fontSize: "0.7rem" }} />
              )}
            </Box>
            <Typography
              component="a"
              href={it.Url ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
              sx={{
                display: "block",
                mt: 0.5,
                fontWeight: 600,
                fontSize: "0.9rem",
                color: "text.primary",
                textDecoration: "none",
                cursor: it.Url ? "pointer" : "default",
                "&:hover": it.Url ? { textDecoration: "underline" } : {},
              }}
            >
              {it.Title}
              {it.Url && (
                <OpenInNew sx={{ fontSize: "0.85rem", ml: 0.5, verticalAlign: "text-bottom",
                  color: "text.secondary" }} />
              )}
            </Typography>
            {it.ContentText && (
              <Typography variant="body2" color="text.secondary"
                sx={{
                  mt: 0.25,
                  display: "-webkit-box",
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: "vertical",
                  overflow: "hidden",
                }}>
                {it.ContentText.slice(0, 200)}
              </Typography>
            )}
          </Box>
        ))}
      </Box>
    </Box>
  );
}
