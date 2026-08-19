import React from "react";
import { renderReactElement } from "@/lib/react-tooltip-renderer";

export function renderTooltip(element: React.ReactElement): string {
  return renderReactElement(element);
}