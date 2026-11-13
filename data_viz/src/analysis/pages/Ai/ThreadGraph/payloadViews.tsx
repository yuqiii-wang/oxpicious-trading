/** The payload reading views of the ThreadGraph rows — one labeled
 *  input/output block, the tool-call argument body, and the chat
 *  message laid out for reading. */
import { memo, useState } from "react";
import { Box, Button, Typography } from "@mui/material";
import MarkdownView from "@/shared/markdown/MarkdownView";
import { LONG_CONTENT_CHARS } from "./format";

/** One input/output block of an expanded task row: the context
 *  payload's text — a single {"note"|"answer"|"error": string} payload
 *  renders as the bare string (the tick notes / subagent results),
 *  anything else as pretty JSON. `scroll` caps the height (the plan
 *  JSON can run long) instead of stretching the panel. */
export function ContextBlock({
  label,
  payload,
  scroll = false,
}: {
  label: string;
  payload: unknown;
  scroll?: boolean;
}) {
  let text: string;
  if (payload !== null && typeof payload === "object" && !Array.isArray(payload)) {
    const entries = Object.entries(payload as Record<string, unknown>).filter(
      ([, v]) => v !== null && v !== undefined,
    );
    text =
      entries.length === 1 && typeof entries[0][1] === "string"
        ? entries[0][1]
        : JSON.stringify(payload, null, 2);
  } else {
    text = JSON.stringify(payload, null, 2);
  }
  return (
    <Box sx={{ borderLeft: 2, borderColor: "divider", pl: 1.25, py: 0.25 }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography
        variant="body2"
        sx={{
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          ...(scroll ? { maxHeight: 280, overflowY: "auto" } : {}),
        }}
      >
        {text}
      </Typography>
    </Box>
  );
}

/** Monospace pre-render shared by the tool-argument bodies (pretty JSON
 *  fallback and the extracted dominant field alike). */
const argsPreSx = {
  margin: 0,
  fontFamily: "monospace",
  fontSize: "0.75rem",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
} as const;

/** The content-view rendering of one tool call's arguments. The
 *  DOMINANT string field is the call's semantic payload — the written
 *  file's content (sandbox_write_file), the SQL (db_run_query), the
 *  delegated goal (spawn_subagent), the tick note (update_plan_step) —
 *  so it becomes the body (real newlines, scroll-capped), and the
 *  remaining short fields compress to a `k=v` meta line. Only when no
 *  string field dominates (small flat args) does the whole pretty JSON
 *  show. */
export function ToolCallArgs({ argsText }: { argsText: string }) {
  const body = (() => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(argsText);
    } catch {
      return null;
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return null;
    }
    const entries = Object.entries(parsed as Record<string, unknown>);
    let longest: [string, string] | null = null;
    for (const [key, value] of entries) {
      if (typeof value === "string" && (longest === null || value.length > longest[1].length)) {
        longest = [key, value];
      }
    }
    if (longest === null || longest[1].length < 80) return null;
    const restLine = entries
      .filter(([key]) => key !== longest![0])
      .map(([key, value]) => {
        const s = typeof value === "string" ? value : JSON.stringify(value) ?? "";
        return `${key}=${s.length > 60 ? `${s.slice(0, 57)}…` : s}`;
      })
      .join(" · ");
    return { restLine, text: longest[1] };
  })();
  if (body === null) {
    return (
      <Typography variant="body2" component="pre" sx={argsPreSx}>
        {argsText}
      </Typography>
    );
  }
  return (
    <>
      {body.restLine !== "" && (
        <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
          {body.restLine}
        </Typography>
      )}
      <Typography
        variant="body2"
        component="pre"
        sx={{ ...argsPreSx, maxHeight: 280, overflowY: "auto", mt: body.restLine ? 0.25 : 0 }}
      >
        {body.text}
      </Typography>
    </>
  );
}

/** One chat message of a request/response payload, laid out for
 *  reading instead of a raw JSON dump: a role line (plus the tool
 *  identity on tool results) over the content — string content renders
 *  as MARKDOWN (the shared MarkdownView at dense scale, matching how
 *  the Result view renders the model's documents); assistant tool_calls
 *  via :class:`ToolCallArgs` — the semantic argument field as the body.
 *  Anything that is not a role-shaped object falls back to pretty
 *  JSON. Memoized: a request row's messages are stable objects from the
 *  fetched payload, so poll-driven parent re-renders skip the (expensive)
 *  markdown re-parse entirely. */
export const ChatMessage = memo(function ChatMessage({ message }: { message: unknown }) {
  // Huge bodies (system prompts, guarded data blocks, tool-result texts)
  // render truncated — parsing and mounting tens of KB of markdown per
  // message is what freezes the panel; the full text is one click away.
  // Declared before the early return (hooks order).
  const [fullText, setFullText] = useState(false);
  if (message === null || typeof message !== "object") {
    return <ContextBlock label="message" payload={message} />;
  }
  const msg = message as Record<string, unknown>;
  const role = typeof msg.role === "string" ? msg.role : "message";
  const toolCallId =
    typeof msg.tool_call_id === "string" ? msg.tool_call_id : null;
  const contentIsText = typeof msg.content === "string";
  const content =
    typeof msg.content === "string"
      ? msg.content
      : msg.content === null || msg.content === undefined
        ? null
        : JSON.stringify(msg.content, null, 2);
  const toolCalls = Array.isArray(msg.tool_calls) ? msg.tool_calls : [];
  const isLong = content !== null && content.length > LONG_CONTENT_CHARS;
  const shown = isLong && !fullText ? content.slice(0, LONG_CONTENT_CHARS) : content;
  return (
    <Box sx={{ borderLeft: 2, borderColor: "divider", pl: 1.25, py: 0.25 }}>
      <Typography variant="caption" color="text.secondary" sx={{ display: "block" }}>
        {role}
        {toolCallId ? ` · ${toolCallId}` : ""}
        {typeof msg.name === "string" ? ` · ${msg.name}` : ""}
      </Typography>
      {shown !== null && shown.trim() !== "" && (
        contentIsText ? (
          <MarkdownView source={shown} dense />
        ) : (
          <Typography
            variant="body2"
            sx={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
          >
            {shown}
          </Typography>
        )
      )}
      {isLong && (
        <Button
          size="small"
          variant="text"
          sx={{ px: 0.5, py: 0, fontSize: "0.7rem", textTransform: "none" }}
          onClick={() => setFullText((v) => !v)}
        >
          {fullText
            ? "show less"
            : `show full text (+${(content!.length - LONG_CONTENT_CHARS).toLocaleString()} chars)`}
        </Button>
      )}
      {toolCalls.map((call, i) => {
        const fn = ((call as Record<string, unknown>)?.function ??
          {}) as Record<string, unknown>;
        const argsText = (() => {
          const raw = String(fn.arguments ?? "");
          try {
            return JSON.stringify(JSON.parse(raw), null, 2);
          } catch {
            return raw;
          }
        })();
        return (
          <Box key={i} sx={{ mt: 0.5 }}>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block" }}
            >
              tool call · {String(fn.name ?? "?")}
            </Typography>
            <ToolCallArgs argsText={argsText} />
          </Box>
        );
      })}
      {content === null && toolCalls.length === 0 && (
        <Typography
          variant="body2"
          component="pre"
          sx={{
            margin: 0,
            fontFamily: "monospace",
            fontSize: "0.75rem",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {JSON.stringify(msg, null, 2)}
        </Typography>
      )}
    </Box>
  );
});
