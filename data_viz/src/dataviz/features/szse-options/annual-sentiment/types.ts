/**
 * Shared type definitions for annual-sentiment charts.
 */
export interface ExpiryContract {
  code: string;
  name: string;
  optionType: "CALL" | "PUT";
  prevDayOi: number;
}

export interface ExpiryMarker {
  expiryDate: string;
  tradingDate: string;
  contracts: ExpiryContract[];
  totalPrevDayOi: number;
  yValueRatio?: number;
  yValueOi?: number;
}

export interface ExpiryMarkerDataItem {
  value: [number, number];
  marker: ExpiryMarker;
}

export interface DailyOi {
  date: string;
  callOi: number;
  putOi: number;
  pcRatio: number;
}