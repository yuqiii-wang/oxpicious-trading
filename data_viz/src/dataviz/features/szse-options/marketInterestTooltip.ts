/**
 * Market Interest Wall tooltip formatter — React-based tooltip for the OI wall.
 */
import React from "react";
import { fmtMil, fmtNum } from "@/lib/series";
import { PRICE_SCALE } from "@/theme/chart-palette";

// ---- Renderer ------------------------------------------------------------

type El = React.ReactElement | string | number | boolean | null | undefined;

function styleObjectToString(style: React.CSSProperties | undefined): string {
  if (!style) return "";
  const parts: string[] = [];
  for (const [key, val] of Object.entries(style)) {
    if (val == null) continue;
    const cssKey = key.replace(/[A-Z]/g, (m) => `-${m.toLowerCase()}`);
    if (typeof val === "number") {
      const unitless = ["opacity", "zIndex", "fontWeight", "lineHeight"];
      if (unitless.includes(key) || val === 0) parts.push(`${cssKey}:${val}`);
      else parts.push(`${cssKey}:${val}px`);
    } else {
      parts.push(`${cssKey}:${String(val)}`);
    }
  }
  return parts.join(";");
}

function renderChildren(children: React.ReactNode): string {
  if (children == null || children === false || children === true) return "";
  if (typeof children === "string") return children;
  if (typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map((c) => renderChildren(c)).join("");
  if (React.isValidElement(children)) return renderEl(children);
  return String(children);
}

function renderEl(el: El): string {
  if (el == null || el === false || el === true) return "";
  if (typeof el === "string") return el;
  if (typeof el === "number") return String(el);
  if (Array.isArray(el)) return el.map((c) => renderEl(c)).join("");
  if (!React.isValidElement(el)) return "";

  const { type, props } = el as React.ReactElement;
  if (type === React.Fragment) return renderChildren(props?.children);
  if (typeof type === "function") return renderEl((type as React.FC<Record<string, unknown>>)(props ?? {}) as El);

  const tag = String(type).toLowerCase();
  const styleStr = styleObjectToString(props?.style as React.CSSProperties | undefined);
  const classStr = props?.className ? ` class="${String(props.className)}"` : "";
  const styleAttr = styleStr ? ` style="${styleStr}"` : "";
  const childHtml = renderChildren(props?.children as React.ReactNode);
  return `<${tag}${classStr}${styleAttr}>${childHtml}</${tag}>`;
}

function render(el: React.ReactElement): string {
  return renderEl(el);
}

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

    return render(React.createElement(React.Fragment, null, children));
  };
}