/**
 * Shared constants for the Volatility page sub-modules.
 */

/**
 * Page size — number of code card groups shown per page. Kept to 1
 * because each group renders four full-height vol charts + a snapshot
 * card.
 */
export const PAGE_SIZE = 1;

/**
 * The echarts.connect group prefix per code — the four vol charts in a
 * code's merged vol box join one group so the FIRST chart's dataZoom
 * time slider + hover sync across them (the other three charts carry no
 * slider of their own; the CrowdednessCodeGroup pattern).
 */
export const VOL_CHART_GROUP_PREFIX = "vol-stats";
