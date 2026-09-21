/** Authors separate paragraphs with newlines inside a description string;
 *  every non-empty trimmed line becomes its own paragraph. */
export function splitParagraphs(text: string): string[] {
  return text
    .split(/\n+/)
    .map((p) => p.trim())
    .filter(Boolean);
}
