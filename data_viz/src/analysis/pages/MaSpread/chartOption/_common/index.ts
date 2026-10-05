/**
 * Shared plumbing for the MaSpread chart-option builders — types, the
 * React-tooltip primitives + formatting helpers, percentage-mode base
 * helpers, shared-metric reads, and the shade-overlay carrier series.
 * Button-group directories import from here; orchestrators re-export
 * the public surface through the top-level index.ts.
 */
export * from "./types";
export * from "./tooltipKit";
export * from "./tooltipFmt";
export * from "./pctBase";
export * from "./sharedMetrics";
export * from "./shadeOverlaySeries";
