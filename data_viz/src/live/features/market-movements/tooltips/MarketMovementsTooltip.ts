import React from "react";
import { renderReactElement, signedPct, tooltipComponents } from "@/lib/react-tooltip-renderer";
import { UP_COLOR, DOWN_COLOR } from "@/theme/chart-palette";
import type { IntradayMovementsResponse } from "@shared/types";
import type { PrevDayOhlcBar } from "../prevDayOhlc";

// ---- Reusable React components -------------------------------------------

const { Row, Header, Bold } = tooltipComponents;

function MutedRow({ children }: { children?: React.ReactNode }) {
  return React.createElement(Row, { style: { opacity: 0.85, marginTop: 2 } }, children);
}

function LabelRow({ children }: { children?: React.ReactNode }) {
  return React.createElement(Row, { style: { opacity: 0.6, fontSize: 10 } }, children);
}

function Dot({ color }: { color: string }) {
  return React.createElement("span", { style: { color } }, "●");
}

function SelectedCard({ children }: { children?: React.ReactNode }) {
  return React.createElement(
    "div",
    {
      style: {
        marginTop: 4,
        padding: "2px 4px",
        background: "rgba(255,255,255,0.05)",
        borderRadius: 3,
      },
    },
    children,
  );
}

// ---- Tooltip data types --------------------------------------------------

interface IndInfo {
  label: string;
  pctByTime: Map<string, number | null>;
  diffByTime: Map<string, number | null>;
}

interface MemberLookupEntry {
  name: string;
  pctByTime: Map<string, number | null>;
  diffByTime: Map<string, number | null>;
}

export interface TooltipRenderOptions {
  params: unknown;
  times: string[];
  offset: number;
  benchPct: Array<number | null>;
  noBenchmark: boolean;
  benchmarkLabel: string;
  data: IntradayMovementsResponse;
  prevDayBar: PrevDayOhlcBar | null;
  selectedMemberCode: string | null;
  selectedIndustryId: string | null;
  memberLookup: Map<string, MemberLookupEntry>;
  indPctByTime: Map<string, IndInfo>;
}

// ---- Helpers -------------------------------------------------------------

function pctRel(v: number, openPct: number, decimals = 2): string {
  const rel = (1 + v) / (1 + openPct) - 1;
  return (rel >= 0 ? "+" : "") + (rel * 100).toFixed(decimals) + "%";
}

// ---- Prev-day OHLC tooltip -----------------------------------------------

function PrevDayOhlcTooltip({ bar }: { bar: PrevDayOhlcBar }) {
  const color = bar.upDay ? UP_COLOR : DOWN_COLOR;
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(Header, null, `${bar.label} · prev day ${bar.date}`),
    React.createElement(MutedRow, null, "OHLC vs prev open"),
    React.createElement(Row, null, "O: ", React.createElement(Bold, { style: { color } }, pctRel(0, bar.openPct))),
    React.createElement(Row, null, "H: ", React.createElement(Bold, { style: { color } }, pctRel(bar.highPct, bar.openPct))),
    React.createElement(Row, null, "L: ", React.createElement(Bold, { style: { color } }, pctRel(bar.lowPct, bar.openPct))),
    React.createElement(Row, null, "C: ", React.createElement(Bold, { style: { color } }, pctRel(bar.closePct, bar.openPct))),
  );
}

// ---- Selected block components -------------------------------------------

function SelectedIndexBlock({
  name,
  code,
  pct,
  diff,
  benchmarkPct,
}: {
  name: string;
  code: string;
  pct: number;
  diff: number | null;
  benchmarkPct: number | null;
}) {
  const color = pct > (benchmarkPct ?? 0) ? UP_COLOR : DOWN_COLOR;
  return React.createElement(
    SelectedCard,
    null,
    React.createElement(LabelRow, null, "● Selected Index"),
    React.createElement(
      Row,
      { style: { marginTop: 0 } },
      React.createElement("span", { style: { color } }, `${name} (${code})`),
      ": ",
      React.createElement(Bold, { style: { color } }, signedPct(pct)),
      diff != null ? React.createElement("span", { style: { opacity: 0.7 } }, `(${signedPct(diff)})`) : null,
    ),
  );
}

function SelectedIndustryBlock({
  name,
  pct,
  diff,
  benchmarkPct,
}: {
  name: string;
  pct: number;
  diff: number | null;
  benchmarkPct: number | null;
}) {
  const color = pct > (benchmarkPct ?? 0) ? UP_COLOR : DOWN_COLOR;
  return React.createElement(
    SelectedCard,
    null,
    React.createElement(LabelRow, null, "● Selected Industry"),
    React.createElement(
      Row,
      { style: { marginTop: 0 } },
      React.createElement("span", { style: { color } }, name),
      ": ",
      React.createElement(Bold, { style: { color } }, signedPct(pct)),
      diff != null ? React.createElement("span", { style: { opacity: 0.7 } }, `(${signedPct(diff)})`) : null,
    ),
  );
}

function SelectedIndexBlockNoBench({
  name,
  code,
  pct,
}: {
  name: string;
  code: string;
  pct: number;
}) {
  const color = pct >= 0 ? UP_COLOR : DOWN_COLOR;
  return React.createElement(
    SelectedCard,
    null,
    React.createElement(LabelRow, null, "● Selected Index"),
    React.createElement(
      Row,
      { style: { marginTop: 0 } },
      React.createElement("span", { style: { color } }, `${name} (${code})`),
      ": ",
      React.createElement(Bold, { style: { color } }, signedPct(pct)),
    ),
  );
}

function SelectedIndustryBlockNoBench({
  name,
  pct,
}: {
  name: string;
  pct: number;
}) {
  const color = pct >= 0 ? UP_COLOR : DOWN_COLOR;
  return React.createElement(
    SelectedCard,
    null,
    React.createElement(LabelRow, null, "● Selected Industry"),
    React.createElement(
      Row,
      { style: { marginTop: 0 } },
      React.createElement("span", { style: { color } }, name),
      ": ",
      React.createElement(Bold, { style: { color } }, signedPct(pct)),
    ),
  );
}

// ---- Main formatter ------------------------------------------------------

export function renderTooltip(opts: TooltipRenderOptions): string {
  const {
    params,
    times,
    offset,
    benchPct,
    noBenchmark,
    benchmarkLabel,
    data,
    prevDayBar,
    selectedMemberCode,
    selectedIndustryId,
    memberLookup,
    indPctByTime,
  } = opts;

  const arr = (Array.isArray(params) ? params : [params]) as Array<{
    dataIndex?: number;
  }>;
  if (arr.length === 0) return "";
  const idx = arr[0].dataIndex ?? 0;

  if (offset > 0 && idx === 0) {
    return prevDayBar ? renderReactElement(React.createElement(PrevDayOhlcTooltip, { bar: prevDayBar })) : "";
  }

  const tick = times[idx - offset] ?? "";
  const bv = benchPct[idx];

  const children: React.ReactNode[] = [];

  if (noBenchmark) {
    children.push(React.createElement(Header, null, benchmarkLabel));
    children.push(React.createElement(MutedRow, null, `tick ${tick} · baseline 0.0%`));

    if (selectedMemberCode) {
      const entry = memberLookup.get(selectedMemberCode);
      const mp = entry?.pctByTime.get(tick);
      if (mp != null && entry) {
        children.push(React.createElement(SelectedIndexBlockNoBench, { name: entry.name, code: selectedMemberCode, pct: mp }));
      }
    } else if (selectedIndustryId) {
      const info = indPctByTime.get(selectedIndustryId);
      const ip = info?.pctByTime.get(tick);
      if (ip != null && info) {
        children.push(React.createElement(SelectedIndustryBlockNoBench, { name: info.label, pct: ip }));
      }
    }

    const rows: Array<{ label: string; pct: number }> = [];
    for (const [, info] of indPctByTime) {
      const iv = info.pctByTime.get(tick);
      if (iv == null) continue;
      rows.push({ label: info.label, pct: iv });
    }
    rows.sort((a, b) => b.pct - a.pct);

    for (const r of rows) {
      const color = r.pct >= 0 ? UP_COLOR : DOWN_COLOR;
      children.push(
        React.createElement(Row, null,
          React.createElement(Dot, { color }),
          ` ${r.label}: `,
          React.createElement(Bold, { style: { color } }, signedPct(r.pct)),
        ),
      );
    }
    return renderReactElement(React.createElement(React.Fragment, null, children));
  }

  // Normal mode
  const benchPctStr = bv == null ? "—" : signedPct(bv);
  children.push(React.createElement(Header, null, `${data.benchmark_name} (${data.benchmark_code})`));
  children.push(React.createElement(MutedRow, null, `tick ${tick} · bench ${benchPctStr}`));

  if (selectedMemberCode) {
    const entry = memberLookup.get(selectedMemberCode);
    const mp = entry?.pctByTime.get(tick);
    const md = entry?.diffByTime.get(tick);
    if (mp != null && entry) {
      children.push(React.createElement(SelectedIndexBlock, {
        name: entry.name, code: selectedMemberCode, pct: mp, diff: md ?? null, benchmarkPct: bv,
      }));
    }
  } else if (selectedIndustryId) {
    const info = indPctByTime.get(selectedIndustryId);
    const ip = info?.pctByTime.get(tick);
    const idf = info?.diffByTime.get(tick);
    if (ip != null && info) {
      children.push(React.createElement(SelectedIndustryBlock, {
        name: info.label, pct: ip, diff: idf ?? null, benchmarkPct: bv,
      }));
    }
  }

  const rows: Array<{ label: string; pct: number; diff: number }> = [];
  for (const [, info] of indPctByTime) {
    const iv = info.pctByTime.get(tick);
    const dv = info.diffByTime.get(tick);
    if (iv == null || dv == null) continue;
    rows.push({ label: info.label, pct: iv, diff: dv });
  }
  rows.sort((a, b) => b.diff - a.diff);
  const top5 = rows.slice(0, 5);
  const bottom5 = rows.slice(-5).reverse();
  const shown = new Set<string>();

  const renderTop5Row = (r: { label: string; pct: number; diff: number }) => {
    if (shown.has(r.label)) return null;
    shown.add(r.label);
    const arrow = r.diff >= 0 ? "▲" : "▼";
    const diffColor = r.diff >= 0 ? UP_COLOR : DOWN_COLOR;
    return React.createElement(Row, null,
      React.createElement("span", { style: { color: diffColor } }, arrow),
      ` ${r.label}: `,
      React.createElement(Bold, null, signedPct(r.pct)),
      " ",
      React.createElement("span", { style: { opacity: 0.7 } }, `(${signedPct(r.diff)})`),
    );
  };

  if (top5.length > 0) {
    children.push(React.createElement(Row, { style: { marginTop: 3, opacity: 0.6, fontSize: 10 } }, "▲ Top 5"));
    for (const r of top5) {
      const el = renderTop5Row(r);
      if (el) children.push(el);
    }
  }
  if (bottom5.length > 0 && bottom5[0].diff < 0) {
    children.push(React.createElement(Row, { style: { marginTop: 3, opacity: 0.6, fontSize: 10 } }, "▼ Bottom 5"));
    for (const r of bottom5) {
      const el = renderTop5Row(r);
      if (el) children.push(el);
    }
  }

  return renderReactElement(React.createElement(React.Fragment, null, children));
}
