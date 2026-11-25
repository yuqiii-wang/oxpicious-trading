/**
 * PseudoImagea — the 伪图片 (Pseudo Imagea) panel + the ````ocr fence
 * splitter.
 *
 * builds.text embeds recovered image content into news item/comment
 * content as 4-backtick fenced blocks whose info string keeps the
 * source image URL plus a KIND token. Three block shapes:
 *
 * plain:  ````ocr <url>\n<transcription>\n````
 * layout: ````ocr <url> <W>x<H>\n[x1,y1,x2,y2,"text"] per line\n````
 *         (text-screenshot spotting — the panel reconstructs the
 *          screenshot on an aspect-ratio canvas: absolute positions +
 *          container-query proportional font sizes)
 * chart:  ````ocr <url> chart\n<comprehension JSON>\n````
 *         (stats-chart comprehension — the panel renders a ROUGH chart
 *          of the critical points + the summary, via PseudoImageaChart)
 *
 * The caption toggles per kind (还原布局 ⇄ 纯文本 / 图表 ⇄ 文本); plain
 * blocks render as plain text only. All shapes are clearly a different
 * visual family from the quote blockquote (dashed border + muted
 * background vs the solid primary left border).
 */
import { useMemo, useState } from "react";
import { Box, Stack, Typography } from "@mui/material";
import { ImageOutlined as ImageIcon } from "@mui/icons-material";
import PseudoImageaChart from "./PseudoImageaChart";
import type { PseudoImageChart } from "./pseudoImageaChartOption";

export interface PseudoDims {
  w: number;
  h: number;
}

export interface PseudoImageElement {
  bbox: [number, number, number, number]; // x1, y1, x2, y2 — original px
  text: string;
}

export interface PseudoImageChunk {
  kind: "text" | "pseudo";
  text: string;
  /** pseudo only — the source image URL from the fence info string. */
  url?: string;
  /** layout blocks — the dims token's WxH (the coordinate space). */
  dims?: PseudoDims;
  /** chart blocks — the info token marker (the body is JSON). */
  chartKind?: boolean;
}

const PSEUDO_FENCE_RE = /````ocr(?:[ \t]+([^\n]*))?[ \t]*\n([\s\S]*?)(?:\n````|$)/g;
const DIMS_RE = /^(\d+)x(\d+)$/;

/** Split content into text chunks (caller renders as before) and pseudo
 *  chunks. An unterminated fence (truncated write) still renders as a
 *  pseudo block consuming the tail. */
export function splitPseudoImageBlocks(content: string): PseudoImageChunk[] {
  const chunks: PseudoImageChunk[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  PSEUDO_FENCE_RE.lastIndex = 0;
  while ((match = PSEUDO_FENCE_RE.exec(content)) !== null) {
    if (match.index > last) {
      chunks.push({ kind: "text", text: content.slice(last, match.index) });
    }
    const info = match[1]?.trim();
    let url: string | undefined;
    let dims: PseudoDims | undefined;
    let chartKind = false;
    if (info) {
      const tokens = info.split(/\s+/);
      const lastToken = tokens[tokens.length - 1];
      const dimsMatch = lastToken?.match(DIMS_RE);
      if (dimsMatch) {
        dims = { w: Number(dimsMatch[1]), h: Number(dimsMatch[2]) };
        tokens.pop();
      } else if (lastToken === "chart") {
        chartKind = true;
        tokens.pop();
      }
      url = tokens.join(" ") || undefined;
    }
    chunks.push({ kind: "pseudo", url, dims, chartKind, text: match[2] ?? "" });
    last = PSEUDO_FENCE_RE.lastIndex;
  }
  if (last < content.length) {
    chunks.push({ kind: "text", text: content.slice(last) });
  }
  return chunks.filter((c) => c.kind === "text" || c.text.length > 0);
}

/** The fence body → spotted lines, or null when it is not a valid
 *  layout body (plain transcription → the plain view). */
function parseLayoutElements(body: string): PseudoImageElement[] | null {
  const elements: PseudoImageElement[] = [];
  for (const line of body.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      return null;
    }
    if (!Array.isArray(parsed) || parsed.length !== 5) return null;
    const [x1, y1, x2, y2, text] = parsed as unknown[];
    if (
      ![x1, y1, x2, y2].every((v) => typeof v === "number" && Number.isFinite(v)) ||
      typeof text !== "string" ||
      !text.trim()
    ) {
      return null;
    }
    elements.push({ bbox: [x1 as number, y1 as number, x2 as number, y2 as number], text });
  }
  return elements.length > 0 ? elements : null;
}

/** The chart block's JSON body → the comprehension, or null (the body
 *  then renders as-is — a malformed write stays visible). */
function parseChartComprehension(body: string): PseudoImageChart | null {
  try {
    const parsed = JSON.parse(body) as Partial<PseudoImageChart> | null;
    if (!parsed || typeof parsed.summary !== "string" || !parsed.summary.trim()) {
      return null;
    }
    const points = Array.isArray(parsed.points)
      ? parsed.points.filter(
          (p) =>
            p != null &&
            typeof p.label === "string" &&
            typeof p.value === "number" &&
            Number.isFinite(p.value),
        )
      : [];
    return {
      kind: typeof parsed.kind === "string" ? parsed.kind : "mixed",
      title: typeof parsed.title === "string" ? parsed.title : null,
      summary: parsed.summary,
      points,
    };
  } catch {
    return null;
  }
}

/** The recovered canvas never exceeds this height; the width shrinks
 *  with the aspect ratio so the coordinate mapping stays honest. */
const CANVAS_MAX_HEIGHT_PX = 480;

type PseudoView = "layout" | "chart" | "plain" | "text";

export default function PseudoImagea({ chunk }: { chunk: PseudoImageChunk }) {
  const elements = useMemo(
    () => (chunk.dims && !chunk.chartKind ? parseLayoutElements(chunk.text) : null),
    [chunk.dims, chunk.chartKind, chunk.text],
  );
  const chart = useMemo(
    () =>
      chunk.chartKind
        ? parseChartComprehension(chunk.text)
        : null,
    [chunk.chartKind, chunk.text],
  );
  const chartViewable = chart != null && chart.points.length > 0;
  const [view, setView] = useState<PseudoView>(
    chartViewable ? "chart" : elements ? "layout" : "plain",
  );
  const showLayout = view === "layout" && elements != null && chunk.dims != null;
  const showChart = view === "chart" && chartViewable && chart != null;
  // A layout block's fence body is JSON lines — the text view renders
  // the readable transcription (the elements' texts), never the JSON.
  const layoutText =
    elements != null ? elements.map((el) => el.text).join("\n") : chunk.text;
  const canvasWidth = chunk.dims
    ? `min(100%, ${Math.round((CANVAS_MAX_HEIGHT_PX * chunk.dims.w) / chunk.dims.h)}px)`
    : undefined;

  return (
    <Box
      sx={{
        my: 0.75,
        border: "1.5px dashed",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "action.hover",
        overflow: "hidden",
      }}
    >
      <Stack
        direction="row"
        spacing={0.5}
        sx={{ alignItems: "center", px: 1, py: 0.4, borderBottom: "1px dashed", borderColor: "divider" }}
      >
        <ImageIcon sx={{ fontSize: 14, color: "text.secondary" }} />
        <Typography variant="caption" sx={{ color: "text.secondary", fontWeight: 600 }}>
          伪图片 (Pseudo Imagea)
        </Typography>
        <Stack direction="row" spacing={0.25} sx={{ ml: "auto", alignItems: "center" }}>
          {elements && chunk.dims && (
            <>
              <PseudoViewLink
                label="还原布局"
                active={showLayout}
                onClick={() => setView("layout")}
              />
              <PseudoViewLink
                label="纯文本"
                active={!showLayout}
                onClick={() => setView("plain")}
              />
            </>
          )}
          {chartViewable && (
            <>
              <PseudoViewLink
                label="图表"
                active={showChart}
                onClick={() => setView("chart")}
              />
              <PseudoViewLink
                label="文本"
                active={!showChart}
                onClick={() => setView("text")}
              />
            </>
          )}
          {chunk.url && (
            <Typography
              component="a"
              href={chunk.url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
              sx={{ fontSize: "0.65rem", color: "text.secondary", "&:hover": { color: "primary.main" } }}
            >
              原图
            </Typography>
          )}
        </Stack>
      </Stack>
      {showLayout && elements && chunk.dims ? (
        <Box sx={{ px: 1, py: 1, display: "flex", justifyContent: "center" }}>
          <Box
            sx={{
              position: "relative",
              aspectRatio: `${chunk.dims.w} / ${chunk.dims.h}`,
              width: canvasWidth,
              containerType: "size",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: 0.5,
              bgcolor: "background.default",
              overflow: "hidden",
            }}
          >
            {elements.map((el, i) => {
              const [x1, y1, , y2] = el.bbox;
              // 0.88 trims the font metrics drift — our rendered glyphs
              // run slightly wider than the screenshot's, and an exact
              // box-height font clips lines that touch the right edge.
              const fontSizeCqh = Math.max(
                ((y2 - y1) / chunk.dims!.h) * 100 * 0.88, 1.2);
              return (
                <Box
                  key={i}
                  component="span"
                  sx={{
                    position: "absolute",
                    left: `${(x1 / chunk.dims!.w) * 100}%`,
                    top: `${(y1 / chunk.dims!.h) * 100}%`,
                    fontSize: `${fontSizeCqh}cqh`,
                    lineHeight: 1.05,
                    whiteSpace: "nowrap",
                  }}
                >
                  {el.text}
                </Box>
              );
            })}
          </Box>
        </Box>
      ) : showChart && chart ? (
        <Box sx={{ px: 0.5, pb: 0.5 }}>
          <Typography
            variant="caption"
            component="div"
            sx={{ px: 0.75, pt: 0.5, color: "text.secondary", lineHeight: 1.5 }}
          >
            {chart.summary}
          </Typography>
          <PseudoImageaChart chart={chart} />
        </Box>
      ) : (
        <Typography
          component="div"
          sx={{ px: 1, py: 0.75, fontSize: "0.72rem", lineHeight: 1.55, whiteSpace: "pre-wrap" }}
        >
          {elements != null ? layoutText : chunk.text}
        </Typography>
      )}
    </Box>
  );
}

function PseudoViewLink({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <Typography
      component="a"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      sx={{
        fontSize: "0.65rem",
        cursor: "pointer",
        px: 0.25,
        color: active ? "primary.main" : "text.secondary",
        fontWeight: active ? 700 : 400,
        "&:hover": { color: "primary.main" },
      }}
    >
      {label}
    </Typography>
  );
}
