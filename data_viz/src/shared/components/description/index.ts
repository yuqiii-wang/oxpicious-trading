/**
 * Description kit — shared rendering of authored descriptions.
 *
 *   - DescriptionView: the documentation-style body (intro paragraphs +
 *     "Series" entries + "Notes" bullets) extracted from the AI Ask
 *     modal's description page.
 *   - InfoMark: a clickable info icon that opens a description popover —
 *     the per-control "what does this mean" affordance.
 */
export { DescriptionView } from "./DescriptionView";
export type { DescriptionViewProps, DescriptionSeriesEntry } from "./DescriptionView";
export { splitParagraphs } from "./splitParagraphs";
export { InfoMark } from "./InfoMark";
export type { InfoMarkProps } from "./InfoMark";
