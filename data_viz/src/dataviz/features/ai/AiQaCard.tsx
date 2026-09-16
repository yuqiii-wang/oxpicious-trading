/**
 * AiQaCard — one LLM Q&A knowledge-base post in the DataViz > AI feed.
 *
 * Collapsed it shows the question (post headline), chips (industry · model ·
 * language · n_sources) and the 2-line answer snippet. Clicking the
 * card expands it: the full row is lazily fetched (GET /api/ai/qa) — the
 * untruncated answer and the per-ref citation list (text.llm_qa_refs
 * joined back to text.news, each typed exact/relevant and linked to its
 * source). The answer renders through AiQaRefBox: its inline
 * [来源：ref_N …] citation brackets become toggle buttons that expand the
 * resolved article inline at the citation site — tags (typing +
 * provenance, source) plus a scrollable content preview. The stored
 * context passage (a format_references text dump) is NOT rendered.
 *
 * Mirrors NewsPostCard's expand-card pattern (same feed idioms).
 */
import { useState } from "react";
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import {
  ExpandLess as ExpandLessIcon,
  ExpandMore as ExpandMoreIcon,
  Psychology as PsychologyIcon,
} from "@mui/icons-material";
import { fetchAiQaDetail } from "@/lib/api-client";
import type { AiQaDetail, AiQaItem } from "@shared/types";
import AiQaRefBox from "./AiQaRefBox";

/** Sentiment chip color by score ([-5, 5] — red bearish, green bullish). */
function sentimentColor(level: number): "error" | "success" | "default" {
  return level < -0.5 ? "error" : level > 0.5 ? "success" : "default";
}

export default function AiQaCard({ item }: { item: AiQaItem }) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<AiQaDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const toggleExpand = async () => {
    const next = !expanded;
    setExpanded(next);
    if (next && detail === null && !detailLoading) {
      setDetailLoading(true);
      try {
        setDetail(await fetchAiQaDetail(item.qa_id));
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
        borderColor: "divider",
        borderRadius: 1.5,
        bgcolor: "background.paper",
        cursor: expanded ? "default" : "pointer",
        "&:hover": { bgcolor: "action.hover" },
      }}
    >
      {/* ---- header: AI avatar · date · status ..... sentiment ---- */}
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mb: 0.5 }}>
        <PsychologyIcon sx={{ fontSize: 20, color: "primary.main" }} />
        <Typography variant="subtitle2" sx={{ fontWeight: 700, fontSize: "0.8rem" }}>
          AI Q&amp;A #{item.qa_id}
        </Typography>
        <Typography variant="subtitle2" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
          {item.date}
        </Typography>
        <Box sx={{ ml: "auto", display: "flex", alignItems: "center", gap: 0.5 }}>
          {expanded && detail?.avg_ref_sentiment != null && (
            <Chip
              label={`情绪 ${detail.avg_ref_sentiment > 0 ? "+" : ""}${detail.avg_ref_sentiment}`}
              size="small"
              color={sentimentColor(detail.avg_ref_sentiment)}
              variant="outlined"
              sx={{ fontSize: "0.65rem", height: 18 }}
            />
          )}
        </Box>
      </Stack>

      {/* ---- chips: sector (sector-only items) · industry · model · language · sources ---- */}
      <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5, mb: 0.5 }}>
        {!item.industry_id && item.sector_id && (
          <Chip label={item.sector_id} size="small" color="secondary" variant="outlined"
            sx={{ fontSize: "0.65rem", height: 18 }} />
        )}
        {item.industry_id && (
          <Chip label={item.industry_id} size="small" color="secondary" variant="outlined"
            sx={{ fontSize: "0.65rem", height: 18 }} />
        )}
        {item.llm_model && (
          <Chip label={item.llm_model} size="small" color="primary" variant="outlined"
            sx={{ fontSize: "0.65rem", height: 18 }} />
        )}
        {item.language && (
          <Chip label={item.language} size="small" variant="outlined"
            sx={{ fontSize: "0.65rem", height: 18 }} />
        )}
        <Chip label={`${item.n_sources} 篇来源`} size="small" variant="outlined"
          sx={{ fontSize: "0.65rem", height: 18 }} />
      </Stack>

      {/* ---- question (post headline) ---- */}
      <Typography sx={{ fontWeight: 700, fontSize: "0.9rem", lineHeight: 1.35, mb: 0.5 }}>
        {item.question}
      </Typography>

      {/* ---- body: answer snippet when collapsed, full row when expanded ---- */}
      {!expanded && item.answer_snippet && (
        <Typography variant="body2" color="text.secondary"
          sx={{
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}>
          {item.answer_snippet}
        </Typography>
      )}
      {expanded && (
        <Box sx={{ my: 0.75 }}>
          {detailLoading ? (
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", py: 1 }}>
              <CircularProgress size={14} thickness={5} />
              <Typography variant="body2" color="text.secondary">加载中…</Typography>
            </Stack>
          ) : detail ? (
            <>
              {/* Answer — inline [来源：ref_N] citation brackets become
                  expandable ref buttons (AiQaRefBox); each expansion carries
                  the resolved article's tags + content preview. */}
              <AiQaRefBox
                text={detail.answer}
                refs={detail.refs}
                sx={{ typography: "body2", lineHeight: 1.65 }}
              />
            </>
          ) : (
            <Typography variant="body2" color="text.secondary" sx={{ py: 0.5 }}>
              （详情加载失败）
            </Typography>
          )}
        </Box>
      )}

      {/* ---- actions: expand toggle ---- */}
      <Stack direction="row" spacing={0.5} sx={{ alignItems: "center", mt: 0.75 }}>
        <Button
          size="small"
          color="inherit"
          startIcon={expanded ? <ExpandLessIcon /> : <ExpandMoreIcon />}
          onClick={(e) => { e.stopPropagation(); void toggleExpand(); }}
          sx={{ fontSize: "0.7rem", minWidth: 0, px: 0.75, color: "text.secondary" }}
        >
          {expanded ? "收起" : "展开回答"}
        </Button>
      </Stack>
    </Box>
  );
}
