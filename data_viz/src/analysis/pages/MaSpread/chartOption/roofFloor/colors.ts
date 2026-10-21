/**
 * Roof/floor overlay colors — the roof/floor trendlines + anchors.
 */

// Roof/floor-window overlay colors: the roof (resistance trendline through the
// window's top + 2nd highs + its rolling-max envelope) is warm orange; the
// floor (support trendline through the top + 2nd lows + rolling-min envelope)
// is blue. Both stand apart from the green/red gap fills and gray long MA.
export const ROOF_COLOR = "#FB8C00";
export const FLOOR_COLOR = "#1E88E5";

/** Lighter variants for the LEVEL-ESTIMATE lines: the is_recently_breached
 *  state (ALL of the last 5 days beyond the d−5 line) de-emphasizes the
 *  estimate — the level has been overrun, so it draws faded. Alpha-baked
 *  rgba derived from the same hex tokens (markLine label colors go through
 *  canvas fillStyle, which ignores CSS-var strings). */
const withAlpha = (hex: string, alpha: number): string => {
  const v = parseInt(hex.slice(1), 16);
  return `rgba(${(v >> 16) & 255}, ${(v >> 8) & 255}, ${v & 255}, ${alpha})`;
};
export const ROOF_COLOR_LIGHT = withAlpha(ROOF_COLOR, 0.3);
export const FLOOR_COLOR_LIGHT = withAlpha(FLOOR_COLOR, 0.3);
