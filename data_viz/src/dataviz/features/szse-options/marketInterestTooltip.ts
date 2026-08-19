/**
 * Market Interest Wall tooltip formatter — React-based tooltip for the OI wall.
 */
import React from "react";
import { fmtMil, fmtNum } from "@/lib/series";
import { renderReactElement } from "@/lib/react-tooltip-renderer";
import { PRICE_SCALE } from "@/theme/chart-palette";

// ---- Types ---------------------------------------------------------------

interface WallTooltipParam {
  value: number;
  seriesName: string;
  marker: string;
  dataIndex?: number;
}

// ---- Formatter -----------------------------------------------------------

export function makeWallTooltipFormatter(unifiedStrikes: number[]): (params: unknown) => string {
  return (params: unknown): string => {
    const arr = (Array.isArray(params) ? params : [params]) as WallTooltipParam[];
    const strikeK = unifiedStrikes[arr[0]?.dataIndex ?? 0];
    const strikeYuan = fmtNum(strikeK / PRICE_SCALE);

    const children: React.ReactNode[] = [
      React.createElement("b", null, `K=${strikeYuan}`),
    ];

    for (const p of arr) {
      if (p.value === 0) continue;
      const oi = Math.abs(p.value);
      const oiStr = oi >= 1e6 ? fmtMil(oi) : fmtNum(oi);
      children.push(
        React.createElement("div", { style: { marginTop: "2px" } }, [
          p.marker,
          ` ${p.seriesName}: `,
          React.createElement("b", null, oiStr),
        ]),
      );
    }

    return renderReactElement(React.createElement(React.Fragment, null, children));
  };
}