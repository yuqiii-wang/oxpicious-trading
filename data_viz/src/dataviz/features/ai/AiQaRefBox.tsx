/**
 * AiQaRefBox — renders an LLM Q&A text passage (the answer) as parsed
 * markdown with its inline citation markers turned into expandable ref
 * buttons.
 *
 * The stored text embeds ZhiPu-style citation brackets — [ref_2],
 * [来源：ref_1], date-suffixed [来源：ref_1, 2026-09-11], and multi-ref
 * brackets [来源：ref_1，2026-09；来源：ref_2，2026-09-10] (fullwidth
 * ，/；, per-model variance) — and is LLM markdown (headings, lists,
 * tables). Parsing mirrors llm_agents' extract_cited_refs two-stage scan:
 * bracketed spans first, then every ref_N tag inside (brackets without any
 * ref_N tag are plain text and pass through untouched). Each citation
 * bracket is rewritten to a cite:// markdown link so the WHOLE passage
 * parses as one MarkdownView document and the citations stay inline in
 * their paragraph; the link renderer swaps them for the small toggle
 * buttons — clicking one expands an inline box right below the citation
 * showing the resolved article (llm.llm_qa_refs joined back to text.news):
 * the title link, the exact/relevant typing with its persisted provenance
 * (resolved_via — summary echo / corpus match / ddgs title search), whether
 * the answer actually cites the article (is_used) and the source as tags,
 * and a scrollable content preview (text.news.content, server-capped at
 * 4000 chars). A cited tag with no resolution row expands to a
 * placeholder instead.
 */
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  Chip,
  Stack,
  Typography,
  type SxProps,
} from "@mui/material";
import type { Theme } from "@mui/material/styles";
import type { ComponentProps } from "react";
import type { Components } from "react-markdown";
import { OpenInNew as OpenInNewIcon } from "@mui/icons-material";
import { MarkdownView } from "@/shared/markdown";
import type { AiQaRefItem } from "@shared/types";
import { fmtDate } from "@/shared/datetime";

// Same shapes as llm_agents/online_search_summary/core/citations.py: bracketed
// spans, then ref_N tags inside. Nested brackets can't occur (the span
// charset excludes [ ]). Non-citation brackets (e.g. "[要点]") stay text.
const BRACKET_SPAN_RE = /\[([^\[\]]*)\]/g;
const REF_TAG_RE = /ref_(\d+)/g;
const CITE_HREF_PREFIX = "cite://";

/** Rewrite the passage as one markdown document: every citation bracket
 *  becomes an adjacent run of cite:// links (one per deduplicated ref_N
 *  tag); brackets without any ref_N tag and the rest of the text pass
 *  through for the markdown parse. */
function toCiteMarkdown(text: string): string {
  return text.replace(BRACKET_SPAN_RE, (bracket, inner: string) => {
    const tags: string[] = [];
    for (const t of inner.matchAll(REF_TAG_RE)) {
      const tag = `ref_${t[1]}`;
      if (!tags.includes(tag)) tags.push(tag);
    }
    if (tags.length === 0) return bracket;
    return tags.map((tag) => `[${tag}](${CITE_HREF_PREFIX}${tag})`).join("");
  });
}

/** One toggle button per cited tag — filled while its box is expanded;
 *  primary-tinted for content-verified (exact) refs, neutral otherwise. */
function RefTagButton({
  tag,
  refItem,
  open,
  onToggle,
}: {
  tag: string;
  refItem?: AiQaRefItem;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <Button
      size="small"
      variant={open ? "contained" : "outlined"}
      color={refItem?.ref_type === "exact" ? "primary" : "inherit"}
      title={
        refItem
          ? `${tag} · ${refItem.ref_type} — ${refItem.title}`
          : `${tag}（未解析）`
      }
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
      sx={{
        minWidth: 0,
        px: 0.5,
        py: 0,
        mx: 0.15,
        fontSize: "0.6rem",
        lineHeight: 1.7,
        whiteSpace: "nowrap",
        verticalAlign: "baseline",
      }}
    >
      {tag}
    </Button>
  );
}

/** The persisted resolution provenance (llm.llm_qa_refs.resolved_via)
 *  behind the ref_type tag: 'summary' — the article came with the
 *  provider's summary response; 'corpus' / 'ddgs' — found after the
 *  summary by title search (stored corpus / ddgs candidate page). Legacy
 *  rows without the column fall back to the old ref_type semantics. */
function viaChipMeta(refItem: AiQaRefItem): { label: string; title: string } {
  if (refItem.resolved_via === "corpus") {
    return refItem.source === "zhihu"
      ? { label: "zhihu", title: "摘要后标题匹配 zhihu 语料（relevant）" }
      : { label: "corpus", title: "摘要后标题匹配本地语料（relevant）" };
  }
  if (refItem.resolved_via === "ddgs") {
    return { label: "ddgs", title: "摘要后 ddgs 标题检索 + 正文核验（relevant）" };
  }
  if (refItem.resolved_via === "zhihu") {
    return { label: "zhihu", title: "摘要后标题匹配 zhihu 在线检索（relevant）" };
  }
  if (refItem.resolved_via === "summary") {
    return { label: "summary", title: "来自 ZhiPu summary 响应的引用原文（exact）" };
  }
  return refItem.ref_type === "exact"
    ? { label: "ddgs", title: "旧数据：ddgs 标题检索 + 正文核验" }
    : { label: "web", title: "旧数据：web 检索命中" };
}

/** The expanded inline box for one cited tag — the resolved article row
 *  (title link, ref_type + provenance + source tags, date) plus a
 *  scrollable content preview (text.news.content, 4000-char server cap),
 *  or a placeholder when the tag has no llm.llm_qa_refs resolution.
 *  Also reused by the card's full source list (null-content rows render
 *  title-only — no content block). */
export function RefExpandBox({ tag, refItem }: { tag: string; refItem?: AiQaRefItem }) {
  if (!refItem) {
    return (
      <Box
        sx={{
          my: 0.5,
          p: 0.75,
          borderRadius: 1,
          bgcolor: "action.hover",
          border: "1px dashed",
          borderColor: "divider",
        }}
      >
        <Typography variant="caption" color="text.secondary">
          {tag} — 未找到引用解析记录
        </Typography>
      </Box>
    );
  }
  const link = refItem.url ?? refItem.resolved_url ?? null;
  const via = viaChipMeta(refItem);
  const exact = refItem.ref_type === "exact";
  const tagSx = { fontSize: "0.6rem", height: 16, flexShrink: 0, alignSelf: "center" } as const;
  return (
    <Box
      sx={{
        my: 0.5,
        p: 0.75,
        borderRadius: 1,
        bgcolor: "action.hover",
        border: "1px solid",
        borderColor: "divider",
      }}
    >
      <Stack direction="row" spacing={0.75} sx={{ alignItems: "baseline" }}>
        <Chip
          label={refItem.ref_type}
          size="small"
          variant="outlined"
          color={exact ? "primary" : "default"}
          title={exact ? "引用原文来自 summary 响应" : "摘要后按标题检索匹配的相关文章"}
          sx={tagSx}
        />
        <Chip
          label={via.label}
          size="small"
          variant="outlined"
          title={via.title}
          sx={{ ...tagSx, color: "text.secondary" }}
        />
        <Chip
          label={refItem.is_used ? "已引用" : "未引用"}
          size="small"
          variant="outlined"
          color={refItem.is_used ? "success" : "default"}
          title={
            refItem.is_used
              ? "回答正文引用了该文章（inline 引用标记命中）"
              : "回答未引用该文章 — 检索命中但摘要未采用的参考"
          }
          sx={tagSx}
        />
        {refItem.source && !["web", "zhihu"].includes(refItem.source) && refItem.source !== via.label && (
          <Chip
            label={refItem.source}
            size="small"
            variant="outlined"
            title={`来源 · ${refItem.source}`}
            sx={{ ...tagSx, color: "text.secondary" }}
          />
        )}
        <Box sx={{ minWidth: 0, overflow: "hidden" }}>
          <Typography
            component={link ? "a" : "span"}
            href={link ?? undefined}
            target={link ? "_blank" : undefined}
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            sx={{
              fontSize: "0.75rem",
              fontWeight: 600,
              color: "text.primary",
              textDecoration: "none",
              wordBreak: "break-all",
              "&:hover": link ? { textDecoration: "underline" } : {},
            }}
          >
            {refItem.title}
          </Typography>{" "}
          {refItem.date && (
            <Typography
              component="span"
              color="text.secondary"
              sx={{ fontSize: "0.65rem" }}
              title="文章发布日期（ref_time；未知时不显示）"
            >
              {fmtDate(refItem.date)}
            </Typography>
          )}
        </Box>
        {link && (
          <OpenInNewIcon
            sx={{ fontSize: 12, color: "text.secondary", flexShrink: 0, alignSelf: "center" }}
          />
        )}
      </Stack>
      {refItem.content && (
        <Typography
          variant="caption"
          sx={{
            display: "block",
            mt: 0.5,
            pt: 0.5,
            borderTop: "1px dashed",
            borderColor: "divider",
            maxHeight: 180,
            overflowY: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            lineHeight: 1.6,
            color: "text.secondary",
          }}
        >
          {refItem.content}
        </Typography>
      )}
    </Box>
  );
}

/** The markdown link renderer for cite:// hrefs — the tag button inline at
 *  the citation site, its expanded RefExpandBox (block-level, breaking the
 *  passage flow) right below while open. Every other link falls through to
 *  the plain themed anchor (LLM answers can carry their own [text](url)
 *  links). Expansion state is per citation occurrence. */
function makeCiteComponents(
  byRef: Map<string, AiQaRefItem>,
): Components {
  const CiteLink = ({ href, children }: ComponentProps<"a">) => {
    const tag = href?.startsWith(CITE_HREF_PREFIX)
      ? href.slice(CITE_HREF_PREFIX.length)
      : null;
    const [open, setOpen] = useState(false);
    if (!tag) {
      return (
        <Box
          component="a"
          href={href}
          target="_blank"
          rel="noreferrer"
          onClick={(e) => e.stopPropagation()}
          sx={{ color: "primary.main", fontWeight: 500 }}
        >
          {children}
        </Box>
      );
    }
    const refItem = byRef.get(tag);
    return (
      <>
        <RefTagButton
          tag={tag}
          refItem={refItem}
          open={open}
          onToggle={() => setOpen((v) => !v)}
        />
        {open && <RefExpandBox tag={tag} refItem={refItem} />}
      </>
    );
  };
  return { a: CiteLink };
}

export default function AiQaRefBox({
  text,
  refs,
  sx,
}: {
  /** The passage with inline citation brackets. */
  text: string;
  /** The Q&A's resolved refs (llm.llm_qa_refs) keyed by their ref tag. */
  refs: AiQaRefItem[];
  /** Caller styling (font size / color — whitespace is the markdown
   *  parse's job now, no pre-wrap). */
  sx?: SxProps<Theme>;
}) {
  const byRef = useMemo(() => new Map(refs.map((r) => [r.ref, r])), [refs]);
  const source = useMemo(() => toCiteMarkdown(text), [text]);
  const citeComponents = useMemo(() => makeCiteComponents(byRef), [byRef]);
  // react-markdown's default URL sanitizer strips unknown schemes (cite://
  // would arrive as href="" and the tags would fall through to plain
  // links) — allow our cite:// hrefs through, keep the default for the rest.
  const citeUrlTransform = useMemo(
    () => (url: string) =>
      url.startsWith(CITE_HREF_PREFIX) ? url : null,
    [],
  );
  return (
    <Box
      sx={[
        { lineHeight: 1.65, minWidth: 0 },
        ...(Array.isArray(sx) ? sx : sx ? [sx] : []),
      ]}
    >
      <MarkdownView
        dense
        source={source}
        extraComponents={citeComponents}
        extraUrlTransform={citeUrlTransform}
      />
    </Box>
  );
}
