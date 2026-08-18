/**
 * Shared data shaping utilities for annual-sentiment charts.
 */
import type { OptionsRow } from "@shared/types";
import type { DailyOi, ExpiryMarker, ExpiryContract } from "./types";

export function buildDailyOi(rows: OptionsRow[]): DailyOi[] {
  const byDate = new Map<string, { callOi: number; putOi: number }>();
  for (const r of rows) {
    if (!byDate.has(r.date)) byDate.set(r.date, { callOi: 0, putOi: 0 });
    const d = byDate.get(r.date)!;
    if (r.option_type === "CALL") d.callOi += r.open_interest;
    else d.putOi += r.open_interest;
  }
  return Array.from(byDate.entries())
    .map(([date, v]) => ({
      date,
      callOi: v.callOi,
      putOi: v.putOi,
      pcRatio: v.callOi > 0 ? v.putOi / v.callOi : NaN,
    }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

export function buildExpiryMarkers(rows: OptionsRow[]): ExpiryMarker[] {
  const contractRows = new Map<string, Map<string, OptionsRow>>();
  const contractExpiry = new Map<string, string>();
  const contractName = new Map<string, string>();

  for (const r of rows) {
    if (r.expiry_date) {
      contractExpiry.set(r.contract_code, r.expiry_date);
    }
    if (r.contract_name) {
      contractName.set(r.contract_code, r.contract_name);
    }
    if (!contractRows.has(r.contract_code)) {
      contractRows.set(r.contract_code, new Map());
    }
    contractRows.get(r.contract_code)!.set(r.date, r);
  }

  const byExpiry = new Map<string, { contracts: Map<string, ExpiryContract>; tradingDate: string }>();
  for (const [code, expiryDate] of contractExpiry) {
    const rowByDate = contractRows.get(code);
    if (!rowByDate) continue;

    let lastDate = "";
    for (const d of rowByDate.keys()) {
      if (d <= expiryDate && d > lastDate) lastDate = d;
    }
    if (!lastDate) continue;

    const row = rowByDate.get(lastDate);
    if (!row) continue;

    if (!byExpiry.has(expiryDate)) {
      byExpiry.set(expiryDate, { contracts: new Map(), tradingDate: lastDate });
    }
    const entry = byExpiry.get(expiryDate)!;

    if (!entry.contracts.has(code)) {
      entry.contracts.set(code, {
        code,
        name: contractName.get(code) || row.contract_name || code,
        optionType: row.option_type,
        prevDayOi: row.open_interest,
      });
    }
  }

  const markers: ExpiryMarker[] = [];
  for (const [expiryDate, entry] of byExpiry) {
    const contracts = Array.from(entry.contracts.values());
    const total = contracts.reduce((s, c) => s + c.prevDayOi, 0);
    markers.push({
      expiryDate,
      tradingDate: entry.tradingDate,
      contracts,
      totalPrevDayOi: total,
    });
  }

  return markers.sort((a, b) => a.tradingDate.localeCompare(b.tradingDate));
}