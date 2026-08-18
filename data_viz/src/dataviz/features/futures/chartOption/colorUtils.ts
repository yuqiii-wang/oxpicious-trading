import {
  FUTURES_GREY_DARK,
  FUTURES_GREY_LIGHT,
} from "@/theme/chart-palette";

/**
 * Build the grey color for a matured contract at the given index.
 * Uses a power curve (exponent=0.5) so recently matured contracts stay
 * darker for longer, ensuring visible contrast even with few visible
 * contracts in a zoomed-in view.
 */
export function greyColorFor(idx: number, total: number): string {
  if (total <= 1) return FUTURES_GREY_DARK;
  const t = idx / (total - 1);
  const adjustedT = Math.pow(t, 0.5);
  return lerpColor(FUTURES_GREY_DARK, FUTURES_GREY_LIGHT, adjustedT);
}

export function lerpColor(hexA: string, hexB: string, t: number): string {
  const [r1, g1, b1] = hexToRgb(hexA);
  const [r2, g2, b2] = hexToRgb(hexB);
  const r = Math.round(r1 + (r2 - r1) * t);
  const g = Math.round(g1 + (g2 - g1) * t);
  const b = Math.round(b1 + (b2 - b1) * t);
  return `rgb(${r}, ${g}, ${b})`;
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [
    parseInt(h.substring(0, 2), 16),
    parseInt(h.substring(2, 4), 16),
    parseInt(h.substring(4, 6), 16),
  ];
}