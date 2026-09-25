/**
 * Shared buy/sell action style helpers — the ONE look every buy/sell
 * text uses: live trading signals, analysis Recent Movements forecast
 * rows, and the Singleton strategy decision table.
 *
 * MUI palette.success (green) for buy / palette.error (red) for sell,
 * visualized as a NO-FILL outlined chip so text never gets hidden.
 * Confidence tunes *border width + text alpha* as the intensity knob:
 * 100 → 2px solid full color, 1 → 1px still-visible tint (minimum alpha
 * = MIN_CONF_ALPHA so never too faint). Confidence is 0–100 (the live
 * signals scale); fractional 0–1 confidences are multiplied by 100 at
 * the call site.
 */
import type { Theme } from "@mui/material";

/** Minimum border/text alpha even at confidence=1 — keeps the chip
 *  clearly visible (never a ghost outline). */
export const MIN_CONF_ALPHA = 0.55;

/** Map confidence (1..100) to a border/text alpha. Linear ramp, clamped
 *  at MIN_CONF_ALPHA on the low end. */
export function confAlpha(confidence: number): number {
  const c = Math.max(1, Math.min(100, confidence));
  return MIN_CONF_ALPHA + (c / 100) * (1 - MIN_CONF_ALPHA);
}

/** Map confidence (1..100) to border width in px. Linear ramp: 1px at 1,
 *  2px at 100 — the thicker outline is the "intensity" signal when no
 *  fill is present. */
export function confBorderWidth(confidence: number): number {
  const c = Math.max(1, Math.min(100, confidence));
  return 1 + (c / 100); // 1.00 → 2.00
}

/** Convert a theme palette "main" hex (#RRGGBB) into an rgba string with
 *  the given alpha — used for confidence-tinted border + text. */
export function hexWithAlpha(hex: string, alpha: number): string {
  const m = /^#([0-9a-fA-F]{6})$/.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1]!, 16);
  const r = (n >> 16) & 0xff;
  const g = (n >> 8) & 0xff;
  const b = n & 0xff;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** The action's main hue from the theme palette — success (green) for
 *  buy, error (red) for everything else. Case-insensitive. */
export function actionColor(action: string, theme: Theme): string {
  return action.toLowerCase() === "buy"
    ? theme.palette.success.main
    : theme.palette.error.main;
}

/** Chip sx overrides — NO fill (outlined by default), so text is never
 *  hidden. Border width + border/text alpha ramp with confidence.
 *  `colorOverride` swaps the success/error hue (e.g. the Singleton
 *  last-day-sell purple) while keeping the same confidence treatment. */
export function actionChipSx(
  action: string,
  confidence: number,
  theme: Theme,
  colorOverride?: string,
): Record<string, unknown> {
  const main = colorOverride ?? actionColor(action, theme);
  const alpha = confAlpha(confidence);
  return {
    fontWeight: 600,
    backgroundColor: "transparent",
    borderColor: hexWithAlpha(main, alpha),
    borderWidth: confBorderWidth(confidence),
    borderStyle: "solid",
    color: hexWithAlpha(main, alpha),
    "&:hover": {
      backgroundColor: hexWithAlpha(main, 0.08),
      borderColor: main,
      color: main,
    },
  };
}
