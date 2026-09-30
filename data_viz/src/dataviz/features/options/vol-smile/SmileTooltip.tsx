import React from "react";
import { fmtNum, fmtPct } from "@/lib/series";
import { renderTooltip } from "./renderTooltip";
import type { SmileTooltipParam } from "./types";

function ColoredDot({ color }: { color: string }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 10,
        height: 10,
        borderRadius: "50%",
        backgroundColor: color,
        marginRight: 5,
        verticalAlign: "middle",
        border: "1px solid rgba(0,0,0,0.2)",
      }}
    />
  );
}

function SmileTooltipContent({ params }: { params: SmileTooltipParam[] }) {
  const validParams = params.filter((param) => {
    const val = param.value;
    const arr = Array.isArray(val) ? val : [val ?? 0, 0];
    return (arr[1] as number) > 0;
  });

  if (validParams.length === 0) return <React.Fragment />;

  const first = validParams[0];
  const firstArr = Array.isArray(first.value) ? first.value : [first.value ?? 0, 0];
  const moneyness = firstArr[0] as number;

  const grouped = new Map<string, { call?: SmileTooltipParam; put?: SmileTooltipParam }>();
  validParams.forEach((param) => {
    const extra = param.data;
    if (!extra) return;
    const key = `${extra.expiry}_${extra.strike}`;
    if (!grouped.has(key)) grouped.set(key, {});
    const g = grouped.get(key)!;
    if (extra.optionType === "CALL") g.call = param;
    else g.put = param;
  });

  const children: React.ReactNode[] = [
    <b key="moneyness">Moneyness: {fmtNum(moneyness)}</b>,
  ];

  let idx = 0;
  grouped.forEach((g) => {
    const strike = g.call?.data?.strike ?? g.put?.data?.strike;
    const expiry = g.call?.data?.expiry ?? g.put?.data?.expiry;
    const k = `group-${idx++}`;

    children.push(<div key={`${k}-spacer`} style={{ marginTop: "8px" }} />);
    children.push(
      <b key={`${k}-title`}>{expiry} · K={fmtNum(strike as number)}</b>,
    );

    if (g.call) {
      const arr = Array.isArray(g.call.value) ? g.call.value : [g.call.value ?? 0, 0];
      const iv = arr[1] as number;
      children.push(
        <div key={`${k}-call`}>
          {g.call.color ? <ColoredDot color={g.call.color} /> : g.call.marker ?? ""}
          <b>CALL</b>: IV={fmtPct(iv)}
        </div>,
      );
    }
    if (g.put) {
      const arr = Array.isArray(g.put.value) ? g.put.value : [g.put.value ?? 0, 0];
      const iv = arr[1] as number;
      children.push(
        <div key={`${k}-put`}>
          {g.put.color ? <ColoredDot color={g.put.color} /> : g.put.marker ?? ""}
          <b>PUT</b>: IV={fmtPct(iv)}
        </div>,
      );
    }
  });

  return <React.Fragment>{children}</React.Fragment>;
}

export function makeSmileTooltipFormatter(): (params: unknown) => string {
  return (p: unknown): string => {
    const params = (Array.isArray(p) ? p : [p]) as SmileTooltipParam[];
    return renderTooltip(<SmileTooltipContent params={params} />);
  };
}
