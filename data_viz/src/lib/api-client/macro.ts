import { fetchJson } from "./_cache";
import type { MacroDailyResponse } from "@shared/types";

export function fetchMacroDaily(
  startDate?: string | null,
  endDate?: string | null,
): Promise<MacroDailyResponse> {
  const params = new URLSearchParams();
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  const qs = params.toString();
  return fetchJson<MacroDailyResponse>(`/api/macro${qs ? `?${qs}` : ""}`);
}
