/**
 * plainText — markdown → plain-text digest for collapsed previews.
 *
 * The post-feed cards' collapsed 2-line snippet is a line-clamped plain
 * text box — rendering block markdown there would break the clamp, and
 * showing the raw source surfaces `#` / `**` noise. This strips the
 * syntax and keeps the words (headings/emphasis/links/code lose their
 * markers), then collapses whitespace. Not a markdown parser — a display
 * digest for snippets; the full text renders through MarkdownView.
 */

/** Strip markdown syntax from `source` down to a one-line plain text. */
export function mdPlainText(source: string): string {
  return source
    .replace(/```[\s\S]*?```/g, " ") // fenced code blocks
    .replace(/`([^`]*)`/g, "$1") // inline code → its content
    .replace(/!\[([^\]]*)\]\(([^)\s]*)\)/g, "$1") // images → alt text
    .replace(/\[([^\]]*)\]\(([^)\s]*)\)/g, "$1") // links → label text
    .replace(/^\s{0,3}#{1,6}\s+/gm, "") // heading markers
    .replace(/\*\*([^*]+)\*\*/g, "$1") // bold
    .replace(/__([^_]+)__/g, "$1") // bold (underscore)
    .replace(/\*([^*\n]+)\*/g, "$1") // italic
    .replace(/^\s{0,3}>\s?/gm, "") // blockquote markers
    .replace(/^\s{0,3}(-{3,}|\*{3,}|_{3,})\s*$/gm, " ") // horizontal rules
    .replace(/^\s{0,3}[-+*]\s+/gm, " ") // list bullets
    .replace(/\s+/g, " ") // collapse all whitespace to single spaces
    .trim();
}
