/**
 * Shared React-tooltip primitives for the MaSpread formatters — the
 * styled Row/Header rows every button group's tooltipRows composes with.
 *
 * Uses React.createElement + a custom element-to-HTML renderer (see
 * renderReactElement) to produce HTML strings for ECharts tooltips,
 * giving us proper React syntax (elements, components, conditional
 * rendering) while still being compatible with ECharts' `formatter`
 * API — without requiring react-dom/server (SSR-only).
 */
import React from "react";

/** A styled row with optional left padding and opacity. */
export function Row({
  children,
  style,
}: {
  children?: React.ReactNode;
  style?: React.CSSProperties;
}) {
  const defaultStyle: React.CSSProperties = { marginBottom: "2px" };
  return React.createElement("div", {
    style: { ...defaultStyle, ...style },
    children,
  });
}

/** A bold header row. */
export function Header({ children }: { children?: React.ReactNode }) {
  return React.createElement(Row, {
    style: { fontWeight: 600, marginBottom: "2px" },
    children,
  });
}
