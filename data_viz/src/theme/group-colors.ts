/**
 * Group color scheme — assigns ONE MAJOR COLOR per distinct group and
 * generates VARIANT SHADES within a group for individual curves.
 *
 * Motivation: when several curves belong to the same logical group (e.g.
 * multiple member indices of one industry, or several ETFs tracking indices
 * of one industry), they should share a hue so the user can visually pair
 * them; curves of a DIFFERENT group use a different major color.
 *
 *   Industry A (BANKS)  → red major:  idx1=red, idx2=lighter red, idx3=darker red
 *   Industry B (AI)      → blue major: idx1=blue, idx2=lighter blue
 *
 * Major colors come from `GROUP_MAJOR_COLORS` (ColorBrewer Set1 — high
 * contrast, colorblind-friendly). Group → major-color assignment is STABLE:
 * distinct group keys are sorted ascending, then assigned palette indices in
 * order, so the same set of groups always maps to the same colors regardless
 * of insertion order. This keeps colors consistent across charts that build
 * independent schemes from the same group set (e.g. the price chart and the
 * aggregate chart on the Industry Sentiments page).
 *
 * Within a group, variants diverge symmetrically around the major color's
 * lightness: item 0 = the exact major color (the "anchor"), item 1 lighter,
 * item 2 darker, item 3 lighter still, … Lightness is clamped to a visible
 * band so variants stay legible. Hue and saturation are preserved, so every
 * variant is recognizably the same color family as its major.
 */
import { GROUP_MAJOR_COLORS } from "./chart-palette";

// ----------------------------------------------------------------------------
// hex ↔ HSL helpers (minimal, allocation-free)
// ----------------------------------------------------------------------------
function hexToRgb(hex: string): [number, number, number] {
  let h = hex.replace("#", "").trim();
  if (h.length === 3) {
    h = h
      .split("")
      .map((c) => c + c)
      .join("");
  }
  const num = parseInt(h, 16);
  if (Number.isNaN(num)) return [0, 0, 0];
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255];
}

function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  let h = 0;
  let s = 0;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    if (max === rn) {
      h = (gn - bn) / d + (gn < bn ? 6 : 0);
    } else if (max === gn) {
      h = (bn - rn) / d + 2;
    } else {
      h = (rn - gn) / d + 4;
    }
    h /= 6;
  }
  return [h * 360, s, l];
}

function hslToHex(h: number, s: number, l: number): string {
  const hn = h / 360;
  const hue2rgb = (p: number, q: number, t: number): number => {
    let tt = t;
    if (tt < 0) tt += 1;
    if (tt > 1) tt -= 1;
    if (tt < 1 / 6) return p + (q - p) * 6 * tt;
    if (tt < 1 / 2) return q;
    if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
    return p;
  };
  let r: number;
  let g: number;
  let b: number;
  if (s === 0) {
    r = g = b = l;
  } else {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue2rgb(p, q, hn + 1 / 3);
    g = hue2rgb(p, q, hn);
    b = hue2rgb(p, q, hn - 1 / 3);
  }
  const toHex = (x: number): string =>
    Math.round(x * 255)
      .toString(16)
      .padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

// ----------------------------------------------------------------------------
// Variant generation
// ----------------------------------------------------------------------------
/** Lightness step per variant (fraction of full scale 0..1). */
const VARIANT_STEP = 0.1;
/** Clamp band so variants never become invisible (too dark) or washed out. */
const MIN_L = 0.18;
const MAX_L = 0.82;

/**
 * Return the `indexInGroup`-th variant shade of `majorHex`.
 *
 * `indexInGroup === 0` returns the major color unchanged (the group's anchor
 * curve). Subsequent indices diverge symmetrically around the major's
 * lightness: 1 → lighter, 2 → darker, 3 → lighter still, 4 → darker still…
 * Hue and saturation are preserved, so every variant stays in the same color
 * family as the major.
 */
export function variantColorOf(majorHex: string, indexInGroup: number): string {
  if (indexInGroup <= 0) return majorHex;
  const [r, g, b] = hexToRgb(majorHex);
  const [h, s, l] = rgbToHsl(r, g, b);
  // item1 → +1·step, item2 → -1·step, item3 → +2·step, item4 → -2·step, …
  const k = Math.ceil(indexInGroup / 2);
  const sign = indexInGroup % 2 === 1 ? 1 : -1;
  const newL = Math.max(MIN_L, Math.min(MAX_L, l + sign * k * VARIANT_STEP));
  return hslToHex(h, s, newL);
}

// ----------------------------------------------------------------------------
// Scheme builder
// ----------------------------------------------------------------------------
export interface GroupColorScheme {
  /** Distinct group keys, sorted ascending (stable assignment order). */
  readonly groupKeys: readonly string[];
  /** Major color for a group key. Falls back to the first major if unknown. */
  majorColor(groupKey: string): string;
  /** Variant color for the `indexInGroup`-th curve within a group. */
  variantColor(groupKey: string, indexInGroup: number): string;
}

/**
 * Build a stable group-color scheme from the list of group keys.
 *
 * Distinct keys are sorted ascending, then assigned major colors from
 * `GROUP_MAJOR_COLORS` in order (cycling when there are more groups than
 * palette entries). Because assignment is keyed off the SORTED distinct set,
 * two schemes built from the same group set always agree on colors — even
 * if the keys were supplied in different orders.
 */
export function buildGroupColorScheme(
  groupKeys: readonly string[],
): GroupColorScheme {
  const distinct = Array.from(new Set(groupKeys)).sort();
  const major = new Map<string, string>();
  distinct.forEach((k, i) => {
    major.set(k, GROUP_MAJOR_COLORS[i % GROUP_MAJOR_COLORS.length]);
  });
  const fallback = GROUP_MAJOR_COLORS[0];
  return {
    groupKeys: distinct,
    majorColor: (g) => major.get(g) ?? fallback,
    variantColor: (g, i) => variantColorOf(major.get(g) ?? fallback, i),
  };
}

/**
 * Build per-item colors from a flat item list, grouping by `getGroupKey`.
 *
 * Returns `colors[i]` aligned to `items[i]`, where each color is the variant
 * shade of its group's major color based on the item's position within its
 * group (counted in first-appearance order across `items`). Also returns the
 * underlying `scheme` so callers can look up major colors directly (e.g. for
 * one-curve-per-group overlays like per-industry mean lines).
 */
export function buildItemGroupColors<T>(
  items: readonly T[],
  getGroupKey: (item: T) => string,
): { scheme: GroupColorScheme; colors: string[] } {
  const counters = new Map<string, number>();
  const indexInGroup: number[] = new Array(items.length).fill(0);
  const groupKeys: string[] = [];
  for (let i = 0; i < items.length; i++) {
    const k = getGroupKey(items[i]);
    const idx = counters.get(k) ?? 0;
    indexInGroup[i] = idx;
    counters.set(k, idx + 1);
    if (idx === 0) groupKeys.push(k);
  }
  const scheme = buildGroupColorScheme(groupKeys);
  const colors = items.map((it, i) =>
    scheme.variantColor(getGroupKey(it), indexInGroup[i]),
  );
  return { scheme, colors };
}
