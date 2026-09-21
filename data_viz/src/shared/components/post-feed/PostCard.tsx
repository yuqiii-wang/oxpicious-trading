/**
 * PostCard — the shared expandable post-card base behind every social-feed
 * page (News cards, AI Q&A cards, Ask history cards).
 *
 * One card = a bordered post box that expands on click: the header row
 * (avatar/icon · title · date · trailing extras), the chip row, the
 * headline, a 2-line snippet while collapsed, and the lazily fetched
 * detail once expanded ("加载中…" spinner in between). The action bar
 * carries the expand toggle plus any card-specific buttons (e.g. the
 * news card's comments toggle), and children render below it (the
 * comments thread).
 *
 * The card owns the expand/detail state machine; subclasses only supply
 * content slots and a *fetchDetail* loader fired once on first expand
 * (a failed fetch leaves detail null — the *detailEmptyText* line shows,
 * and collapsing + re-expanding retries).
 */
import { useState, type ReactNode } from "react";
import {
  Box,
  Button,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import {
  ExpandLess as ExpandLessIcon,
  ExpandMore as ExpandMoreIcon,
} from "@mui/icons-material";

/** Structured header row pieces (all optional except icon + title). */
export interface PostCardHeader {
  /** Left identity: the Avatar (news author) or MUI icon (AI cards). */
  icon: ReactNode;
  /** Bold name — author, "AI Q&A #id", "Ask #id". */
  title: ReactNode;
  /** Muted date text right of the title. */
  date?: ReactNode;
  /** Inline status chips right of the date (failed · online search). */
  afterDate?: ReactNode;
  /** ml:auto right side (votes · 原文 link · sentiment chip). */
  trailing?: ReactNode;
}

interface PostCardProps<T> {
  /** Error accent — the card border turns error.light (failed asks). */
  error?: boolean;
  header: PostCardHeader;
  /** Chip row under the header (source · code · model · counts). */
  chips?: ReactNode;
  /** Post headline (title / question); *headlineHref* turns it into a
   *  stopPropagation external link. */
  headline?: ReactNode;
  headlineHref?: string | null;
  /** Collapsed 2-line snippet (null/empty hides it). */
  snippet?: string | null;
  /** Render the snippet in the error color (failed asks). */
  snippetError?: boolean;
  /** Lazy loader fired once on first expand. */
  fetchDetail?: () => Promise<T | null>;
  /** Extra header-trailing content that reads the fetch state (e.g. the
   *  AI card's sentiment chip, which needs the fetched detail). Rendered
   *  after *header.trailing* inside the same ml:auto box. */
  renderHeaderTrailing?: (detail: T | null, expanded: boolean) => ReactNode;
  /** Expanded body from the fetched detail (non-null only). */
  renderDetail?: (detail: T) => ReactNode;
  /** Line shown when expanded, not loading, detail is null — a failed
   *  fetch or nothing to render (（无正文内容）/（详情加载失败）). */
  detailEmptyText?: string;
  /** Extra action-bar buttons (the comments toggle); re-rendered when
   *  the detail arrives so live counts can surface. */
  renderActions?: (detail: T | null) => ReactNode;
  /** Collapsed label of the expand toggle (default 展开全文). */
  openLabel?: string;
  /** Below the action bar — the comments thread section. */
  children?: ReactNode;
}

export default function PostCard<T>({
  error = false,
  header,
  chips,
  headline,
  headlineHref,
  snippet,
  snippetError = false,
  fetchDetail,
  renderHeaderTrailing,
  renderDetail,
  detailEmptyText,
  renderActions,
  openLabel = "展开全文",
  children,
}: PostCardProps<T>) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<T | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const toggleExpand = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && fetchDetail && detail === null && !detailLoading) {
      setDetailLoading(true);
      try {
        setDetail(await fetchDetail());
      } catch {
        setDetail(null);
      } finally {
        setDetailLoading(false);
      }
    }
  };

  return (
    <Box
      onClick={() => { if (!expanded) void toggleExpand(); }}
      sx={{
        p: 1.25,
        border: "1px solid",
        borderColor: error ? "error.light" : "divider",
        borderRadius: 1.5,
        bgcolor: "background.paper",
        cursor: expanded ? "default" : "pointer",
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      {/* ---- header: avatar/icon · title · date · extras · trailing ---- */}
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mb: 0.75 }}>
        {header.icon}
        <Typography variant="subtitle2" sx={{ fontWeight: 700, fontSize: "0.8rem" }}>
          {header.title}
        </Typography>
        {header.date && (
          <Typography variant="subtitle2" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
            {header.date}
          </Typography>
        )}
        {header.afterDate}
        {(header.trailing || renderHeaderTrailing) && (
          <Box sx={{ ml: "auto", display: "flex", alignItems: "center", gap: 0.5 }}>
            {header.trailing}
            {renderHeaderTrailing?.(detail, expanded)}
          </Box>
        )}
      </Stack>

      {/* ---- chips: source · code · model · counts ---- */}
      {chips && (
        <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5, mb: 0.5 }}>
          {chips}
        </Stack>
      )}

      {/* ---- headline (post title / question) ---- */}
      {headline && (
        <Typography
          component={headlineHref ? "a" : "div"}
          href={headlineHref ?? undefined}
          target={headlineHref ? "_blank" : undefined}
          rel="noopener noreferrer"
          onClick={headlineHref ? (e) => e.stopPropagation() : undefined}
          sx={{
            display: "block",
            fontWeight: 700,
            fontSize: "0.9rem",
            lineHeight: 1.35,
            color: "text.primary",
            textDecoration: "none",
            mb: 0.5,
            "&:hover": headlineHref ? { textDecoration: "underline" } : {},
          }}
        >
          {headline}
        </Typography>
      )}

      {/* ---- body: 2-line snippet collapsed, fetched detail expanded ---- */}
      {!expanded && snippet && (
        <Typography
          variant="body2"
          color={snippetError ? "error.main" : "text.secondary"}
          sx={{
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {snippet}
        </Typography>
      )}
      {expanded && (
        <Box sx={{ my: 0.75 }}>
          {detailLoading ? (
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", py: 1 }}>
              <CircularProgress size={14} thickness={5} />
              <Typography variant="body2" color="text.secondary">加载中…</Typography>
            </Stack>
          ) : detail !== null && renderDetail ? (
            renderDetail(detail)
          ) : (
            detailEmptyText && (
              <Typography variant="body2" color="text.secondary" sx={{ py: 0.5 }}>
                {detailEmptyText}
              </Typography>
            )
          )}
        </Box>
      )}

      {/* ---- actions: expand toggle · card-specific buttons ---- */}
      <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", mt: 0.75 }}>
        <Button
          size="small"
          color="inherit"
          startIcon={expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          onClick={(e) => { e.stopPropagation(); void toggleExpand(); }}
          sx={{ fontSize: "0.7rem", minWidth: 0, px: 0.75, color: "text.secondary" }}
        >
          {expanded ? "收起" : openLabel}
        </Button>
        {renderActions?.(detail)}
      </Stack>

      {/* ---- below the actions (the comments thread section) ---- */}
      {children}
    </Box>
  );
}
