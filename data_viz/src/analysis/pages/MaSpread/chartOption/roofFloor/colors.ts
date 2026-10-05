/**
 * Roof/floor overlay colors — the roof/floor trendlines + anchors and
 * the extreme↔2nd-extreme shade zones ("shade" toggle) + their legend
 * carrier names.
 */

// Roof/floor-window overlay colors: the roof (resistance trendline through the
// window's top + 2nd highs + its rolling-max envelope) is warm orange; the
// floor (support trendline through the top + 2nd lows + rolling-min envelope)
// is blue. Both stand apart from the green/red gap fills and gray long MA.
export const ROOF_COLOR = "#FB8C00";
export const FLOOR_COLOR = "#1E88E5";

// Extreme↔2nd-extreme zone shades (the row's "shade" toggle): every
// roof/floor window fills the flat band between its two extreme anchors
// (min↔max of the DB's high/high_2nd and low/low_2nd pairs, which are
// stored in DATE order — see the shade block in shades.ts) of the window ending
// at the anchor date — light purple top (roof zone), light blue bottom
// (floor zone). Each window paints at a fixed low alpha, so OVERLAPPING
// windows stack — where several windows' zones agree the shade deepens
// (strength by confluence).
export const OHLC_SHADE_TOP_FILL = "rgba(171, 71, 188, 0.12)";
export const OHLC_SHADE_BOTTOM_FILL = "rgba(30, 136, 229, 0.12)";
// Legend names of the two per-side carrier series (rect marker in the
// accent color; toggling a side hides its windows' rects).
export const OHLC_SHADE_TOP_NAME = "Roof shade";
export const OHLC_SHADE_BOTTOM_NAME = "Floor shade";
