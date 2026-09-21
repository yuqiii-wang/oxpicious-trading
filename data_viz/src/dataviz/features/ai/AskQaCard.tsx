/**
 * AskQaCard — one persisted interactive AI ask in the DataViz > AI feed's
 * "QA by Ask" mode (text.llm_qa_by_ask, written by llm_agents.llm_ask.storage
 * for every AI Ask modal submit).
 *
 * Built on the shared PostCard base (expand-on-click, lazy detail fetch):
 * failed asks get the error border + red snippet and the expanded body
 * swaps the answer for the stored error tail. Expanding lazily fetches the
 * full row (GET /api/ai/ask-qa) — the untruncated answer, the chart-context
 * line (title · window · page), the derived keyword chips, and the stored
 * screenshots as thumbnails (GET /api/ai/ask-image bytes) that open a zoom
 * lightbox on click.
 */
import { Fragment, useState } from "react";
import { Box, Dialog, Stack, Typography } from "@mui/material";
import { QuestionAnswer as QuestionAnswerIcon } from "@mui/icons-material";
import PostCard from "@/shared/components/post-feed/PostCard";
import PostChip from "@/shared/components/post-feed/PostChip";
import { aiAskImageUrl, fetchAiAskDetail } from "@/lib/api-client";
import type { AiAskHistoryDetail, AiAskHistoryItem } from "@shared/types";

export default function AskQaCard({ item }: { item: AiAskHistoryItem }) {
  const [zoomedImage, setZoomedImage] = useState<number | null>(null);

  const failed = item.status !== "answered";

  return (
    <Fragment>
      <PostCard<AiAskHistoryDetail>
        error={failed}
        header={{
          icon: <QuestionAnswerIcon sx={{ fontSize: 20, color: "primary.main" }} />,
          title: `Ask #${item.ask_id}`,
          date: item.date,
          afterDate: (
            <>
              {failed && <PostChip label="failed" color="error" />}
              {item.online_search && <PostChip label="online search" color="info" />}
            </>
          ),
        }}
        chips={
          <>
            {item.code && <PostChip label={item.code} color="secondary" />}
            {item.product && <PostChip label={item.product} color="primary" />}
            {item.llm_model && <PostChip label={item.llm_model} />}
            {item.n_images > 0 && <PostChip label={`${item.n_images} 张截图`} />}
            {item.n_keywords > 0 && <PostChip label={`${item.n_keywords} 关键词`} />}
          </>
        }
        headline={item.question}
        snippet={item.answer_snippet}
        snippetError={failed}
        fetchDetail={() => fetchAiAskDetail(item.ask_id)}
        renderDetail={(detail) => (
          <>
            {/* Chart context line — what the question was asked against. */}
            {(detail.chart_title || detail.page || detail.window_start || detail.industry_id) && (
              <Typography variant="caption" color="text.secondary" sx={{ display: "block", mb: 0.5 }}>
                {[
                  detail.chart_title,
                  detail.chart_kind,
                  detail.window_start && detail.window_end
                    ? `${detail.window_start} → ${detail.window_end}`
                    : null,
                  detail.page,
                  detail.industry_id ?? detail.sector_id,
                ].filter(Boolean).join(" · ")}
              </Typography>
            )}

            {/* The full answer (or the failure detail for failed asks). */}
            <Box
              sx={{
                whiteSpace: "pre-wrap",
                bgcolor: "action.hover",
                borderRadius: 1,
                p: 1.25,
                maxHeight: "40vh",
                overflowY: "auto",
              }}
            >
              <Typography variant="body2" color={failed ? "error.main" : undefined}>
                {detail.answer ?? detail.error_tail ?? "（无内容）"}
              </Typography>
            </Box>

            {/* Derived keyword chips — the ask's search tags (codes, product,
                in-plot items, series names). */}
            {detail.keywords.length > 0 && (
              <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5, mt: 0.75 }}>
                {detail.keywords.map((k) => (
                  <PostChip key={k.keyword} label={k.keyword} title={k.kind} />
                ))}
              </Stack>
            )}

            {/* Stored screenshots — thumbnails that open a zoom lightbox
                (bytes via GET /api/ai/ask-image, browser-cached by id). */}
            {detail.images.length > 0 && (
              <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap", gap: 1, mt: 0.75 }}>
                {detail.images.map((img) => (
                  <Box
                    key={img.image_id}
                    component="img"
                    src={aiAskImageUrl(img.image_id)}
                    alt={img.label ?? `Ask screenshot ${img.position + 1}`}
                    title="Click to zoom"
                    onClick={(e) => { e.stopPropagation(); setZoomedImage(img.image_id); }}
                    sx={{
                      height: 72,
                      width: "auto",
                      borderRadius: 1,
                      border: "1px solid",
                      borderColor: "divider",
                      cursor: "zoom-in",
                      display: "block",
                      "&:hover": { borderColor: "primary.main" },
                    }}
                  />
                ))}
              </Stack>
            )}
          </>
        )}
        detailEmptyText="（详情加载失败）"
        openLabel={failed ? "展开错误详情" : "展开回答"}
      />

      {/* screenshot zoom lightbox — click the image or backdrop to close */}
      <Dialog
        open={zoomedImage !== null}
        onClose={() => setZoomedImage(null)}
        maxWidth={false}
        onClick={() => setZoomedImage(null)}
        PaperProps={{ sx: { bgcolor: "transparent", boxShadow: "none" } }}
      >
        {zoomedImage !== null && (
          <Box
            component="img"
            src={aiAskImageUrl(zoomedImage)}
            alt="Ask screenshot (zoomed)"
            sx={{
              display: "block",
              maxWidth: "94vw",
              maxHeight: "88vh",
              borderRadius: 1,
              cursor: "zoom-out",
            }}
          />
        )}
      </Dialog>
    </Fragment>
  );
}
